#!/usr/bin/env python3
"""A bounded own-hardware file example; see examples/README.md before `transfer`.

`manifest`, `assemble` and `firmware-identity` use no hardware. `transfer`
consumes already approved RunAuthority grants. It neither provisions grants nor
discovers RF permission.
No application or fleet integration is imported by this example.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import time

from meshticulum.bench.admission import Gfsk250Admission, RadioAdmission
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import OperationLimits, RunAuthority
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
from meshticulum.bench.hat_worker import Gfsk250HATWorker, HATWorker, journal_binding as hat_binding
from meshticulum.bench.linux_hat import LinuxHAT
from meshticulum.bench.open_watch import OpenCollisionWatch
from meshticulum.bench.objects import VerifiedChunks
from meshticulum.bench.serial_port import ExclusiveSerial, usb_identity
from meshticulum.bench.serial_protocol import Frame, Message, Gfsk250Status, RadioProfile
from meshticulum.bench.serial_worker import Gfsk250SerialWorker, SerialWorker, journal_binding
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, require
from meshticulum.scheduler.budget import BudgetPolicy, Ledger

CHUNK_BYTES, MAX_FILE_BYTES, ACK_GRACE_US = 16384, 64 * 16384, 20000000
FILE_SCHEMA = 'meshticulum.example-radio-file.v1'
MAX_FIRMWARE_SOURCE_BYTES = 8 * 1024 * 1024
MODEMS = {'gfsk250': ('Gfsk250Profile', 'Gfsk250HATWorker', 'Gfsk250SerialWorker', 'Gfsk250Admission'),
          'lora': ('RadioProfile', 'HATWorker', 'SerialWorker', 'RadioAdmission')}


def modem_classes(modem):
    """Return profile, HAT worker, serial worker and admission for one modem."""
    require(modem in MODEMS, 'example_unknown_radio_modem')
    return tuple(globals()[name] for name in MODEMS[modem])


def plan_modem(plan):
    """Plans without a modem marker are the LoRa profile path."""
    modem = plan.get('radio_modem')
    if modem is None:
        return 'lora'
    require(modem == 'gfsk250', 'example_unknown_radio_modem')
    return modem


def add_operation_limit_arguments(parser):
    parser.add_argument('--operation-attempt-limit', type=int,
        help='reduce this operation journal attempt cap; never changes the MCU boot cap')
    parser.add_argument('--operation-airtime-limit-us', type=int,
        help='reduce this operation journal estimated-airtime cap in microseconds')


def operation_journal_limits(args, admitted):
    attempts = getattr(args, 'operation_attempt_limit', None)
    airtime = getattr(args, 'operation_airtime_limit_us', None)
    limits = OperationLimits(admitted.attempts if attempts is None else attempts,
                             admitted.estimated_airtime_us if airtime is None else airtime)
    require(limits.attempts <= admitted.attempts
            and limits.estimated_airtime_us <= admitted.estimated_airtime_us,
            'example_operation_limits_exceed_grant')
    return limits


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path, limit, *, pin=None, private=False):
    """Read an ordinary bounded local file, refusing replacement and mutation."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= limit, 'invalid_example_file')
        if private:
            require(before.st_uid == os.geteuid()
                    and stat.S_IMODE(before.st_mode) == 0o600, 'example_file_not_private')
        data = os.read(fd, limit + 1)
        after = os.fstat(fd)
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                           s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(len(data) == before.st_size and stamp(before) == stamp(after)
                and stamp(after) == stamp(Path(path).lstat()), 'example_file_changed')
        require(pin is None or sha(data) == pin, 'example_file_hash_mismatch')
        return data
    finally:
        os.close(fd)


def manifest(data):
    require(type(data) is bytes and 0 < len(data) <= MAX_FILE_BYTES, 'example_file_size')
    digest = sha(data)
    return {'schema': FILE_SCHEMA, 'bytes': len(data), 'sha256': digest,
            'chunks': [{'object_id': digest, 'chunk_index': i, 'bytes': len(part),
                        'sha256': sha(part)} for i, part in enumerate(
                            data[n:n + CHUNK_BYTES] for n in range(0, len(data), CHUNK_BYTES))]}


def load_manifest(path, pin):
    value = json.loads(read_file(path, 32768, pin=pin))
    require(type(value) is dict and set(value) == {'schema', 'bytes', 'sha256', 'chunks'}
            and value['schema'] == FILE_SCHEMA and type(value['bytes']) is int
            and 0 < value['bytes'] <= MAX_FILE_BYTES and type(value['chunks']) is list
            and 1 <= len(value['chunks']) <= 64, 'invalid_example_manifest')
    require(type(value['sha256']) is str and re.fullmatch('[0-9a-f]{64}', value['sha256']),
            'invalid_example_digest')
    total = 0
    for index, chunk in enumerate(value['chunks']):
        require(type(chunk) is dict and set(chunk) == {'object_id', 'chunk_index', 'bytes', 'sha256'}
                and chunk['object_id'] == value['sha256'] and type(chunk['chunk_index']) is int
                and chunk['chunk_index'] == index and type(chunk['bytes']) is int
                and 0 < chunk['bytes'] <= CHUNK_BYTES and type(chunk['sha256']) is str
                and re.fullmatch('[0-9a-f]{64}', chunk['sha256']), 'invalid_example_chunk')
        require(index == len(value['chunks']) - 1 or chunk['bytes'] == CHUNK_BYTES,
                'noncanonical_example_chunks')
        total += chunk['bytes']
    require(total == value['bytes'], 'example_manifest_size_mismatch')
    return value


def publish(path, data):
    """Publish a verified result once; never overwrite an existing destination."""
    directory = Path(path).absolute().parent
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700, 'example_output_directory_not_private')
    # VerifiedChunks uses fsynced no-replace publication and directory syncs.
    VerifiedChunks._publish(Path(path), data)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def bind_file_window(cache, file, issued, expires, *, time_source=None, boot_reader=None):
    return FileWindow(cache.path, file, issued, expires,
                      time_source=time_source, boot_reader=boot_reader)


def clock():
    return time.time_ns() // 1000, time.monotonic_ns() // 1000


def host_boot():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def firmware_source_identity(source):
    require(type(source) is dict
            and source.get('schema') == 'meshticulum.raw-firmware-prepared.v1'
            and type(source.get('application')) is dict
            and set(source['application']) in (
                {'main.cpp', 'protocol.hpp', 'gfsk_profile.hpp', 'gfsk250_profile.hpp'},
                {'main.cpp', 'protocol.hpp', 'gfsk_profile.hpp', 'gfsk250_profile.hpp',
                 'burst_adapter.hpp', 'experimental/tx_burst.hpp'})
            and all(type(value) is str and re.fullmatch('[0-9a-f]{64}', value)
                    for value in source['application'].values())
            and source.get('identity') == 'mtb1-' + sha(json.dumps(
                source['application'], sort_keys=True).encode())[:10], 'example_firmware_source_mismatch')
    return source['identity']


def firmware_identity(path, pin):
    return firmware_source_identity(json.loads(read_file(path, 65536, pin=pin)))


def compact_firmware_identity(args):
    """Retain an exact full-manifest digest; do not verify a build or source tree."""
    pin = args.firmware_source_sha256
    require(type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin), 'invalid_example_digest')
    source = json.loads(read_file(args.firmware_source, MAX_FIRMWARE_SOURCE_BYTES, pin=pin))
    identity = firmware_source_identity(source)
    compact = {key: source[key] for key in ('schema', 'identity', 'application')}
    compact['source_manifest_sha256'] = pin
    data = json.dumps(compact, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    publish(args.output, data)
    return dict(output=str(args.output), identity=identity,
                firmware_source_sha256=sha(data), source_manifest_sha256=pin)


def inspect_usb(args):
    """HELLO, capabilities and STATUS only: no grant, ARM, TX or disarm."""
    modem = getattr(args, 'modem', 'gfsk250')
    _, _, serial_cls, _ = modem_classes(modem)
    version = firmware_identity(args.firmware_source, args.firmware_source_sha256)
    with ExclusiveSerial(args.device, usb_serial=args.usb_serial) as channel:
        worker = serial_cls(channel)
        identity = worker.discover(expected_board_id=1, expected_version=version)
        request, deadline = secrets.randbits(63) or 1, channel.monotonic() + 2
        worker._send(Frame(Message.STATUS, identity.boot, request), deadline)
        reply = worker._wait(request, deadline)
        require(reply.boot == identity.boot and reply.request == request
                and channel.monotonic() < deadline, 'example_unbound_status')
        if modem == 'gfsk250' and reply.type is Message.REFUSED and reply.body == b'\x01':
            # Current firmware refuses LoRa STATUS in GFSK mode (BAD_COMMAND).
            # Query that modem without reconfiguring it or renewing this deadline.
            request += 1
            worker._send(Frame(Message.GFSK250_STATUS, identity.boot, request), deadline)
            reply = worker._wait(request, deadline)
            require(reply.type is Message.GFSK250_STATUS_REPLY, 'example_expected_gfsk250_status')
        require(reply.boot == identity.boot and reply.request == request
                and channel.monotonic() < deadline, 'example_unbound_status')
        if reply.type is Message.GFSK250_STATUS_REPLY:
            status = Gfsk250Status.from_reply(reply, boot=identity.boot,
                request=request, board_id=identity.board_id)
            require(not status.armed and not status.transmitting and not status.fenced,
                    'example_board_not_disarmed_and_unfenced')
            attempts, airtime, dropped = (status.boot_attempts,
                status.boot_reserved_estimated_airtime_us, status.dropped_receive)
        else:
            require(reply.type is Message.STATUS_REPLY and len(reply.body) == 49,
                    'example_expected_unconfigured_or_gfsk250_status')
            attempts, airtime, dropped = struct.unpack('!IQI', reply.body[30:46])
            require(reply.body[46:] == bytes(3), 'example_board_not_disarmed_and_unfenced')
        return dict(firmware=version, boot=identity.boot.hex(), board_id=identity.board_id,
                    boot_attempts=attempts, boot_reserved_airtime_us=airtime,
                    dropped_receive=dropped, armed=False, transmitting=False, fenced=False)


class FileWindow:
    """A retained original file clock/boot binding; not RF authority."""
    def __init__(self, directory, file, issued, expires, *, time_source=None, boot_reader=None,
                 max_window_us=240_000_000):
        self.path = Path(directory) / (file['sha256'] + '-window.json')
        self.clock, self.boot_reader = time_source or clock, boot_reader or host_boot
        now = self.clock()
        require(type(issued) is int and type(expires) is int and 0 < issued < expires
                and type(max_window_us) is int and 0 < max_window_us <= 1200000000
                and expires - issued <= max_window_us and issued <= now[0] < expires,
                'example_file_window_bound')
        expected = dict(schema='meshticulum.example-file-window.v2', manifest=file,
                        issued_epoch_us=issued, expires_epoch_us=expires,
                        host_boot_id=self.boot_reader())
        if not self.path.exists():
            first = dict(expected, original_epoch_us=now[0], original_monotonic_us=now[1],
                         deadline_monotonic_us=now[1] + expires - now[0])
            publish(self.path, json.dumps(first, sort_keys=True, separators=(',', ':')).encode())
        self.raw = read_file(self.path, 65536, private=True)
        self.record = json.loads(self.raw)
        require(set(self.record) == set(expected) | {'original_epoch_us', 'original_monotonic_us',
                'deadline_monotonic_us'} and all(self.record[k] == v for k, v in expected.items())
                and all(type(self.record[k]) is int for k in ('original_epoch_us',
                    'original_monotonic_us', 'deadline_monotonic_us'))
                and self.record['deadline_monotonic_us'] == self.record['original_monotonic_us']
                    + expires - self.record['original_epoch_us'], 'example_original_file_window_changed')
        info = self.path.lstat()
        self.identity = (info.st_dev, info.st_ino)
        self.deadline_us, self.last = self.record['deadline_monotonic_us'], None
        self.check()

    def check(self):
        info = self.path.lstat()
        require((info.st_dev, info.st_ino) == self.identity
                and read_file(self.path, 65536, private=True) == self.raw
                and self.boot_reader() == self.record['host_boot_id'], 'example_file_window_rebound')
        now = self.clock()  # Last callback: file/boot reads cannot make this a stale sample.
        require(type(now) is tuple and len(now) == 2
                and all(type(n) is int and 0 <= n < 2**63 for n in now), 'example_invalid_clock')
        require(self.record['original_epoch_us'] <= now[0] < self.record['expires_epoch_us']
                and self.record['original_monotonic_us'] <= now[1] < self.deadline_us
                and (self.last is None or all(a >= b for a, b in zip(now, self.last))),
                'example_original_file_window_expired')
        self.last = now
        return now

    def current(self):
        try:
            self.check()
            return True
        except (OSError, ValueError):
            return False

    def require_capacity(self, remaining_parts):
        now = self.check()
        require(type(remaining_parts) is int and remaining_parts > 0
                and remaining_parts * ACK_GRACE_US < self.deadline_us - now[1],
                'example_no_time_for_remaining_ack_graces')

    def pin_mcu(self, identity):
        self.check()
        path = self.path.with_name(self.path.stem + '-mcu.json')
        raw = json.dumps(dict(file_window_sha256=sha(self.raw), boot=identity.boot.hex(),
            board_id=identity.board_id, firmware=identity.version), sort_keys=True).encode()
        if not path.exists():
            publish(path, raw)
        require(read_file(path, 4096, private=True) == raw, 'example_mcu_boot_or_identity_changed')
        self.check()


class HeldHardware:
    """Fresh cooperative OS ownership checks for an already exclusive driver.

    The SDK process must not fork or pass its held device descriptors. Host
    root is trusted; this probe does not isolate the radio from host root.
    """
    def __init__(self, transport, *, by_id=None, usb_serial=None):
        self.transport, self.by_id, self.usb_serial = transport, by_id, usb_serial
        self.boot, self.pid = host_boot(), os.getpid()
        names = ('fd', 'lock_fd') if type(transport) is ExclusiveSerial else (
            'spi_fd', 'chip_fd', 'lines_fd', 'lock_fd')
        self.fds = tuple((name, getattr(transport, name), os.fstat(getattr(transport, name)))
                         for name in names)
        self.opens = OpenCollisionWatch(self.fds[0][2])

    def close(self):
        self.opens.close()

    def current(self):
        try:
            if os.getpid() != self.pid or host_boot() != self.boot:
                return False
            for name, fd, original in self.fds:
                if getattr(self.transport, name) != fd:
                    return False
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino, info.st_rdev, info.st_mode, info.st_uid) != (
                        original.st_dev, original.st_ino, original.st_rdev,
                        original.st_mode, original.st_uid):
                    return False
            radio = self.fds[0][2]
            if self.by_id is not None:
                node = Path(self.by_id).resolve(strict=True)
                bound = (node.stat().st_rdev == radio.st_rdev
                         and usb_identity(node.name) == ('303a', '1001', self.usb_serial))
            else:
                bound = (Path('/dev/spidev0.0').stat().st_rdev == radio.st_rdev
                         and self.transport.boot_id == self.boot)
            return bound and self.opens.current()
        except (OSError, ValueError):
            return False


class ChunkTransfer:
    """Compose the public reliable flow; receiver ACK retirement stays finite."""
    def __init__(self, authority, worker, journal, cipher, descriptor, cache, *, clock):
        require(authority.plan['object'] == descriptor, 'example_grant_object_mismatch')
        window = authority.profile.lease_ms * 1000
        own = min(window, authority.profile.max_boot_estimated_airtime_us)
        ledger = Ledger(BudgetPolicy(window, own, min(window, 2 * own),
                                     min(window, 1000000), quantization_us=1),
                        authority.ledger_epoch, clock()[1])
        self.endpoint = BenchFlow(authority=authority, worker=worker, cipher=cipher,
            journal=journal, flow_key=authority.flow_key, contract=authority.contract,
            profile=authority.carrier_profile, ledger=ledger,
            scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
            sends=authority.sends, clock=clock)
        self.authority, self.descriptor, self.cache, self.clock = authority, descriptor, cache, clock
        require(authority.application_deadline_us - clock()[1] > ACK_GRACE_US,
                'example_no_room_for_ack_retirement')
        self.completed_at = None

    def enable_receipt_confirmation(self, context):
        self.endpoint.enable_receipt_confirmation(context,
            receiver_ready=None if self.endpoint.sends else lambda: self.completed_at is not None)

    def send(self, payload):
        self.endpoint.send(payload)

    def step(self):
        require(self.authority.application_live(), 'example_authority_not_current')
        complete = self.endpoint.step()
        if not complete:
            return False
        if self.endpoint.sends:
            if self.endpoint.receipt_confirmation_enabled:
                self.endpoint.confirm_receipt()
            return True
        if self.completed_at is None:
            require(self.authority.application_live(), 'example_commit_authority_expired')
            self.cache.commit(self.descriptor, bytes(self.endpoint.received))
            require(self.authority.application_live(), 'example_commit_authority_expired')
            self.completed_at = self.clock()[1]
        # Repeated authenticated final DATA can still receive its original ACK.
        # This start is never renewed, and the original grant still bounds step.
        return (self.endpoint.receipt_confirmed
                or self.clock()[1] - self.completed_at >= ACK_GRACE_US)

    def close(self):
        self.endpoint.close()


def transfer_chunk(args):
    file = load_manifest(args.manifest, args.manifest_sha256)
    require(0 <= args.chunk < len(file['chunks']), 'example_chunk_index')
    descriptor = file['chunks'][args.chunk]
    raw = read_file(args.grant_directory / 'grant.json', 16384,
                    pin=args.grant_sha256, private=True)
    plan = json.loads(raw)
    modem = plan_modem(plan)
    require(plan['object'] == descriptor
            and plan['schema'] == 'meshticulum.bench-run.v1', 'example_requires_matching_bench_grant')
    require(plan['expires_epoch_us'] - plan['issued_epoch_us'] <= 240000000,
            'example_grant_window_too_large')
    require(args.file_issued_epoch_us == plan['issued_epoch_us']
            and args.file_expires_epoch_us == plan['expires_epoch_us'],
            'example_original_file_deadline_mismatch')
    require(len(file['chunks']) * ACK_GRACE_US
            < args.file_expires_epoch_us - args.file_issued_epoch_us,
            'example_file_cannot_fit_original_window')
    admission = json.loads(read_file(args.admission, 8192, pin=args.admission_sha256, private=True))
    for name in ('frequencies_hz', 'powers_dbm'):
        if name in admission:
            admission[name] = tuple(admission[name])
    if 'waveforms' in admission:
        admission['waveforms'] = tuple(tuple(row) for row in admission['waveforms'])
    admission = modem_classes(modem)[3](**admission)
    payload = None
    if args.input is not None:
        data = read_file(args.input, MAX_FILE_BYTES)
        require(manifest(data) == file, 'example_input_mismatch')
        offset = args.chunk * CHUNK_BYTES
        payload = data[offset:offset + descriptor['bytes']]
    require((plan['sender_unit'] == args.unit) == (payload is not None), 'example_role_mismatch')
    version = firmware_identity(args.firmware_source, args.firmware_source_sha256)
    cache = VerifiedChunks(args.chunks)
    window = bind_file_window(cache, file, args.file_issued_epoch_us, args.file_expires_epoch_us)
    window.require_capacity(len(file['chunks']) - args.chunk)
    return run_object(args, raw, admission, version, payload, window,
        lambda authority, worker, journal, cipher: ChunkTransfer(
            authority, worker, journal, cipher, descriptor, cache, clock=clock))


def run_object(args, raw, admission, version, payload, window, factory):
    plan = json.loads(raw)
    admitted_limits = OperationLimits.from_plan(plan)
    limits = operation_journal_limits(args, admitted_limits)
    transport = (LinuxHAT() if args.interface == 'hat' else
                 ExclusiveSerial(args.device, usb_serial=args.usb_serial))
    cleanup, errors, result = [transport.close], [], None
    try:
        owner = HeldHardware(transport, by_id=args.device if args.interface == 'usb' else None,
                             usb_serial=args.usb_serial)
        cleanup.append(owner.close)
        authority = RunAuthority(args.grant_directory, expected_unit=args.unit,
            host_boot_id=host_boot(), clock=clock, owner_probe=lambda: owner.current() and window.current(), radio_admission=admission)
        cleanup.append(authority.close)
        require(authority.plan_bytes == raw and authority.application_live(), 'example_authority_changed')
        require(authority.operation_limits == admitted_limits, 'example_operation_limits_changed')
        profile = authority.profile
        profile_cls, hat_cls, serial_cls, _ = modem_classes(plan_modem(plan))
        require(type(profile) is profile_cls, 'example_requires_matching_profile')
        policy = authority.data_burst_policy
        require(not policy or not authority.sends or args.interface == 'usb',
                'data_burst_sender_requires_usb')
        worker = ((BurstSerialWorker(transport, policy=policy) if policy and authority.sends
                   else serial_cls(transport)) if args.interface == 'usb' else None)
        if worker is not None:
            identity = worker.discover(expected_board_id=1, expected_version=version)
            window.pin_mcu(identity)
            binding = journal_binding(authority.grant.digest, identity, profile)
        else:
            binding = hat_binding(authority.grant.digest, transport.boot_id, profile)
        journal = AttemptJournal(args.grant_directory / 'attempts.db', binding=binding,
            max_attempts=limits.attempts, max_airtime_us=limits.estimated_airtime_us,
            create=True, request_clock=lambda: clock()[0])
        cleanup.append(journal.close)
        store = SessionStore(args.grant_directory / 'wire.db', authority.grant, authority.local_peer,
                             boot_id=host_boot(), clock=clock, create=True)
        cleanup.extend((store.close, store.retire))
        authorize = authority.application_live
        if worker is not None:
            # Retain containment even when ARM only partially succeeds.
            cleanup.append(worker.disarm)
            worker.arm(journal=journal, profile=profile, context_digest=authority.grant.digest,
                       authorize=authorize, authority_deadline=min(authority.deadlines.application_deadline_monotonic, window.deadline_us / 1000000))
        else:
            worker = hat_cls(transport, journal=journal, profile=profile,
                context_digest=authority.grant.digest, authorize=authorize,
                authority_deadline=min(authority.deadlines.application_deadline_monotonic, window.deadline_us / 1000000),
                antenna_confirmed=plan['antenna_confirmed'])
            cleanup.append(worker.disarm)
        session = factory(authority, worker, journal,
            SessionCipher(authority.grant, authority.local_peer, store))
        cleanup.append(session.close)
        if payload is not None:
            session.send(payload)
        while not session.step():
            window.check()
            time.sleep(.005)
        window.check()
        result = {'chunk': args.chunk, 'sender_peer_receipt': authority.sends,
                  'receiver_chunk_verified': not authority.sends,
                  'whole_file_verified': False, 'attempt_journal': journal.snapshot(),
                  'operation_limits': dict(attempts=limits.attempts,
                                          estimated_airtime_us=limits.estimated_airtime_us)}
        if session.endpoint.receipt_confirmation_enabled:
            result['receipt_confirmation'] = session.endpoint.receipt_confirmation_snapshot()
    except BaseException as error:
        errors.append(error)
    finally:
        for close in reversed(cleanup):
            try:
                close()
            except BaseException as error:
                errors.append(error)
    if errors:
        raise BaseExceptionGroup('File transfer or cleanup failed; retain all run state', errors)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('manifest', help='describe a local file; no hardware')
    create.add_argument('input', type=Path)
    compact = commands.add_parser('firmware-identity',
        help='write compact identity from a pinned SOURCE.json; no hardware')
    compact.add_argument('--output', required=True, type=Path)
    assemble = commands.add_parser('assemble', help='verify and publish all chunks; no hardware')
    inspect = commands.add_parser('inspect', help='read HELLO/capabilities/initial STATUS after boot; no ARM or TX')
    run = commands.add_parser('transfer', help='consume one preapproved grant; arms radio and transmits DATA or ACK')
    for sub in (assemble, run):
        sub.add_argument('--manifest', required=True, type=Path)
        sub.add_argument('--manifest-sha256', required=True)
        sub.add_argument('--chunks', required=True, type=Path)
    assemble.add_argument('--output', required=True, type=Path)
    run.add_argument('--chunk', required=True, type=int)
    run.add_argument('--grant-directory', required=True, type=Path)
    run.add_argument('--grant-sha256', required=True)
    run.add_argument('--file-expires-epoch-us', required=True, type=int)
    run.add_argument('--file-issued-epoch-us', required=True, type=int)
    run.add_argument('--admission', required=True, type=Path)
    run.add_argument('--admission-sha256', required=True)
    add_operation_limit_arguments(run)
    for sub in (run, inspect, compact):
        sub.add_argument('--firmware-source', required=True, type=Path)
        sub.add_argument('--firmware-source-sha256', required=True)
    inspect.add_argument('--device', type=Path, required=True)
    inspect.add_argument('--usb-serial', required=True)
    inspect.add_argument('--modem', choices=tuple(MODEMS), default='gfsk250')
    run.add_argument('--unit', required=True)
    run.add_argument('--interface', choices=('hat', 'usb'), required=True)
    run.add_argument('--device', type=Path)
    run.add_argument('--usb-serial')
    run.add_argument('--input', type=Path, help='sender only; receiver omits it')
    args = parser.parse_args()
    if args.command == 'manifest':
        value = manifest(read_file(args.input, MAX_FILE_BYTES))
    elif args.command == 'firmware-identity':
        value = compact_firmware_identity(args)
    elif args.command == 'assemble':
        file = load_manifest(args.manifest, args.manifest_sha256)
        data = VerifiedChunks(args.chunks).assemble(file['chunks'])
        require(len(data) == file['bytes'], 'example_assembled_size_mismatch')
        publish(args.output, data)
        value = {'whole_file_verified': True, 'bytes': len(data), 'sha256': sha(data)}
    elif args.command == 'inspect':
        value = inspect_usb(args)
    else:
        require(args.interface != 'usb' or (args.device is not None and args.usb_serial),
                'example_usb_identity_required')
        value = transfer_chunk(args)
    print(json.dumps(value, sort_keys=True))


if __name__ == '__main__':
    main()
