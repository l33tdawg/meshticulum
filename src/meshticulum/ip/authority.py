"""Single-use local TCP admission with held revocation and original deadlines.

Host runtime supplies boot identity and clocks independently of peer input.
No listener, discovery, connection, consent enrollment or grant creation occurs.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import threading
import uuid

from ..contracts.identity import DeadlineSet,PeerID,SessionKey
from .wire import IpGrant,IpLimits,require
from .journal import private


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def grant_from_plan(p, secret, *, local_peer, host_boot_id):
    """Pure native-plan validation; grants no operation or single-use claim."""
    require(type(p) is dict and set(p)=={'schema','host_boot_id','local_peer','session','format','object','route_binding_digest',
      'issued_epoch_us','expires_epoch_us','application_expires_epoch_us','application_deadline_monotonic_us','initiator_address','responder_address','limits'},'invalid_ip_plan')
    require(p['schema']=='meshticulum.ip-service-run.v1' and p['host_boot_id']==host_boot_id and PeerID(**p['local_peer'])==local_peer,'wrong_ip_host')
    s=p['session'];require(type(s) is dict and set(s)=={'initiator','responder','session_id','generation'},'invalid_ip_session_plan')
    session=SessionKey(PeerID(**s['initiator']),PeerID(**s['responder']),bytes.fromhex(s['session_id']),bytes.fromhex(s['generation']))
    require(type(p['object']) is dict and set(p['object'])=={'bytes','sha256'},'invalid_ip_object_plan')
    grant=IpGrant(session,p['format'],p['object']['bytes'],bytes.fromhex(p['object']['sha256']),bytes.fromhex(p['route_binding_digest']),
      p['issued_epoch_us'],p['expires_epoch_us'],p['application_expires_epoch_us'],tuple(p['initiator_address']),tuple(p['responder_address']),IpLimits(**p['limits']),secret)
    grant.direction(local_peer)
    return grant


class IpAuthority:
    def __init__(self,directory,*,local_peer,host_boot_id,clock,owner_probe):
        self.path=Path(directory).absolute();private(self.path.lstat(),True)
        self.clock,self.owner_probe=clock,owner_probe
        self.closed=True;self.held=False;self.held_thread=None;self.fds={};self._lock=threading.RLock()
        require(type(local_peer) is PeerID and callable(clock) and callable(owner_probe),'invalid_ip_authority')
        require(type(host_boot_id) is str and str(uuid.UUID(host_boot_id))==host_boot_id,'invalid_ip_boot')
        try:
            for name in ('grant.json','secret.bin','active'):
                fd=os.open(self.path/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC);self.fds[name]=fd;private(os.fstat(fd))
            self.file_ids={name:(os.fstat(fd).st_dev,os.fstat(fd).st_ino)for name,fd in self.fds.items()}
            raw=os.pread(self.fds['grant.json'],16385,0);require(0<len(raw)<=16384,'invalid_ip_plan_size')
            self.plan=json.loads(raw);self.plan_bytes=raw;p=self.plan
            secret=os.pread(self.fds['secret.bin'],33,0)
            self.grant=grant_from_plan(p,secret,local_peer=local_peer,host_boot_id=host_boot_id)
            self.local_peer=local_peer;self.sends=self.grant.direction(local_peer)==0
            self.service_format,self.service_kind=self.grant.format,self.grant.service_kind
            self.application_expires_epoch_us=self.grant.application_expires_epoch_us
            epoch,mono=self._time();require(self.grant.issued_epoch_us<=epoch<self.application_expires_epoch_us,'ip_grant_not_current')
            require(type(p['application_deadline_monotonic_us']) is int and mono<p['application_deadline_monotonic_us']<2**63, 'invalid_ip_original_deadline')
            self.application_deadline_us=min(p['application_deadline_monotonic_us'],mono+self.application_expires_epoch_us-epoch)
            self.deadline_us=mono+self.grant.expires_epoch_us-epoch;self.last_clock=(epoch,mono)
            self.deadlines=DeadlineSet(self.application_deadline_us/1e6,self.deadline_us/1e6,self.grant.expires_epoch_us/1e6)
            fd=os.open(self.path/'started',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
            try:os.write(fd,self.grant.digest.hex().encode()+b'\n');os.fsync(fd)
            finally:os.close(fd)
            fd=os.open(self.path,os.O_RDONLY|os.O_DIRECTORY);os.fsync(fd);os.close(fd)
            self.closed=False;require(self.live(),'ip_grant_not_live')
        except BaseException:self.close();raise

    def _time(self):
        value=self.clock();require(type(value) is tuple and len(value)==2 and all(type(n) is int and 0<=n<2**63 for n in value),'invalid_ip_clock')
        return value

    def live(self):
        if self.closed:return False
        try:
            epoch,mono=self._time();require(epoch>=self.last_clock[0] and mono>=self.last_clock[1],'ip_clock_regressed')
            self.last_clock=(epoch,mono);self.deadlines.check(mono/1e6,epoch/1e6)
            require(epoch<self.application_expires_epoch_us and self.owner_probe() is True,'ip_owner_or_application_not_current')
            private(self.path.lstat(),True)
            for name,fd in self.fds.items():
                info=(self.path/name).lstat();private(info)
                require((info.st_dev,info.st_ino)==self.file_ids[name],'ip_authority_file_replaced')
            require(os.pread(self.fds['grant.json'],16385,0)==self.plan_bytes
              and os.pread(self.fds['secret.bin'],33,0)==self.grant.secret
              and os.pread(self.fds['active'],16,0)==b'active\n','ip_authority_retired_or_changed')
            return True
        except (OSError,ValueError):return False

    application_live=live

    @contextmanager
    def application_lease(self):
        with self._lock:
            require(not self.held,'nested_ip_authority_lease')
            fcntl.flock(self.fds['active'],fcntl.LOCK_SH|fcntl.LOCK_NB)
            self.held=True;self.held_thread=threading.get_ident()
            try:
                require(self.live(),'ip_authority_not_current');yield self
                require(self.live(),'ip_authority_not_current')
            finally:
                self.held=False;self.held_thread=None;fcntl.flock(self.fds['active'],fcntl.LOCK_UN)

    def require_dispatch(self):require(self.held and self.held_thread==threading.get_ident() and self.live(),'ip_dispatch_lease_not_held')

    def close(self):
        # Close joins another thread's lease. Same-thread close is refused so
        # the context manager retains its original revocation descriptor.
        with self._lock:
            require(not self.held, 'cannot_close_held_ip_authority')
            self.closed=True
            for fd in self.fds.values():os.close(fd)
            self.fds.clear()


def revoke(directory):
    path=Path(directory);private(path.lstat(),True)
    fd=os.open(path/'active',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        private(os.fstat(fd));fcntl.flock(fd,fcntl.LOCK_EX)
        os.ftruncate(fd,0);os.write(fd,b'retired\n');os.fsync(fd)
    finally:os.close(fd)
