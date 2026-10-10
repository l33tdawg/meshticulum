# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Private, single-use bench grants and held dispatch leases.

The Linux launcher requires root; grants are installed over pinned management,
never enrolled over radio. The provisioned peer IDs belong to the bench scope,
not to a personal SAGE identity. No production service permission is inferred.
Revocation takes the active-file exclusive lock; dispatch holds its shared lock
through the bounded physical attempt. Removal, mutation, expiry or a new host
boot refuses new work. Restart requires a new grant and fresh wire state.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from ..contracts.identity import (DeadlineSet, FlowKey, PeerID,
                                             RouteBinding, ServiceID, ServiceKind, SessionKey)
from ..contracts.carrier import CarrierKind, CarrierProfile, ProfileBasis
from ..contracts.negotiation import (CapabilityLimits, Feature,
                                                NegotiatedContract, TrafficClass)
from ..scheduler.scheduler import DispatchGrant, LeaseScope
from .serial_protocol import RadioProfile
from .serial_worker import estimated_airtime_us
from .profiles import MODES
from .wire import BenchGrant, require


SERVICE_FORMATS = {
    'MR1.request': ServiceKind.KNOWLEDGE,
    'MR1.response': ServiceKind.KNOWLEDGE,
    'LME.binary': ServiceKind.MESSAGE,
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def _private_info(info, *, directory=False):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600)
            and (directory or info.st_nlink == 1), 'unsafe_bench_authority_path')


def _open(path, flags=os.O_RDONLY):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        _private_info(os.fstat(fd))
    except BaseException:
        os.close(fd)
        raise
    return fd


class RunAuthority:
    def __init__(self, directory, *, expected_unit, host_boot_id, clock, owner_probe):
        self.path = Path(directory).absolute()
        _private_info(self.path.lstat(), directory=True)
        self.fds, self.held = {}, {}
        self.clock, self.owner_probe = clock, owner_probe
        self.closed = True
        try:
            for name in ('grant.json', 'secret.bin', 'active'):
                self.fds[name] = _open(self.path / name)
            raw = os.pread(self.fds['grant.json'], 16385, 0)
            require(0 < len(raw) <= 16384, 'invalid_grant_size')
            self.plan = json.loads(raw)
            self.plan_bytes = raw
            self.file_ids = {n: (os.fstat(fd).st_dev, os.fstat(fd).st_ino) for n, fd in self.fds.items()}
            p = self.plan
            fields = {'schema', 'unit', 'host_boot_id', 'local_peer',
                'initiator', 'responder', 'session_id', 'generation', 'issued_epoch_us',
                'expires_epoch_us', 'profile', 'object', 'sender_unit', 'country',
                'antenna_confirmed', 'max_frames'}
            require(type(p) is dict and p.get('schema') in
                    ('meshticulum.bench-run.v1', 'meshticulum.service-run.v1'), 'invalid_bench_plan')
            is_service = p['schema'] == 'meshticulum.service-run.v1'
            require(set(p) == fields | ({'service'} if is_service else set()), 'invalid_bench_plan')
            self.service_kind, self.service_format = ServiceKind.KNOWLEDGE, None
            self.application_expires_epoch_us = p['expires_epoch_us']
            if is_service:
                service = p['service']
                require(type(service) is dict and set(service) ==
                        {'kind', 'format', 'application_expires_epoch_us'}, 'invalid_service_plan')
                require(type(service['format']) is str and service['format'] in SERVICE_FORMATS
                        and service['kind'] == SERVICE_FORMATS[service['format']].value,
                        'service_format_mismatch')
                self.service_kind = SERVICE_FORMATS[service['format']]
                self.service_format = service['format']
                self.application_expires_epoch_us = service['application_expires_epoch_us']
                require(type(self.application_expires_epoch_us) is int
                        and p['issued_epoch_us'] < self.application_expires_epoch_us <= p['expires_epoch_us'],
                        'invalid_service_deadline')
            require(p['unit'] == expected_unit
                    and p['host_boot_id'] == host_boot_id and str(uuid.UUID(host_boot_id)) == host_boot_id,
                    'wrong_bench_host')
            require(p['country'] == 'MY' and p['antenna_confirmed'] is True,
                    'bench_country_or_antenna_unconfirmed')
            require(type(p['sender_unit']) is str and p['sender_unit'] in ('unit-a', 'unit-b', 'unit-c')
                    and expected_unit in ('unit-a', 'unit-b', 'unit-c'), 'invalid_bench_unit')
            self.local_peer = PeerID(**p['local_peer'])
            session = SessionKey(PeerID(**p['initiator']), PeerID(**p['responder']),
                                 bytes.fromhex(p['session_id']), bytes.fromhex(p['generation']))
            self.sends = p['sender_unit'] == expected_unit
            require(self.local_peer == (session.initiator if self.sends else session.responder),
                    'bench_endpoint_role_mismatch')
            self.profile = RadioProfile(**p['profile'])
            self.profile.encode(board_id=1)
            require(self.profile.frequency_hz == 921375000
                    and (self.profile.spreading_factor, self.profile.bandwidth_hz) in MODES.values()
                    and self.profile.coding_rate == 5
                    and self.profile.chip_power_dbm == 0 and self.profile.sync_word == 0x42
                    and self.profile.preamble == 8 and 10000 <= self.profile.lease_ms <= 180000
                    and self.profile.max_boot_attempts <= 256
                    and self.profile.max_boot_estimated_airtime_us <= 60000000,
                    'unsupported_initial_bench_profile')
            obj = p['object']
            require(type(obj) is dict and set(obj) == {'sha256', 'bytes', 'chunk_index', 'object_id'}
                    and type(obj['bytes']) is int and 1 <= obj['bytes'] <= 16384
                    and type(obj['chunk_index']) is int and 0 <= obj['chunk_index'] < 64,
                    'invalid_bench_object_descriptor')
            for name in ('sha256', 'object_id'):
                require(type(obj[name]) is str and len(obj[name]) == 64
                        and bytes.fromhex(obj[name]).hex() == obj[name], 'invalid_bench_object_digest')
            secret = os.pread(self.fds['secret.bin'], 33, 0)
            self.route_record = {k: p[k] for k in ('initiator', 'responder', 'session_id', 'generation',
                                                  'profile', 'object', 'sender_unit', 'country')}
            if is_service:
                self.route_record['service'] = p['service']
            route_digest = hashlib.sha256(canonical(self.route_record)).digest()
            self.grant = BenchGrant(session, 'bench_transfer', route_digest,
                p['issued_epoch_us'], p['expires_epoch_us'], 208, p['max_frames'], secret)
            require(self.grant.max_frames <= 512
                    and self.grant.expires_epoch_us - self.grant.issued_epoch_us <= 240000000,
                    'bench_grant_too_large')
            epoch, mono = clock()
            require(self.grant.issued_epoch_us <= epoch < self.grant.expires_epoch_us,
                    'bench_grant_not_current')
            self.deadline_us = mono + self.grant.expires_epoch_us - epoch
            self.application_deadline_us = mono + self.application_expires_epoch_us - epoch
            require(epoch < self.application_expires_epoch_us, 'service_application_expired')
            self.last_clock = (epoch, mono)
            self.owner_id = hashlib.sha256(b'owner' + self.grant.digest + expected_unit.encode()).digest()[:16]
            self.scheduler_epoch = hashlib.sha256(b'scheduler' + self.owner_id).digest()[:16]
            self.broker_epoch = hashlib.sha256(b'broker' + self.owner_id).digest()[:16]
            self.ledger_epoch = hashlib.sha256(b'ledger' + self.owner_id).digest()[:16]
            self.flow_key = FlowKey(session, session.initiator, 1)
            self._contract(epoch, mono)
            # Durable single-use claim; a crashed start is deliberately spent.
            fd = os.open(self.path / 'started', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, self.grant.digest.hex().encode() + b'\n')
                os.fsync(fd)
            finally:
                os.close(fd)
            fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            self.closed = False
            require(self.live(), 'bench_grant_not_live')
        except BaseException:
            self.close()
            raise

    def _contract(self, epoch, mono):
        profile_digest = hashlib.sha256(canonical(asdict(self.profile))).digest()
        air = estimated_airtime_us(self.profile, 255) * 1.10 / 1000000 + .001
        ack_air = estimated_airtime_us(self.profile, 70) * 1.10 / 1000000 + .001
        exchange = air + ack_air
        burst = 4 if self.profile.bandwidth_hz >= 250000 else 1
        initial_rto = max(.5, 3 * exchange + .15, (burst + 1) * air + 2 * ack_air + .15)
        minimum_rto = max(.25, 2 * exchange + .10, burst * air + ack_air + .10)
        # SOURCE_FIXTURE is the frozen API's label for unmeasured source cost.
        # Receipts explicitly call this a calculation, never measured RF time.
        self.carrier_profile = CarrierProfile(CarrierKind.BINARY, 255, 31, 16, profile_digest,
            initial_rto, minimum_rto, 15.0, air, 0.0, ack_air,
            mono / 1000000, 240, ProfileBasis.SOURCE_FIXTURE)
        route = RouteBinding(self.grant.session, ServiceID(self.grant.session.responder, self.service_kind),
            str(uuid.UUID(bytes=self.grant.session.session_id)), self.grant.route_binding_digest,
            profile_digest, 1)
        duration = min(self.profile.lease_ms / 1000 - 3, (self.deadline_us - mono) / 1000000 - 3)
        require(duration > 5, 'bench_grant_too_short')
        self.deadlines = DeadlineSet(min(mono / 1000000 + duration, self.application_deadline_us / 1000000),
                                    self.deadline_us / 1000000,
                                    self.grant.expires_epoch_us / 1000000)
        limits = CapabilityLimits(208, 832, 16384, 0, 4, 16384, 4, 1, 1, 128, 32)
        private = self.service_kind is ServiceKind.MESSAGE
        feature = Feature.PROTECTED_MESSAGE_V2 if private else Feature.OBJECT_RESUME
        self.contract = NegotiatedContract(1, frozenset({Feature.BYTE_CREDIT, Feature.SELECTIVE_ACK_32,
            feature}), limits, route, self.deadlines,
            TrafficClass.INTERACTIVE if private else TrafficClass.BULK, 1, self.owner_id)

    def live(self):
        if self.closed:
            return False
        try:
            epoch, mono = self.clock()
            require(epoch >= self.last_clock[0] and mono >= self.last_clock[1], 'bench_clock_reversed')
            require(self.grant.issued_epoch_us <= epoch < self.grant.expires_epoch_us
                    and mono < self.deadline_us and epoch < self.application_expires_epoch_us
                    and mono < self.application_deadline_us, 'bench_authority_expired')
            for name, fd in self.fds.items():
                current = (self.path / name).lstat()
                _private_info(current)
                require((current.st_dev, current.st_ino) == self.file_ids[name], 'bench_authority_replaced')
            require(os.pread(self.fds['active'], 32, 0) == b'active\n', 'bench_authority_retired')
            require(os.pread(self.fds['grant.json'], 16385, 0) == self.plan_bytes, 'bench_authority_mutated')
            require(os.pread(self.fds['secret.bin'], 33, 0) == self.grant.secret, 'bench_key_mutated')
            self.last_clock = (epoch, mono)
            return True
        except (OSError, ValueError):
            return False

    def _request(self, request):
        require(self.live() and request.flow == self.flow_key and request.route == self.contract.route
                and request.profile == self.carrier_profile
                and request.scheduler_epoch == self.scheduler_epoch
                and request.broker_epoch == self.broker_epoch
                and request.deadlines == self.deadlines and request.flow_reservation is not None,
                'bench_dispatch_binding_refused')
        epoch, mono = self.clock()
        self.deadlines.check(mono / 1000000, epoch / 1000000)

    @contextmanager
    def service_lease(self, request):
        require(not self.held, 'nested_bench_service_lease')
        fcntl.flock(self.fds['active'], fcntl.LOCK_SH | fcntl.LOCK_NB)
        try:
            self._request(request)
            self.held[LeaseScope.SERVICE] = request
            yield DispatchGrant(request, LeaseScope.SERVICE, self.deadlines, 1, self.broker_epoch)
        finally:
            self.held.pop(LeaseScope.SERVICE, None)
            fcntl.flock(self.fds['active'], fcntl.LOCK_UN)

    @contextmanager
    def broker_lease(self, request):
        require(self.held.get(LeaseScope.SERVICE) is request and LeaseScope.BROKER not in self.held,
                'bench_service_lease_not_held')
        self._request(request)
        require(self.owner_probe() is True, 'bench_radio_owner_not_held')
        self.held[LeaseScope.BROKER] = request
        try:
            yield DispatchGrant(request, LeaseScope.BROKER, self.deadlines, 1, self.broker_epoch)
        finally:
            self.held.pop(LeaseScope.BROKER, None)

    def require_dispatch(self, frame):
        self._request(frame.request)
        require(all(self.held.get(scope) is frame.request for scope in (LeaseScope.SERVICE, LeaseScope.BROKER))
                and self.owner_probe() is True, 'bench_dispatch_leases_not_held')
        require(hashlib.sha256(frame.payload).digest() == frame.request.payload_digest
                and len(frame.payload) == frame.request.payload_bytes, 'bench_dispatch_payload_changed')

    def application_live(self):
        """Finite service admission only; owner consent remains a separate gate."""
        if not self.live() or self.owner_probe() is not True:
            return False
        try:
            epoch, mono = self.clock()
            return self.deadlines.check(mono / 1000000, epoch / 1000000)
        except ValueError:
            return False

    @contextmanager
    def application_lease(self):
        """Serialize completed-object use against the same grant revocation lock.

        This grants neither radio access nor personal storage permission. The
        recipient must independently hold its live protected-store/peer guards.
        """
        require(self.service_format is not None and not self.held, 'service_application_lease_refused')
        fcntl.flock(self.fds['active'], fcntl.LOCK_SH | fcntl.LOCK_NB)
        try:
            self.held['application'] = self
            require(self.application_live(), 'service_application_not_live')
            yield self
            require(self.application_live(), 'service_application_not_live')
        finally:
            self.held.pop('application', None)
            fcntl.flock(self.fds['active'], fcntl.LOCK_UN)

    def close(self):
        self.closed = True
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()


def revoke(directory):
    """Retire through the same lock held during physical attempts; never renew."""
    path = Path(directory)
    _private_info(path.lstat(), directory=True)
    fd = _open(path / 'active', os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.ftruncate(fd, 0)
        os.write(fd, b'retired\n')
        os.fsync(fd)
    finally:
        os.close(fd)
