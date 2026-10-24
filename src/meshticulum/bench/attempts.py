"""Durable, finite bench radio attempts. No payloads or keys are stored.

Reservation precedes any serial write. All reserved airtime remains charged,
including refusal and unknown outcomes. A recovered unresolved attempt fences
this run; an operator must provision a fresh session before further RF work.
This is crash persistence, not protection against privileged disk rollback.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time


class AttemptError(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise AttemptError(reason)


class AttemptJournal:
    def __init__(self, path, *, binding, max_attempts, max_airtime_us, create=False,
                 request_clock=None):
        require(type(binding) is bytes and len(binding) == 32, 'invalid_binding')
        require(type(max_attempts) is int and 1 <= max_attempts <= 100000
                and type(max_airtime_us) is int and 1 <= max_airtime_us <= 300000000,
                'invalid_attempt_limits')
        self.binding = binding
        self.max_attempts, self.max_airtime_us = max_attempts, max_airtime_us
        self.clock = request_clock or (lambda: time.time_ns() // 1000)
        self._lock = threading.RLock()
        path = Path(path).absolute()
        parent = path.parent.lstat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid()
                and parent.st_mode & 0o077 == 0, 'state_directory_not_private')
        if create:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0, 'unsafe_state_file')
        # Separate inode: BSD flock shares locking with SQLite's POSIX locks
        # on macOS. Locking the database itself would block SQLite locally.
        self.lock_fd = os.open(str(path) + '.owner',
                               os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        self.db = None
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = os.fstat(self.lock_fd)
            require(stat.S_ISREG(held.st_mode) and held.st_nlink == 1
                    and held.st_uid == os.geteuid() and held.st_mode & 0o077 == 0,
                    'unsafe_journal_owner_file')
            current = path.lstat()
            require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino), 'state_file_changed')
            self.db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                                      isolation_level=None, timeout=2, check_same_thread=False)
            # One owner/connection writes this finite journal. WAL avoids
            # rewriting/deleting a rollback journal for every packet while
            # FULL still syncs each reservation before transport is invoked.
            require(self.db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',),
                    'durable_wal_unavailable')
            self.db.execute('PRAGMA synchronous=FULL')
            require(self.db.execute('PRAGMA synchronous').fetchone() == (2,),
                    'durable_sync_unavailable')
        except BaseException:
            if self.db is not None:
                self.db.close()
            os.close(self.lock_fd)
            self.lock_fd = None
            raise
        try:
            if create:
                with self.transaction():
                    self.db.execute('CREATE TABLE owner (id INTEGER PRIMARY KEY CHECK(id=1), '
                        'binding BLOB, max_attempts INTEGER, max_airtime INTEGER, '
                        'request INTEGER, fenced INTEGER)')
                    self.db.execute('INSERT INTO owner VALUES (1,?,?,?,0,0)',
                                    (binding, max_attempts, max_airtime_us))
                    self.db.execute('CREATE TABLE attempts (request INTEGER PRIMARY KEY, '
                        'packet_sha BLOB NOT NULL, bytes INTEGER NOT NULL, estimated_us INTEGER NOT NULL, '
                        'state TEXT NOT NULL, evidence TEXT)')
                fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            with self.transaction():
                # Optional physical queue records share the same FULL commit
                # as debt. They contain immutable metadata, never ciphertext.
                self.db.execute('CREATE TABLE IF NOT EXISTS queued_attempts ('
                    'request INTEGER PRIMARY KEY, metadata BLOB NOT NULL, '
                    'terminal_event BLOB, released INTEGER NOT NULL DEFAULT 0)')
                self._owner(allow_fenced=True)
                pending = self.db.execute("SELECT count(*) FROM attempts WHERE state='RESERVED'").fetchone()[0]
                if pending:
                    self.db.execute("UPDATE attempts SET state='UNKNOWN',evidence='process_recovery' WHERE state='RESERVED'")
                    self.db.execute('UPDATE owner SET fenced=1 WHERE id=1')
                self.recovered_unknown = pending
        except BaseException:
            self.db.close()
            os.close(self.lock_fd)
            self.lock_fd = None
            raise

    @contextmanager
    def transaction(self):
        with self._lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def _owner(self, allow_fenced=False):
        row = self.db.execute('SELECT binding,max_attempts,max_airtime,request,fenced FROM owner WHERE id=1').fetchone()
        require(row is not None and row[:3] == (self.binding, self.max_attempts, self.max_airtime_us),
                'attempt_state_binding_mismatch')
        require(type(row[3]) is int and 0 <= row[3] < 2**63 - 1 and row[4] in (0, 1),
                'invalid_attempt_state')
        require(allow_fenced or row[4] == 0, 'attempt_run_fenced')
        return row

    def _next(self, row):
        stamp = self.clock()
        require(type(stamp) is int and 0 < stamp < 2**63 - 1, 'invalid_request_clock')
        request = max(stamp, row[3] + 1)
        self.db.execute('UPDATE owner SET request=? WHERE id=1', (request,))
        return request

    def command_request(self, *, containment=False):
        with self.transaction():
            return self._next(self._owner(allow_fenced=containment))

    def reserve(self, packet, estimated_us, *, queue_metadata=None):
        require(type(packet) is bytes and 1 <= len(packet) <= 255, 'invalid_radio_packet')
        require(type(estimated_us) is int and 0 < estimated_us <= self.max_airtime_us,
                'invalid_airtime_reservation')
        require(queue_metadata is None or type(queue_metadata) is bytes
                and 1 <= len(queue_metadata) <= 1024, 'invalid_queue_metadata')
        with self.transaction():
            owner = self._owner()
            count, reserved = self.db.execute('SELECT count(*),coalesce(sum(estimated_us),0) FROM attempts').fetchone()
            require(count < self.max_attempts and reserved + estimated_us <= self.max_airtime_us,
                    'attempt_budget_exhausted')
            request = self._next(owner)
            self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,NULL)',
                            (request, hashlib.sha256(packet).digest(), len(packet), estimated_us, 'RESERVED'))
            if queue_metadata is not None:
                self.db.execute('INSERT INTO queued_attempts(request,metadata) VALUES (?,?)',
                                (request, queue_metadata))
        return request

    def settle_queued(self, request, packet, outcome, evidence, terminal_event):
        """Commit physical evidence before releasing its retained MCU slot.

        Exact terminal replay is idempotent. A host UNKNOWN is never upgraded;
        later exact native evidence may be retained solely for physical release.
        """
        require(outcome in ('RF_DONE', 'REFUSED', 'NOT_SENT', 'UNKNOWN'), 'invalid_attempt_outcome')
        require(type(evidence) is str and len(evidence) <= 512, 'invalid_attempt_evidence')
        require(terminal_event is None and outcome in ('UNKNOWN', 'NOT_SENT', 'REFUSED')
                or type(terminal_event) is bytes and 1 <= len(terminal_event) <= 1024,
                'invalid_queue_terminal_event')
        with self.transaction():
            self._owner(allow_fenced=True)
            row = self.db.execute('SELECT a.packet_sha,a.bytes,a.state,q.terminal_event '
                'FROM attempts a JOIN queued_attempts q USING(request) WHERE request=?',
                (request,)).fetchone()
            require(row is not None and row[:2] == (hashlib.sha256(packet).digest(), len(packet)),
                    'wrong_attempt_settlement')
            if row[2] != 'RESERVED':
                require(row[2] == outcome and (row[3] == terminal_event
                        or row[2] == 'UNKNOWN' and row[3] is None), 'attempt_already_settled')
                if row[3] == terminal_event:
                    return False
            self.db.execute('UPDATE attempts SET state=?,evidence=? WHERE request=?',
                            (outcome, evidence, request))
            self.db.execute('UPDATE queued_attempts SET terminal_event=? WHERE request=?',
                            (terminal_event, request))
            if terminal_event is None and outcome in ('NOT_SENT', 'REFUSED'):
                self.db.execute('UPDATE queued_attempts SET released=1 WHERE request=?', (request,))
            if outcome == 'UNKNOWN':
                self.db.execute('UPDATE owner SET fenced=1 WHERE id=1')
        return True

    def release_queued(self, request, terminal_event):
        """Mark an exact, confirmed MCU release; retain immutable history/debt."""
        require(type(terminal_event) is bytes and 1 <= len(terminal_event) <= 1024,
                'invalid_queue_terminal_event')
        with self.transaction():
            self._owner(allow_fenced=True)
            row = self.db.execute('SELECT a.state,q.terminal_event,q.released FROM attempts a '
                'JOIN queued_attempts q USING(request) WHERE request=?', (request,)).fetchone()
            require(row is not None and row[0] != 'RESERVED' and row[1] == terminal_event,
                    'wrong_queue_release')
            self.db.execute('UPDATE queued_attempts SET released=1 WHERE request=?', (request,))
            return not bool(row[2])

    def reconcile_queued_unknown(self, request, *, packet_sha, size, terminal_event, evidence):
        """Attach exact cleanup evidence after recovery without payload or RF.

        Only a previously UNKNOWN record qualifies. Its charged outcome and
        fence remain UNKNOWN; the exact owner must validate native provenance
        and immutable queue metadata before using this cleanup-only operation.
        """
        require(type(packet_sha) is bytes and len(packet_sha) == 32
                and type(size) is int and 1 <= size <= 255, 'invalid_queue_packet_identity')
        require(type(terminal_event) is bytes and 1 <= len(terminal_event) <= 1024,
                'invalid_queue_terminal_event')
        require(type(evidence) is str and len(evidence) <= 512, 'invalid_attempt_evidence')
        with self.transaction():
            owner = self._owner(allow_fenced=True)
            row = self.db.execute('SELECT a.packet_sha,a.bytes,a.state,q.terminal_event '
                'FROM attempts a JOIN queued_attempts q USING(request) WHERE request=?',
                (request,)).fetchone()
            require(owner[4] == 1 and row is not None
                    and row[:3] == (packet_sha, size, 'UNKNOWN')
                    and row[3] in (None, terminal_event), 'wrong_unknown_reconciliation')
            if row[3] == terminal_event:
                return False
            self.db.execute('UPDATE queued_attempts SET terminal_event=? WHERE request=?',
                            (terminal_event, request))
            self.db.execute('UPDATE attempts SET evidence=? WHERE request=?', (evidence, request))
        return True

    def queued_records(self):
        """Read durable physical state, including UNKNOWN left by recovery."""
        with self._lock:
            self._owner(allow_fenced=True)
            return [dict(request=row[0], packet_sha=row[1], bytes=row[2],
                         reserved_estimated_us=row[3], state=row[4], metadata=row[5],
                         terminal_event=row[6], released=bool(row[7])) for row in
                    self.db.execute('SELECT a.request,a.packet_sha,a.bytes,a.estimated_us,a.state,'
                        'q.metadata,q.terminal_event,q.released FROM attempts a '
                        'JOIN queued_attempts q USING(request) ORDER BY a.request')]

    def fence(self):
        with self.transaction():
            self._owner(allow_fenced=True)
            self.db.execute('UPDATE owner SET fenced=1 WHERE id=1')

    def settle(self, request, packet, outcome, evidence):
        require(outcome in ('RF_DONE', 'REFUSED', 'NOT_SENT', 'UNKNOWN'), 'invalid_attempt_outcome')
        require(type(evidence) is str and len(evidence) <= 512, 'invalid_attempt_evidence')
        with self.transaction():
            row = self.db.execute('SELECT packet_sha,bytes,state FROM attempts WHERE request=?', (request,)).fetchone()
            require(row is not None and row[:2] == (hashlib.sha256(packet).digest(), len(packet)),
                    'wrong_attempt_settlement')
            require(row[2] == 'RESERVED', 'attempt_already_settled')
            self.db.execute('UPDATE attempts SET state=?,evidence=? WHERE request=?',
                            (outcome, evidence, request))
            if outcome == 'UNKNOWN':
                self.db.execute('UPDATE owner SET fenced=1 WHERE id=1')

    def snapshot(self):
        with self._lock:
            row = self._owner(allow_fenced=True)
            count, reserved = self.db.execute('SELECT count(*),coalesce(sum(estimated_us),0) FROM attempts').fetchone()
            return {'attempts': count, 'reserved_estimated_airtime_us': reserved,
                    'fenced': bool(row[4]), 'recovered_unknown': self.recovered_unknown}

    def close(self):
        with self._lock:
            self.db.close()
            if self.lock_fd is not None:
                os.close(self.lock_fd)
                self.lock_fd = None
