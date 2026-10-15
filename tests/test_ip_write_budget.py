"""Real TCP with deterministic local guard cost, not a network latency claim.

The authority and worker share one explicit source-fixture clock. No production
clock, application deadline, live guard, retry limit or socket budget is changed.
"""
import socket
import time
from types import SimpleNamespace

import pytest

import meshticulum.ip.worker as worker_module
from meshticulum.bench.services import resource
from meshticulum.ip.journal import IpJournal
from meshticulum.ip.wire import FrameKind, IpError, PREFIX, READY_MODE
from meshticulum.ip.worker import TcpWorker
import tests.test_ip_transport as transport_fixture


class GuardClock:
    def __init__(self):
        self.epoch = time.time_ns() // 1000
        self.mono = time.monotonic_ns() // 1000

    def __call__(self):
        return self.epoch, self.mono

    def advance(self, us):
        self.epoch += us
        self.mono += us


@pytest.fixture
def clocked_pair(monkeypatch):
    monkeypatch.setattr(transport_fixture, 'Clock', GuardClock)
    return transport_fixture.pair


def payload():
    return resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)


def local_clock(monkeypatch, clock):
    # Replace only this worker module's clock, not the process-wide time module.
    monkeypatch.setattr(worker_module, 'time', SimpleNamespace(monotonic=lambda: clock()[1] / 1e6))


def guarded_cost(monkeypatch, worker, clock, *, before=None, after=None):
    original = TcpWorker._guard
    calls = []

    def guard(current, authority):
        if current is not worker:
            return original(current, authority)
        calls.append(clock()[1])
        if before is not None:
            before(len(calls), authority)
        result = original(current, authority)
        if after is not None:
            after(len(calls), authority)
        return result

    monkeypatch.setattr(TcpWorker, '_guard', guard)
    return calls


def socket_calls(monkeypatch, worker, action=None):
    original = socket.socket.send
    calls = []

    def send(sock, data, *args, **kwargs):
        if sock is worker.sock:
            calls.append(len(data))
            if action is not None:
                return action(len(calls), original, sock, data, *args, **kwargs)
        return original(sock, data, *args, **kwargs)

    monkeypatch.setattr(socket.socket, 'send', send)
    return calls


def packet_for(flow, kind):
    plain = flow.authority.grant.digest if kind is FrameKind.READY else b'authenticated DATA fixture'
    return flow.cipher.seal(plain, kind, authorize=flow.authority.live), plain


@pytest.mark.parametrize('kind', [FrameKind.DATA, FrameKind.READY])
def test_write_budget_starts_after_first_fresh_prewrite_guard(tmp_path, monkeypatch, clocked_pair, kind):
    mode = READY_MODE if kind is FrameKind.READY else None
    with clocked_pair(tmp_path/'guard-cost', payload(), startup_mode=mode) as (flows, workers, stores, clock):
        a, b = flows
        worker = workers[0]
        local_clock(monkeypatch, clock)
        calls = guarded_cost(monkeypatch, worker, clock,
            before=lambda number, authority: clock.advance(300000) if number == 2 else None)
        sends = socket_calls(monkeypatch, worker)
        deadline = a.authority.application_deadline_us
        with a.authority.application_lease():
            packet, plain = packet_for(a, kind)
            assert worker.transmit(packet, kind.name, a.authority) is True
        assert len(calls) == 3 and len(sends) == 1
        assert a.authority.application_deadline_us == deadline and not worker.closed
        receipt = worker.journal.snapshot()
        assert receipt['attempts'] == 1 and receipt['outcomes'] == {'KERNEL_ACCEPTED': 1}
        assert receipt['reserved_stream_bytes'] == receipt['known_kernel_accepted_stream_bytes'] == len(packet) + PREFIX.size
        if kind is FrameKind.READY:
            assert receipt['reserved_stream_bytes'] == 83
        with b.authority.application_lease():
            received = workers[1].receive(b.authority)
            opened = b.cipher.open(received, authorize=b.authority.live)
        assert received == packet and opened.payload == plain and opened.kind is kind
        assert stores[1]._db.execute('SELECT high FROM state').fetchone() == (1,)


@pytest.mark.parametrize('damage', ['mono_expiry', 'epoch_expiry', 'revoked', 'owner_lost', 'plan_changed', 'active_replaced'])
def test_first_prewrite_guard_refuses_without_socket_effect_or_refund(tmp_path, monkeypatch, clocked_pair, damage):
    with clocked_pair(tmp_path/'refusal', payload()) as (flows, workers, stores, clock):
        a = flows[0]
        worker = workers[0]
        local_clock(monkeypatch, clock)

        def before(number, authority):
            if number != 2:
                return
            if damage == 'mono_expiry':
                clock.mono = authority.application_deadline_us
            elif damage == 'epoch_expiry':
                clock.epoch = authority.application_expires_epoch_us
            elif damage == 'revoked':
                (authority.path/'active').write_bytes(b'retired\n')
            elif damage == 'owner_lost':
                authority.owner_probe = lambda: False
            elif damage == 'plan_changed':
                (authority.path/'grant.json').write_bytes(b'{}')
            else:
                (authority.path/'active').unlink()
                (authority.path/'active').write_bytes(b'active\n')
                (authority.path/'active').chmod(0o600)

        calls = guarded_cost(monkeypatch, worker, clock, before=before)
        sends = socket_calls(monkeypatch, worker)
        with pytest.raises(IpError):
            with a.authority.application_lease():
                packet, _ = packet_for(a, FrameKind.DATA)
                worker.transmit(packet, 'DATA', a.authority)
        receipt = worker.journal.snapshot()
        assert len(calls) == 2 and not sends and worker.closed
        assert receipt['attempts'] == 1 and receipt['outcomes'] == {'NOT_SENT': 1}
        assert receipt['reserved_stream_bytes'] == len(packet) + PREFIX.size
        assert receipt['known_kernel_accepted_stream_bytes'] == 0


def test_original_application_deadline_still_caps_first_postguard_send(tmp_path, monkeypatch, clocked_pair):
    with clocked_pair(tmp_path/'after-guard-expiry', payload()) as (flows, workers, stores, clock):
        a = flows[0]
        worker = workers[0]
        local_clock(monkeypatch, clock)
        deadline = a.authority.application_deadline_us
        calls = guarded_cost(monkeypatch, worker, clock,
            after=lambda number, authority: setattr(clock, 'mono', deadline) if number == 2 else None)
        sends = socket_calls(monkeypatch, worker)
        with pytest.raises(IpError, match='tcp_write_timeout'):
            with a.authority.application_lease():
                packet, _ = packet_for(a, FrameKind.DATA)
                worker.transmit(packet, 'DATA', a.authority)
        assert len(calls) == 2 and not sends and worker.closed
        assert a.authority.application_deadline_us == deadline
        receipt = worker.journal.snapshot()
        assert receipt['outcomes'] == {'NOT_SENT': 1} and receipt['known_kernel_accepted_stream_bytes'] == 0
        assert receipt['reserved_stream_bytes'] == len(packet) + PREFIX.size


@pytest.mark.parametrize('first_write', ['partial', 'would_block', 'zero'])
def test_one_write_budget_is_not_refilled_after_any_socket_attempt(tmp_path, monkeypatch, clocked_pair, first_write):
    with clocked_pair(tmp_path/'one-budget', payload()) as (flows, workers, stores, clock):
        a = flows[0]
        worker = workers[0]
        local_clock(monkeypatch, clock)

        def before(number, authority):
            if number == 2:
                clock.advance(300000)
            elif number == 3:
                clock.advance(60000)

        calls = guarded_cost(monkeypatch, worker, clock, before=before)

        def action(number, original, sock, data, *args, **kwargs):
            assert number == 1
            clock.advance(200000)
            if first_write == 'partial':
                return original(sock, data[:3], *args, **kwargs)
            if first_write == 'would_block':
                raise BlockingIOError()
            return 0

        sends = socket_calls(monkeypatch, worker, action)
        # No real waiting is needed for the declared source-clock EAGAIN case.
        monkeypatch.setattr(worker_module, 'select', SimpleNamespace(select=lambda r, w, x, timeout: ([], [], [])))
        expected = 'tcp_peer_closed' if first_write == 'zero' else 'tcp_write_timeout'
        with pytest.raises(IpError, match=expected):
            with a.authority.application_lease():
                packet, _ = packet_for(a, FrameKind.DATA)
                worker.transmit(packet, 'DATA', a.authority)
        assert len(sends) == 1 and len(calls) == (2 if first_write == 'zero' else 3)
        receipt = worker.journal.snapshot()
        assert worker.closed and receipt['fenced'] and receipt['outcomes'] == {'UNKNOWN': 1}
        assert receipt['reserved_stream_bytes'] == len(packet) + PREFIX.size
        assert receipt['known_kernel_accepted_stream_bytes'] == (3 if first_write == 'partial' else 0)
        path, grant = worker.journal.path, worker.grant
        worker.journal.close()
        reopened = IpJournal(path, grant)
        try:
            assert reopened.snapshot() == receipt
            with pytest.raises(IpError, match='ip_journal_fenced'):
                reopened.reserve(b'no retry or refund', 'DATA')
        finally:
            reopened.close()


@pytest.mark.parametrize('damage', ['expiry', 'revoked', 'owner_lost'])
@pytest.mark.parametrize('write', ['partial', 'complete'])
def test_each_partial_write_and_final_settlement_keep_fresh_guard(tmp_path, monkeypatch, clocked_pair, damage, write):
    with clocked_pair(tmp_path/'postwrite-refusal', payload()) as (flows, workers, stores, clock):
        a = flows[0]
        worker = workers[0]
        local_clock(monkeypatch, clock)

        def action(number, original, sock, data, *args, **kwargs):
            assert number == 1
            sent = original(sock, data[:3] if write == 'partial' else data, *args, **kwargs)
            if damage == 'expiry':
                clock.mono = a.authority.application_deadline_us
            elif damage == 'revoked':
                (a.authority.path/'active').write_bytes(b'retired\n')
            else:
                a.authority.owner_probe = lambda: False
            return sent

        calls = guarded_cost(monkeypatch, worker, clock)
        sends = socket_calls(monkeypatch, worker, action)
        with pytest.raises(IpError):
            with a.authority.application_lease():
                packet, _ = packet_for(a, FrameKind.DATA)
                worker.transmit(packet, 'DATA', a.authority)
        assert len(calls) == 3 and len(sends) == 1
        receipt = worker.journal.snapshot()
        assert worker.closed and receipt['fenced'] and receipt['outcomes'] == {'UNKNOWN': 1}
        assert receipt['reserved_stream_bytes'] == len(packet) + PREFIX.size
        assert receipt['known_kernel_accepted_stream_bytes'] == (3 if write == 'partial' else len(packet) + PREFIX.size)
