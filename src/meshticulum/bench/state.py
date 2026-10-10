"""Durable nonce/replay state for one explicitly provisioned bench session.

Only metadata is stored: never session secrets or application payloads. Creation
is a distinct enrollment operation and refuses an existing file. Opening cannot
initialize missing state. The caller supplies real boot identity and clocks;
production composition must obtain those from the host, not a client request.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import threading
import uuid

from .wire import BenchGrant, WireError, require

REPLAY_BITS = 256
REPLAY_BYTES = REPLAY_BITS // 8
MASK = (1 << REPLAY_BITS) - 1


class SessionStore:
    def __init__(self, path, grant, local_peer, *, boot_id, clock, create=False):
        require(type(grant) is BenchGrant, 'invalid_grant')
        require(type(boot_id) is str and str(uuid.UUID(boot_id)) == boot_id, 'invalid_boot_id')
        require(callable(clock), 'invalid_clock')
        self.grant, self.direction = grant, grant.direction(local_peer)
        self.binding = (grant.digest, self.direction)
        self.boot_id, self.clock = boot_id, clock
        self._lock = threading.RLock()
        path = Path(path).absolute()
        parent = path.parent.stat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid()
                and parent.st_mode & 0o077 == 0, 'state_directory_not_private')
        if create:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                'unsafe_state_file')
        # mode=rw forbids SQLite's implicit creation on the normal reopen path.
        self._db = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True,
                                   isolation_level=None, timeout=2, check_same_thread=False)
        # Preserve a durable commit per nonce/replay update; use sequential
        # WAL writes instead of creating/deleting a journal for every frame.
        require(self._db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',),
                'durable_wal_unavailable')
        self._db.execute('PRAGMA synchronous=FULL')
        require(self._db.execute('PRAGMA synchronous').fetchone() == (2,),
                'durable_sync_unavailable')
        try:
            if create:
                epoch, mono = self._time()
                require(grant.issued_epoch_us <= epoch < grant.expires_epoch_us, 'session_not_current')
                deadline = mono + grant.expires_epoch_us - epoch
                require(deadline < 2**63, 'invalid_monotonic_deadline')
                with self._transaction():
                    self._db.execute('CREATE TABLE state (only_row INTEGER PRIMARY KEY CHECK(only_row=1), '
                        'binding BLOB NOT NULL, direction INTEGER NOT NULL, boot TEXT NOT NULL, '
                        'deadline INTEGER NOT NULL, epoch INTEGER NOT NULL, mono INTEGER NOT NULL, '
                        'tx INTEGER NOT NULL, high INTEGER NOT NULL, bitmap BLOB NOT NULL, retired INTEGER NOT NULL)')
                    self._db.execute('INSERT INTO state VALUES (1,?,?,?,?,?,?,0,0,?,0)',
                        (grant.digest, self.direction, boot_id, deadline, epoch, mono, bytes(REPLAY_BYTES)))
                descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            self.check_live(authorize=lambda: True)
        except BaseException:
            self._db.close()
            raise

    def _time(self):
        pair = self.clock()
        require(type(pair) is tuple and len(pair) == 2
                and all(type(n) is int and 0 <= n < 2**63 for n in pair), 'invalid_clock')
        return pair

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def _live(self, authorize):
        require(callable(authorize) and authorize() is True, 'authority_refused')
        row = self._db.execute('SELECT binding,direction,boot,deadline,epoch,mono,tx,high,bitmap,retired FROM state WHERE only_row=1').fetchone()
        require(row is not None and row[:2] == self.binding, 'state_binding_mismatch')
        require(all(type(row[i]) is int for i in (1, 3, 4, 5, 6, 7, 9)),
                'invalid_persisted_state')
        require(row[2] == self.boot_id, 'boot_changed_requires_new_session')
        require(row[9] == 0, 'session_retired')
        epoch, mono = self._time()
        require(epoch >= row[4] and mono >= row[5], 'clock_moved_backwards')
        require(self.grant.issued_epoch_us <= epoch < self.grant.expires_epoch_us
                and mono < row[3], 'session_expired')
        require(0 <= row[6] <= self.grant.max_frames and 0 <= row[7] <= self.grant.max_frames
                and type(row[8]) is bytes and len(row[8]) == REPLAY_BYTES, 'invalid_persisted_state')
        return row, epoch, mono

    def check_live(self, *, authorize):
        with self._transaction():
            self._live(authorize)

    def reserve_transmit(self, *, authorize):
        with self._transaction():
            row, epoch, mono = self._live(authorize)
            require(row[6] < self.grant.max_frames, 'session_frame_limit')
            counter = row[6] + 1
            self._db.execute('UPDATE state SET tx=?,epoch=?,mono=? WHERE only_row=1', (counter, epoch, mono))
        return counter

    def record_receive(self, counter, *, authorize):
        require(type(counter) is int and 1 <= counter <= self.grant.max_frames, 'invalid_counter')
        with self._transaction():
            row, epoch, mono = self._live(authorize)
            high, bitmap = row[7], int.from_bytes(row[8], 'big')
            if counter > high:
                distance = counter - high
                bitmap = 1 if distance >= REPLAY_BITS else ((bitmap << distance) | 1) & MASK
                high = counter
            else:
                distance = high - counter
                require(distance < REPLAY_BITS and not bitmap & (1 << distance), 'replayed_or_too_old')
                bitmap |= 1 << distance
            self._db.execute('UPDATE state SET high=?,bitmap=?,epoch=?,mono=? WHERE only_row=1',
                             (high, bitmap.to_bytes(REPLAY_BYTES, 'big'), epoch, mono))

    def retire(self):
        with self._transaction():
            self._db.execute('UPDATE state SET retired=1 WHERE only_row=1')

    def close(self):
        with self._lock:
            self._db.close()
