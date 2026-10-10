"""Durable TCP stream-byte reservations, never radio-airtime estimates.

Every DATA/ACK/retry reservation remains charged for this grant's lifetime.
Kernel acceptance does not prove peer receipt. Unresolved process recovery is
UNKNOWN and fences new dispatch. Payload and keys are never journaled.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import threading

from .wire import IpGrant,require,integer


def private(info,directory=False):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
      and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==(0o700 if directory else 0o600)
      and (directory or info.st_nlink==1),'unsafe_ip_state_path')


class IpJournal:
    def __init__(self,path,grant,*,create=False):
        require(type(grant) is IpGrant,'invalid_ip_journal_grant')
        self.grant,self.binding,self.lock_fd,self.db=grant,grant.digest,None,None
        self._lock=threading.RLock();self.closed=True
        self._in_transaction=False;self._ownership_failed=False;self.lock_identity=None
        self.path=Path(path).absolute();private(self.path.parent.lstat(),True)
        if create:
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            os.fsync(fd);os.close(fd)
        info=self.path.lstat();private(info)
        try:
            self.lock_fd=os.open(str(self.path)+'.owner',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            held=os.fstat(self.lock_fd)
            self.lock_identity=(held.st_dev,held.st_ino)
            private(held);fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.identity=(info.st_dev,info.st_ino)
            require((self.path.lstat().st_dev,self.path.lstat().st_ino)==self.identity,'ip_journal_replaced')
            self.db=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,isolation_level=None,timeout=2,check_same_thread=False)
            require(self.db.execute('PRAGMA journal_mode=WAL').fetchone()==('wal',),'ip_wal_unavailable')
            self.db.execute('PRAGMA synchronous=FULL')
            require(self.db.execute('PRAGMA synchronous').fetchone()==(2,),'ip_sync_unavailable')
            if create:
                with self.transaction():
                    self.db.execute('CREATE TABLE owner (id INTEGER PRIMARY KEY CHECK(id=1),binding BLOB NOT NULL,rx INTEGER NOT NULL,rx_pending INTEGER NOT NULL,rx_unknown INTEGER NOT NULL,fenced INTEGER NOT NULL)')
                    self.db.execute('INSERT INTO owner VALUES(1,?,0,0,0,0)',(self.binding,))
                    self.db.execute('CREATE TABLE attempts (id INTEGER PRIMARY KEY,digest BLOB NOT NULL,bytes INTEGER NOT NULL,lane TEXT NOT NULL,written INTEGER NOT NULL,state TEXT NOT NULL)')
                fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
            self.closed=False
            with self.transaction():
                self._owner(True)
                pending=self.db.execute('SELECT rx_pending FROM owner WHERE id=1').fetchone()[0]
                if pending:
                    self.db.execute('UPDATE owner SET rx_unknown=rx_unknown+rx_pending,rx_pending=0,fenced=1 WHERE id=1')
                self.recovered_unknown=self.db.execute("SELECT count(*) FROM attempts WHERE state='RESERVED'").fetchone()[0]
                if self.recovered_unknown:
                    self.db.execute("UPDATE attempts SET state='UNKNOWN' WHERE state='RESERVED'")
                    self.db.execute('UPDATE owner SET fenced=1 WHERE id=1')
        except BaseException:
            self.close();raise

    @contextmanager
    def transaction(self):
        with self._lock:
            require(not self._in_transaction and self.db is not None, 'ip_journal_not_available')
            self._check_owner_files()
            self.db.execute('BEGIN IMMEDIATE')
            self._in_transaction=True
            try:
                yield;self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK');raise
            finally:
                self._in_transaction=False

    def _owner(self,allow_fenced=False):
        row=self.db.execute('SELECT binding,rx,fenced FROM owner WHERE id=1').fetchone()
        require(row is not None and row[0]==self.binding and type(row[1]) is int and 0<=row[1]<=self.grant.limits.max_rx_stream_bytes
          and row[2] in (0,1),'ip_journal_binding_invalid')
        reserved=self.db.execute('SELECT rx_pending,rx_unknown FROM owner WHERE id=1').fetchone()
        require(all(type(x) is int and x>=0 for x in reserved) and row[1]+sum(reserved)<=self.grant.limits.max_rx_stream_bytes,'invalid_ip_receive_state')
        require(allow_fenced or row[2]==0,'ip_journal_fenced')
        return row

    def _check_owner_files(self):
        require(not self._ownership_failed, 'ip_journal_ownership_lost')
        try:
            info=self.path.lstat();lock=Path(str(self.path)+'.owner').lstat()
            held=os.fstat(self.lock_fd)
            private(info);private(lock);private(held)
            require((info.st_dev,info.st_ino)==self.identity
              and (lock.st_dev,lock.st_ino)==self.lock_identity
              and (held.st_dev,held.st_ino)==self.lock_identity,'ip_journal_replaced_or_owner_closed')
        except (OSError,TypeError,ValueError):
            # Never repair this instance by reacquiring or replacing a handle.
            self._ownership_failed=True
            raise

    def owned(self):
        with self._lock:
            if self.closed or self.db is None:return False
            try:
                self._check_owner_files();self._owner();return True
            except (OSError,TypeError,ValueError,sqlite3.Error):return False

    def reserve(self,packet,lane):
        require(type(packet) is bytes and 1<=len(packet)<=4100 and lane in ('DATA','ACK'),'invalid_ip_attempt')
        with self.transaction():
            self._owner()
            count,total=self.db.execute('SELECT count(*),coalesce(sum(bytes),0) FROM attempts').fetchone()
            require(count<self.grant.max_frames and total+len(packet)<=self.grant.limits.max_tx_stream_bytes,'ip_tx_budget_exhausted')
            cur=self.db.execute("INSERT INTO attempts(digest,bytes,lane,written,state) VALUES(?,?,?,0,'RESERVED')",
              (hashlib.sha256(packet).digest(),len(packet),lane))
            return cur.lastrowid

    def progress(self,token,packet,written):
        with self.transaction():
            row=self.db.execute('SELECT digest,bytes,written,state FROM attempts WHERE id=?',(token,)).fetchone()
            require(row is not None and row[0]==hashlib.sha256(packet).digest() and row[1]==len(packet) and row[3]=='RESERVED','wrong_ip_attempt')
            integer(written,row[2],len(packet),'invalid_ip_write_progress')
            self.db.execute('UPDATE attempts SET written=? WHERE id=?',(written,token))

    def settle(self,token,packet,state):
        require(state in ('KERNEL_ACCEPTED','NOT_SENT','UNKNOWN'),'invalid_ip_outcome')
        with self.transaction():
            row=self.db.execute('SELECT digest,bytes,written,state FROM attempts WHERE id=?',(token,)).fetchone()
            require(row is not None and row[:2]==(hashlib.sha256(packet).digest(),len(packet)) and row[3]=='RESERVED','wrong_ip_settlement')
            require(state!='KERNEL_ACCEPTED' or row[2]==row[1],'incomplete_ip_write')
            require(state!='NOT_SENT' or row[2]==0,'written_ip_packet_not_sent')
            self.db.execute('UPDATE attempts SET state=? WHERE id=?',(state,token))
            if state=='UNKNOWN':self.db.execute('UPDATE owner SET fenced=1 WHERE id=1')

    def reserve_receive(self,maximum):
        integer(maximum,1,4100,'invalid_ip_receive_reservation')
        with self.transaction():
            row=self._owner()
            pending,unknown=self.db.execute('SELECT rx_pending,rx_unknown FROM owner WHERE id=1').fetchone()
            require(pending==0,'ip_receive_already_pending')
            maximum=min(maximum,self.grant.limits.max_rx_stream_bytes-row[1]-unknown)
            require(maximum>0,'ip_rx_budget_exhausted')
            self.db.execute('UPDATE owner SET rx_pending=? WHERE id=1',(maximum,))
            return maximum

    def settle_receive(self,size):
        with self.transaction():
            pending=self.db.execute('SELECT rx_pending FROM owner WHERE id=1').fetchone()[0]
            require(pending>0,'ip_receive_not_pending')
            integer(size,0,pending,'invalid_ip_receive_settlement')
            self.db.execute('UPDATE owner SET rx=rx+?,rx_pending=0 WHERE id=1',(size,))

    def fence(self):
        with self.transaction():self.db.execute('UPDATE owner SET fenced=1 WHERE id=1')

    def snapshot(self):
        with self.transaction():
            row=self._owner(True)
            count,reserved,written=self.db.execute('SELECT count(*),coalesce(sum(bytes),0),coalesce(sum(written),0) FROM attempts').fetchone()
            pending,unknown=self.db.execute('SELECT rx_pending,rx_unknown FROM owner WHERE id=1').fetchone()
            lanes=dict(self.db.execute('SELECT lane,count(*) FROM attempts GROUP BY lane'))
            outcomes=dict(self.db.execute('SELECT state,count(*) FROM attempts GROUP BY state'))
            return dict(transport='tcp',basis='local TCP stream bytes; excludes IP/TCP headers and kernel retransmissions',
              attempts=count,reserved_stream_bytes=reserved,known_kernel_accepted_stream_bytes=written,
              received_stream_bytes=row[1],pending_receive_stream_bytes=pending,unknown_receive_stream_bytes=unknown,lanes=lanes,outcomes=outcomes,fenced=bool(row[2]),
              measured_airtime_us=None,kernel_retransmission_bytes=None,peer_receipt_established=False)

    def close(self):
        with self._lock:
            require(not self._in_transaction, 'cannot_close_active_ip_transaction')
            self.closed=True
            if self.db is not None:self.db.close();self.db=None
            if self.lock_fd is not None:
                # A native-close bug may have freed and reused this number.
                # Dispose only the descriptor this journal originally owned.
                try:
                    held=os.fstat(self.lock_fd)
                    if (held.st_dev,held.st_ino)==self.lock_identity:os.close(self.lock_fd)
                except OSError:
                    pass
                self.lock_fd=None
