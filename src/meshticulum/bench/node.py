# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Root-only finite bench process. No listener, automatic enrollment or service."""
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import stat
import subprocess
import time

from .attempts import AttemptJournal
from .authority import RunAuthority, _open, _private_info
from .flow_runtime import BenchFlow
from .hat_worker import HATWorker, journal_binding as hat_binding
from .linux_hat import LinuxHAT
from .objects import VerifiedChunks
from .serial_port import ExclusiveSerial
from .serial_protocol import Frame, Message
from .serial_worker import SerialWorker, journal_binding as serial_binding
from .state import SessionStore
from .wire import SessionCipher, require
from ..scheduler.budget import BudgetPolicy, Ledger

FIRMWARE = 'mtb1-9816f92488'
USB = {'unit-a': 'F8:5B:1B:A5:F6:08', 'unit-b': '44:1B:F6:62:56:54'}
PERSONAL = ('sage-lantern-personal-sage.service', 'sage-lantern-voice-loop.service',
            'sage-lantern-llm.service', 'sage-lantern-vlm.service')


def clock():
    return time.time_ns() // 1000, time.monotonic_ns() // 1000


def roles(unit):
    require(platform.system() == 'Linux' and os.geteuid() == 0, 'requires_linux_root')
    require(unit in ('unit-a', 'unit-b', 'unit-c'), 'unknown_bench_unit')
    states = {}
    units = PERSONAL if unit == 'unit-c' else (PERSONAL[:3] + ('sage-lantern-mesh-runtime.service',))
    for name in units:
        result = subprocess.run(['/usr/bin/systemctl', 'show', name, '-p', 'ActiveState', '-p', 'MainPID'],
                                capture_output=True, text=True, timeout=5)
        require(result.returncode == 0, 'cannot_read_service_role')
        row = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        states[name] = row['ActiveState']
        if unit != 'unit-c':
            require(row['ActiveState'] in ('inactive', 'failed') and row['MainPID'] == '0',
                    'private_or_legacy_radio_service_active')
    if unit == 'unit-c':
        require(b'rockchip,rk3588-orangepi-5-plus\0' in Path('/proc/device-tree/compatible').read_bytes(),
                'wrong_c_board')
    else:
        root = Path('/etc/sage-lamplighter-lab')
        require(json.loads((root / 'operator.json').read_text())['unit'] == unit
                and (root / 'enabled').is_file(), 'wrong_public_carrier_unit')
    return states


def _serial(unit):
    serial = USB[unit]
    channel = ExclusiveSerial('/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_'
                              + serial + '-if00', usb_serial=serial)
    try:
        worker = SerialWorker(channel)
        identity = worker.discover(expected_board_id=1, expected_version=FIRMWARE)
        require(identity.boot[:8] == bytes(2) + bytes.fromhex(serial.replace(':', ''))[::-1],
                'wrong_radio_efuse_identity')
        request = time.time_ns() // 1000
        end = channel.monotonic() + 2
        worker._send(Frame(Message.STATUS, identity.boot, request), end)
        reply = worker._wait(request, end)
        require(reply.type is Message.STATUS_REPLY and len(reply.body) == 49
                and reply.body[46:] == bytes(3), 'radio_not_disarmed_or_fenced')
        status = {'firmware': identity.version, 'mcu_boot': identity.boot.hex(),
            'usb_serial': serial, 'disarmed': True,
            'boot_attempts': int.from_bytes(reply.body[30:34], 'big'),
            'boot_reserved_estimated_airtime_us': int.from_bytes(reply.body[34:42], 'big')}
        return channel, worker, status
    except BaseException:
        channel.close()
        raise


def inspect(unit):
    import cryptography
    before = roles(unit)
    result = {'unit': unit, 'host_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'cryptography': cryptography.__version__, 'services_before': before,
              'radio_opened': False, 'arm_commands': 0, 'tx_commands': 0}
    if unit in USB:
        channel, _, status = _serial(unit)
        try: result.update(radio=status, radio_opened=True)
        finally: channel.close()
    result['services_after'] = roles(unit)
    return result


def _read_payload(path, descriptor):
    fd = _open(path)
    try:
        data = os.read(fd, 16385)
    finally:
        os.close(fd)
    require(len(data) == descriptor['bytes'] and hashlib.sha256(data).hexdigest() == descriptor['sha256'],
            'sender_chunk_does_not_match_grant')
    return data


def run(unit, directory, object_directory):
    return _run(unit, directory, object_directory, public_service=False)


def run_public_service(unit, directory):
    """Finite public MR1 service flow; private native provider stays unwired.

    Received bytes are quarantined in the root-only run directory. They are
    not a verified knowledge piece, availability advertisement or public seed.
    No service, listener or autonomous forwarding is commissioned by this run.
    """
    return _run(unit, directory, None, public_service=True)


def _quarantine(directory, data):
    _private_info(directory.lstat(), directory=True)
    require(type(data) is bytes and 0 < len(data) <= 16384, 'invalid_public_service_capture')
    fd = os.open(directory / 'receiver.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as handle:
            handle.write(data); handle.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _run(unit, directory, object_directory, *, public_service):
    before = roles(unit)
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    directory = Path(directory)
    _private_info(directory.lstat(), directory=True)
    result = {'schema': 'meshticulum.physical-service-run.v1' if public_service else 'meshticulum.physical-run.v1',
              'unit': unit, 'host_boot_id': boot,
              'services_before': before, 'start_epoch_us': clock()[0], 'start_monotonic_us': clock()[1],
              'complete': False, 'measured_energy_joules': None, 'measured_eirp_dbm': None,
              'airtime_basis': 'LoRa source estimate; raw driver completion timings are separate',
              'application_data_channel': 'exclusive local radio only', 'stop_requested': False}
    worker = authority = flow = journal = store = channel = None
    runner = None
    saved_handlers = {}
    def stop(signum, _frame): result['stop_requested'] = True
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            saved_handlers[number] = signal.signal(number, stop)
        owned = lambda: channel is not None and channel.lock_fd is not None and all(
            os.fstat(fd) for fd in ([channel.fd] if unit in USB else [channel.spi_fd, channel.lines_fd]))
        # Convert the OS-owner observation to an exact bool, never a caller flag.
        authority = RunAuthority(directory, expected_unit=unit, host_boot_id=boot,
                                 clock=clock, owner_probe=lambda: bool(owned()))
        if public_service:
            from ..contracts.identity import ServiceKind
            from .services import _shape
            require(authority.plan['schema'] == 'meshticulum.service-run.v1'
                    and authority.service_kind is ServiceKind.KNOWLEDGE
                    and authority.service_format in ('MR1.request', 'MR1.response'),
                    'private_service_requires_qualified_native_provider')
            result.update(service_kind=authority.service_kind.value, service_format=authority.service_format,
                          publisher_verified=False, private_admission=False)
        else:
            # Legacy random bench objects never consume service records.
            require(authority.plan['schema'] == 'meshticulum.bench-run.v1',
                    'service_requires_dedicated_runner')
        grant, profile = authority.grant, authority.profile
        result.update(grant_context_sha256=grant.digest.hex(), session_id=grant.session.session_id.hex(),
                      object=authority.plan['object'], sends=authority.sends)
        payload = _read_payload(directory / 'sender.bin', authority.plan['object']) if authority.sends else None
        if public_service and payload is not None:
            _shape(payload, authority.service_format)
        require(authority.sends or not (directory / 'sender.bin').exists(), 'receiver_has_ip_payload')
        # The fixed paths contain only bench state. No system or personal store
        # is queried as an identity/key source.
        store = SessionStore(directory / 'wire.sqlite', grant, authority.local_peer,
                             boot_id=boot, clock=clock, create=True)
        if unit in USB:
            channel, worker, radio = _serial(unit)
            result['radio'] = radio
            require(radio['boot_attempts'] < profile.max_boot_attempts
                    and radio['boot_reserved_estimated_airtime_us'] < profile.max_boot_estimated_airtime_us,
                    'mcu_boot_budget_already_exhausted')
            binding = serial_binding(grant.digest, worker.identity, profile)
        else:
            channel = LinuxHAT()
            binding = hat_binding(grant.digest, boot, profile)
            result['radio'] = {'model': 'Waveshare SX1262 HF', 'spi': '/dev/spidev0.0'}
        journal = AttemptJournal(directory / 'attempts.sqlite', binding=binding,
            max_attempts=profile.max_boot_attempts, max_airtime_us=profile.max_boot_estimated_airtime_us,
            create=True)
        deadline = authority.deadline_us / 1000000
        if unit in USB:
            worker.arm(journal=journal, profile=profile, context_digest=grant.digest,
                       authorize=authority.live, authority_deadline=deadline)
        else:
            worker = HATWorker(channel, journal=journal, profile=profile, context_digest=grant.digest,
                authorize=authority.live, authority_deadline=deadline,
                antenna_confirmed=authority.plan['antenna_confirmed'])
        now = clock()[1]
        ledger = Ledger(BudgetPolicy(240000000, 60000000, 120000000, 1000000, quantization_us=1),
                        authority.ledger_epoch, now)
        flow = BenchFlow(authority=authority, worker=worker, cipher=SessionCipher(grant, authority.local_peer, store),
            journal=journal, flow_key=authority.flow_key, contract=authority.contract,
            profile=authority.carrier_profile, ledger=ledger, scheduler_epoch=authority.scheduler_epoch,
            broker_epoch=authority.broker_epoch, sends=authority.sends, clock=clock)
        if public_service:
            from .services import ServiceTransfer
            from .service_runner import ServiceRunner
            runner = ServiceRunner(ServiceTransfer(flow), payload=payload)
            runner.start()
        elif payload is not None:
            flow.send(payload)
        complete_at = None
        while not result['stop_requested']:
            now = clock()[1]
            require(now / 1000000 < authority.deadlines.application_deadline_monotonic,
                    'bench_application_deadline')
            if (runner.step() if runner is not None else flow.step()):
                if complete_at is None:
                    complete_at = clock()[1]
                    result['flow_complete_monotonic_us'] = complete_at
                    if not authority.sends:
                        if runner is not None:
                            data = runner.public_payload
                            _quarantine(directory, data)
                            result['service_object_sha256'] = hashlib.sha256(data).hexdigest()
                            result['service_object_durable'] = True
                            result['application_status'] = 'public_block_quarantined'
                        else:
                            data = bytes(flow.received)
                            VerifiedChunks(object_directory).commit(authority.plan['object'], data)
                            result['verified_chunk_sha256'] = hashlib.sha256(data).hexdigest()
                            result['chunk_durable'] = True
                # Receiver stays available to re-ACK a lost final receipt.
                if authority.sends or clock()[1] - complete_at > 20000000:
                    result['complete'] = True
                    break
            time.sleep(.002 if profile.bandwidth_hz >= 250000 else .01)
        result['flow'] = flow.snapshot()
        if runner is not None:
            result['service_runner'] = runner.snapshot()
    except BaseException as error:
        result['error'] = type(error).__name__ + ':' + str(error)[:256]
        if flow is not None:
            result['flow'] = flow.snapshot()
    finally:
        def cleanup(name, action):
            try:
                action()
            except BaseException as error:
                result.setdefault('cleanup_errors', {})[name] = type(error).__name__
                result['complete'] = False
        if runner is not None:
            cleanup('service_runner', runner.close)
        elif flow is not None:
            cleanup('flow', flow.close)
        if worker is not None and worker.journal is not None:
            try:
                worker.disarm(); result['disarmed_on_exit'] = True
            except BaseException as error:
                result['disarmed_on_exit'] = False
                result['containment_error'] = type(error).__name__
                result['complete'] = False
        if store is not None:
            cleanup('retire_wire', store.retire)
            cleanup('close_wire', store.close)
        if journal is not None:
            cleanup('journal_snapshot', lambda: result.update(attempt_journal=journal.snapshot()))
            cleanup('journal_close', journal.close)
        if channel is not None: cleanup('radio_close', channel.close)
        if authority is not None: cleanup('authority_close', authority.close)
        for number, handler in saved_handlers.items(): signal.signal(number, handler)
        result['end_monotonic_us'] = clock()[1]
        usage = resource.getrusage(resource.RUSAGE_SELF)
        result['resource'] = {'user_cpu_seconds': usage.ru_utime, 'system_cpu_seconds': usage.ru_stime,
                              'max_rss_linux_kib': usage.ru_maxrss}
        result['services_after'] = roles(unit)
        fd = os.open(directory / 'result.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(result, output, indent=2); output.write('\n'); output.flush(); os.fsync(output.fileno())
    return result
