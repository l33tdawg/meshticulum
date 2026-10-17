"""Real TCP final-ACK drain; no fd, revocation, deadline or byte-budget waiver."""
import os
import ctypes
import socket
import struct
import time
from types import SimpleNamespace

import pytest

from meshticulum.bench.services import ServiceTransfer, resource
from meshticulum.ip.authority import revoke
from meshticulum.ip.wire import IpError, IpLimits
from meshticulum.ip import socket_identity as native_identity
from tests.test_ip_transport import connected, pair


def payload():
    return resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)


def final_ack_with_unread_retry(flows, workers, clock):
    """The real frozen sender retransmits before it reads the receiver's ACK."""
    ServiceTransfer(flows[0]).send(payload())
    for _ in range(500):
        flows[0].step(); flows[1].step()
        if flows[1].flow.complete:
            break
        time.sleep(.001)
    assert flows[1].flow.complete and not flows[0].flow.complete
    assert not workers[1].buffer and workers[1].expected is None
    initial_rx = workers[1].journal.snapshot()['received_stream_bytes']
    before = workers[0].journal.snapshot()['known_kernel_accepted_stream_bytes']
    clock.advance(600000)
    # Deliberately postpone sender ingress: this is a genuine ReliableFlow
    # retransmission, cipher seal, guarded kernel send and journal settlement.
    with flows[0].authority.application_lease():
        flows[0]._pump()
    assert flows[0].flow.retransmits == 1
    retry_bytes = workers[0].journal.snapshot()['known_kernel_accepted_stream_bytes'] - before
    assert retry_bytes > 0
    for _ in range(500):
        if flows[1].finish_receive_ack():
            break
        time.sleep(.001)
    assert not flows[1].flow._ack_dirty
    assert workers[1].journal.snapshot()['received_stream_bytes'] == initial_rx
    return initial_rx, retry_bytes


def test_final_ack_survives_unread_genuine_duplicate_data(tmp_path):
    with pair(tmp_path / 'duplicate', payload()) as (flows, workers, stores, clock):
        deadlines = [(f.authority.application_deadline_us,
                      f.authority.application_expires_epoch_us) for f in flows]
        initial_rx, retry_bytes = final_ack_with_unread_retry(flows, workers, clock)
        ack_attempts = workers[1].journal.snapshot()['attempts']
        assert flows[1].finish_stream() is False
        assert workers[1].write_shutdown and not workers[1].receive_eof
        assert workers[1].owned() and workers[0].owned()
        receipt = workers[1].journal.snapshot()
        assert receipt['received_stream_bytes'] == initial_rx + retry_bytes
        assert receipt['attempts'] == ack_attempts
        assert receipt['pending_receive_stream_bytes'] == 0
        assert not receipt['peer_receipt_established'] and not receipt['fenced']
        # The sender still performs its original authenticated ACK processing,
        # guarded terminal dispatch and post-lease ownership check.
        assert flows[0].step() is True
        assert flows[0].flow.complete and not flows[0].closed
        assert not workers[0].journal.snapshot()['fenced']
        for _ in range(500):
            sender_eof = flows[0].finish_stream()
            receiver_eof = flows[1].finish_stream()
            if sender_eof and receiver_eof:
                break
            time.sleep(.001)
        assert workers[0].receive_eof and workers[0].owned()
        assert workers[1].receive_eof and workers[1].owned()
        workers[0].close()
        assert workers[1].owned()
        assert flows[1].finish_stream() is True
        assert workers[1].journal.snapshot()['received_stream_bytes'] == initial_rx + retry_bytes
        assert [(f.authority.application_deadline_us,
                 f.authority.application_expires_epoch_us) for f in flows] == deadlines


def test_drain_is_nonblocking_and_normal_packet_ingress_stays_closed(tmp_path):
    with pair(tmp_path / 'nonblocking', payload()) as (flows, workers, stores, clock):
        final_ack_with_unread_retry(flows, workers, clock)
        assert flows[1].finish_stream() is False
        before = workers[1].journal.snapshot()
        assert flows[1].finish_stream() is False
        assert workers[1].journal.snapshot() == before
        with flows[1].authority.application_lease():
            with pytest.raises(IpError, match='tcp_terminal_drain_started'):
                workers[1].receive(flows[1].authority)
            with pytest.raises(IpError, match='tcp_terminal_drain_started'):
                workers[1].transmit(b'not-dispatched', 'ACK', flows[1].authority)
        with pytest.raises(IpError, match='ip_flow_closed'):
            flows[1].step()
        with pytest.raises(IpError, match='ip_receive_not_complete'):
            flows[1].finish_receive_ack()
        assert workers[1].journal.snapshot() == before


@pytest.mark.parametrize('change', ['deadline', 'active', 'native_owner', 'socket_fd'])
def test_each_drain_call_retains_original_liveness_checks(tmp_path, change):
    with pair(tmp_path / change, payload()) as (flows, workers, stores, clock):
        final_ack_with_unread_retry(flows, workers, clock)
        assert flows[1].finish_stream() is False
        worker, authority = workers[1], flows[1].authority
        before = worker.journal.snapshot()
        backup = None
        replacement = ()
        try:
            if change == 'deadline':
                clock.advance(60000000)
            elif change == 'active':
                revoke(authority.path)
            elif change == 'native_owner':
                authority.owner_probe = lambda: False
            else:
                replacement = connected()
                backup = os.dup(worker.sock.fileno())
                os.dup2(replacement[1].fileno(), worker.sock.fileno())
                assert worker.sock.getpeername()[:2] != worker.remote
                assert not worker.owned()
            with pytest.raises(IpError, match='ip_authority_not_current'):
                flows[1].finish_stream()
            assert worker.journal.snapshot() == before
        finally:
            if backup is not None:
                os.dup2(backup, worker.sock.fileno()); os.close(backup)
            for sock in replacement:
                sock.close()


@pytest.mark.parametrize('change', ['active', 'native_owner'])
def test_receive_reservation_cannot_bypass_fresh_dispatch_check(tmp_path, monkeypatch, change):
    with pair(tmp_path / ('reserve-' + change), payload()) as (flows, workers, stores, clock):
        final_ack_with_unread_retry(flows, workers, clock)
        worker, authority = workers[1], flows[1].authority
        reserve = worker.journal.reserve_receive
        calls = []
        recv = socket.socket.recv
        def retire_after_reservation(maximum):
            result = reserve(maximum)
            if change == 'active':
                fd = os.open(authority.path / 'active', os.O_WRONLY)
                try:os.pwrite(fd, b'retired\n', 0)
                finally:os.close(fd)
            else:
                authority.owner_probe = lambda: False
            return result
        def observed_recv(channel, *args, **kwargs):
            calls.append(channel.fileno())
            return recv(channel, *args, **kwargs)
        monkeypatch.setattr(worker.journal, 'reserve_receive', retire_after_reservation)
        monkeypatch.setattr(socket.socket, 'recv', observed_recv)
        before = worker.journal.snapshot()['received_stream_bytes']
        with pytest.raises(IpError, match='ip_dispatch_lease_not_held'):
            flows[1].finish_stream()
        assert not calls
        receipt = worker.journal.snapshot()
        assert receipt['received_stream_bytes'] == before
        assert receipt['pending_receive_stream_bytes'] > 0
        assert receipt['fenced'] and worker.closed


@pytest.mark.parametrize('change', ['active', 'native_owner'])
def test_terminal_read_keeps_post_kernel_revocation_check(tmp_path, monkeypatch, change):
    with pair(tmp_path / ('post-' + change), payload()) as (flows, workers, stores, clock):
        initial_rx, retry_bytes = final_ack_with_unread_retry(flows, workers, clock)
        worker, authority = workers[1], flows[1].authority
        original = worker.journal.settle_receive
        def retire_after_charged_read(size):
            original(size)
            if size:
                if change == 'active':
                    # An in-process native revocation fault after the syscall;
                    # ordinary revoke() correctly waits for this held lease.
                    fd = os.open(authority.path / 'active', os.O_WRONLY)
                    try:os.pwrite(fd, b'retired\n', 0)
                    finally:os.close(fd)
                else:
                    authority.owner_probe = lambda: False
        monkeypatch.setattr(worker.journal, 'settle_receive', retire_after_charged_read)
        with pytest.raises(IpError, match='ip_dispatch_lease_not_held'):
            flows[1].finish_stream()
        receipt = worker.journal.snapshot()
        assert receipt['received_stream_bytes'] == initial_rx + retry_bytes
        assert receipt['pending_receive_stream_bytes'] == 0
        assert receipt['fenced'] and worker.closed


def test_terminal_drain_never_exceeds_original_receive_byte_budget(tmp_path):
    limits = IpLimits(max_rx_stream_bytes=512)
    with pair(tmp_path / 'budget', payload(), limits=limits) as (flows, workers, stores, clock):
        final_ack_with_unread_retry(flows, workers, clock)
        # Adversarial trailing stream data is discarded, never authenticated or
        # accepted. Only this raw peer stimulus bypasses the legitimate sender.
        assert workers[0].sock.send(b'x' * 1024) == 1024
        with pytest.raises(IpError, match='ip_rx_budget_exhausted'):
            for _ in range(500):
                assert flows[1].finish_stream() is False
        receipt = workers[1].journal.snapshot()
        assert receipt['received_stream_bytes'] == 512
        assert receipt['pending_receive_stream_bytes'] == 0
        assert receipt['fenced'] and workers[1].closed


def test_terminal_drain_requires_received_complete_object_and_final_ack(tmp_path):
    with pair(tmp_path / 'boundary', payload()) as (flows, workers, stores, clock):
        for flow in flows:
            with pytest.raises(IpError, match='ip_flow_not_complete'):
                flow.finish_stream()
        assert not any(worker.write_shutdown for worker in workers)
        assert all(worker.journal.snapshot()['received_stream_bytes'] == 0 for worker in workers)


@pytest.mark.parametrize('corruption', ['size', 'socket', 'pcb', 'generation', 'kind', 'family'])
def test_unknown_darwin_socket_identity_fails_closed(monkeypatch, corruption):
    # Negative ABI controls complement the real kernel identity/FD-replacement
    # observations; these modeled bytes never establish a native receipt.
    monkeypatch.setattr(native_identity, 'sys', SimpleNamespace(platform='darwin', byteorder='little'))
    raw = bytearray(792)
    struct.pack_into('<QQiii', raw, 160, 11, 22, socket.SOCK_STREAM, socket.IPPROTO_TCP, socket.AF_INET)
    struct.pack_into('<i', raw, 256, 2)
    struct.pack_into('<Q', raw, 272, 33)
    if corruption != 'size':
        offset, format = {'socket': (160, '<Q'), 'pcb': (168, '<Q'),
                          'generation': (272, '<Q'), 'kind': (256, '<i'),
                          'family': (184, '<i')}[corruption]
        struct.pack_into(format, raw, offset, 0)
    def reader(pid, fd, flavor, target, size):
        assert pid == os.getpid() and fd == 101 and flavor == 3 and size == 792
        ctypes.memmove(target, bytes(raw), len(raw))
        return 791 if corruption == 'size' else 792
    monkeypatch.setattr(native_identity, '_darwin_reader', lambda: reader)
    channel = SimpleNamespace(fileno=lambda: 101, family=socket.AF_INET)
    with pytest.raises(OSError, match='kernel_socket_identity_unavailable'):
        native_identity.socket_identity(channel, SimpleNamespace(st_dev=0, st_ino=0))
