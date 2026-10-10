"""Explicit Linux-root, finite public MR1 TCP job; no action on import.

The operator provisions exact endpoints, peers, object and original deadlines.
This job opens one bounded connection, never discovers or enrolls peers, and
quarantines authenticated public bytes without claiming publisher verification.
Private messages require a separately qualified native provider and are refused.
"""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import threading
import time

from ..bench.node import PERSONAL, roles
from ..bench.service_runner import ServiceRunner
from ..bench.services import ServiceTransfer, _shape
from ..contracts.identity import PeerID
from .authority import IpAuthority
from .flow import IpFlow
from .journal import IpJournal, private
from .state import SessionStore
from .wire import require
from .worker import TcpWorker


def clock():
    return time.time_ns() // 1000, time.monotonic_ns() // 1000


def _facts(unit):
    states = roles(unit)  # Independently checks Linux/root and actual unit roles.
    names = PERSONAL if unit == 'unit-c' else PERSONAL[:3] + ('sage-lantern-mesh-runtime.service',)
    lifetimes = {}
    for name in names:
        result = subprocess.run(['/usr/bin/systemctl', 'show', name, '-p', 'MainPID',
            '-p', 'InvocationID', '-p', 'ExecMainStartTimestampMonotonic'],
            capture_output=True, text=True, timeout=5)
        require(result.returncode == 0, 'cannot_read_ip_service_lifetime')
        lifetimes[name] = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    return dict(unit=unit, host_boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                services=states, service_lifetimes=lifetimes)


def _read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        private(os.fstat(fd))
        raw = os.read(fd, maximum + 1)
        require(len(raw) <= maximum, 'ip_node_file_too_large')
        return raw
    finally:
        os.close(fd)


def _write(path, raw, *, directory_fd=None):
    own_directory = directory_fd is None
    if own_directory:
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        private(os.fstat(directory_fd), True)
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory_fd)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        finally:
            os.close(fd)
        os.fsync(directory_fd)
    finally:
        if own_directory:
            os.close(directory_fd)


def _connected(authority, stopped, directory, directory_fd):
    """One explicitly admitted setup attempt, checked under short held leases."""
    grant = authority.grant
    local = grant.initiator_address if authority.sends else grant.responder_address
    remote = grant.responder_address if authority.sends else grant.initiator_address
    require((':' in local[0]) == (':' in remote[0]), 'mixed_ip_address_families')
    bound = min(time.monotonic() + 5, authority.application_deadline_us / 1e6)
    channel = None
    try:
        with authority.application_lease():
            channel = socket.socket(socket.AF_INET6 if ':' in local[0] else socket.AF_INET, socket.SOCK_STREAM)
            channel.setblocking(False)
            channel.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            channel.bind(local)
            if authority.sends:
                error = channel.connect_ex(remote)
                require(error in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY), 'tcp_connect_refused')
            else:
                channel.listen(1)
                _write(directory / 'socket-ready.json', json.dumps(dict(
                    schema='meshticulum.ip-socket-ready.v1', grant_context_sha256=grant.digest.hex(),
                    status='listening', setup_deadline_monotonic=bound), sort_keys=True).encode() + b'\n',
                    directory_fd=directory_fd)
        while True:
            require(not stopped() and time.monotonic() < bound, 'ip_connection_setup_stopped_or_expired')
            with authority.application_lease():
                authority.require_dispatch()
                if authority.sends:
                    _, ready, errors = select.select([], [channel], [channel], max(0, min(.05, bound - time.monotonic())))
                    if ready or errors:
                        require(channel.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0, 'tcp_connect_failed')
                        require(channel.getsockname()[:2] == local and channel.getpeername()[:2] == remote,
                                'ip_setup_endpoint_mismatch')
                        result, channel = channel, None
                        return result
                else:
                    ready, _, _ = select.select([channel], [], [], max(0, min(.05, bound - time.monotonic())))
                    if ready:
                        accepted, _ = channel.accept()
                        try:
                            require(accepted.getsockname()[:2] == local and accepted.getpeername()[:2] == remote,
                                    'ip_setup_endpoint_mismatch')
                            authority.require_dispatch()
                        except BaseException:
                            accepted.close()
                            raise
                        return accepted
    finally:
        if channel is not None:
            channel.close()


def run_public_service(unit, directory):
    before = _facts(unit)
    directory = Path(directory).absolute()
    private(directory.lstat(), True)
    result = dict(schema='meshticulum.ip-public-node-run.v1', unit=unit,
        host_boot_id=before['host_boot_id'], facts_before=before, complete=False,
        application_data_channel='one explicitly provisioned TCP connection',
        publisher_verified=False, private_admission=False, stop_requested=False,
        start_epoch_us=clock()[0], start_monotonic_us=clock()[1],
        radio_opened=False, measured_airtime_us=None, kernel_retransmission_bytes=None)
    authority = journal = worker = store = flow = runner = connected = None
    directory_fd = None
    saved_handlers = {}

    def stop(_signum, _frame):
        result['stop_requested'] = True

    def owned():
        if result['stop_requested']:
            return False
        try:
            held = os.fstat(directory_fd); current = directory.lstat()
            private(held, True); private(current, True)
            if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                return False
            if worker is not None:
                return worker.owned()
            return journal is None or journal.owned()
        except (OSError, TypeError, ValueError):
            return False

    try:
        require(threading.current_thread() is threading.main_thread(), 'ip_node_requires_main_thread')
        for number in (signal.SIGTERM, signal.SIGINT):
            saved_handlers[number] = signal.signal(number, stop)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        # Peer input does not select this identity. It comes from the private
        # native provisioner's local plan, independently of socket ingress.
        native_plan = json.loads(_read(directory / 'grant.json', 16384))
        local_peer = PeerID(**native_plan['local_peer'])
        authority = IpAuthority(directory, local_peer=local_peer, host_boot_id=before['host_boot_id'],
                                clock=clock, owner_probe=owned)
        require(authority.service_format in ('MR1.request', 'MR1.response'),
                'private_ip_service_requires_qualified_native_provider')
        payload = _read(directory / 'sender.bin', authority.grant.limits.max_object_bytes) if authority.sends else None
        if payload is not None:
            require(len(payload) == authority.grant.object_bytes
                    and hashlib.sha256(payload).digest() == authority.grant.object_sha256,
                    'ip_sender_object_mismatch')
            _shape(payload, authority.service_format)
        else:
            require(not (directory / 'sender.bin').exists(), 'ip_receiver_has_management_payload')
        result.update(grant_context_sha256=authority.grant.digest.hex(),
            session_id=authority.grant.session.session_id.hex(), service_format=authority.service_format,
            sends=authority.sends, object=authority.plan['object'],
            original_application_expires_epoch_us=authority.application_expires_epoch_us,
            original_application_deadline_us=authority.application_deadline_us)
        journal = IpJournal(directory / 'bytes.sqlite', authority.grant, create=True)
        connected = _connected(authority, lambda: result['stop_requested'], directory, directory_fd)
        worker = TcpWorker(connected, authority.grant, local_peer, journal)
        store = SessionStore(directory / 'wire.sqlite', authority.grant, local_peer,
            boot_id=before['host_boot_id'], clock=clock,
            original_deadline_us=authority.application_deadline_us, create=True)
        flow = IpFlow(authority=authority, worker=worker, store=store)
        runner = ServiceRunner(ServiceTransfer(flow), payload=payload)
        runner.start()
        while not result['stop_requested']:
            require(clock()[1] < authority.application_deadline_us, 'ip_application_deadline')
            if runner.step():
                result['flow_complete_monotonic_us'] = clock()[1]
                if not authority.sends:
                    while not flow.finish_receive_ack():
                        require(not result['stop_requested'], 'ip_ack_finish_stopped')
                        time.sleep(.001)
                    data = runner.public_payload
                    with authority.application_lease():
                        _write(directory / 'receiver.bin', data, directory_fd=directory_fd)
                        result.update(service_object_sha256=hashlib.sha256(data).hexdigest(),
                                      service_object_durable=True, application_status='public_block_quarantined')
                result['complete'] = True
                break
            time.sleep(.001)
    except BaseException as error:
        result['error'] = type(error).__name__ + ':' + str(error)[:256]
    finally:
        def cleanup(name, action):
            try:
                action()
            except BaseException as error:
                result.setdefault('cleanup_errors', {})[name] = type(error).__name__
                result['complete'] = False
        if flow is not None:
            cleanup('flow_snapshot', lambda: result.update(flow=flow.snapshot()))
        if runner is not None:
            result['service_runner'] = runner.snapshot()
            cleanup('runner_close', runner.close)
        elif flow is not None:
            cleanup('flow_close', flow.close)
        if worker is not None:
            cleanup('socket_close', worker.close)
        if connected is not None:
            cleanup('original_socket_close', connected.close)
        if store is not None:
            cleanup('wire_retire', store.retire)
            cleanup('wire_close', store.close)
        if journal is not None:
            cleanup('journal_snapshot', lambda: result.update(byte_journal=journal.snapshot()))
            cleanup('journal_close', journal.close)
        if authority is not None:
            cleanup('authority_close', authority.close)
        for number, handler in saved_handlers.items():
            signal.signal(number, handler)
        result['end_monotonic_us'] = clock()[1]
        cleanup('facts_after', lambda: result.update(facts_after=_facts(unit)))
        if result.get('facts_after') != before:
            result['complete'] = False
            result['lifecycle_changed'] = True
        try:
            _write(directory / 'result.json', json.dumps(result, sort_keys=True, indent=2).encode() + b'\n',
                   directory_fd=directory_fd)
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', required=True, choices=('unit-a', 'unit-b', 'unit-c'))
    parser.add_argument('--grant-dir', required=True)
    args = parser.parse_args()
    result = run_public_service(args.unit, args.grant_dir)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
