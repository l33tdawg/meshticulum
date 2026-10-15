"""Real TCP handovers use dispatch return time, not the earlier pump instant.

The explicit fixture clock advances model synchronous guard/dispatch work;
they are deterministic accounting witnesses, not measured network latency.
"""
import socket

import pytest

from meshticulum.bench.services import ServiceTransfer
from meshticulum.ip.wire import IpError, IpLimits, READY_MODE
from meshticulum.ip.worker import TcpWorker
from tests.test_ip_transport import pair
from tests.test_ip_receive_fairness import payload, multi_window_payload, queue_ack


def dispatch_clock(monkeypatch, worker, clock, lane, delay_us=600000):
    original = TcpWorker.transmit
    accepted = []

    def transmit(current, packet, current_lane, authority):
        if current is worker and current_lane == lane:
            clock.advance(delay_us)
        result = original(current, packet, current_lane, authority)
        if current is worker and current_lane == lane:
            accepted.append(clock()[1] / 1e6)
        return result

    monkeypatch.setattr(TcpWorker, 'transmit', transmit)
    return accepted


@pytest.mark.parametrize('mode', [None, READY_MODE])
def test_fresh_data_starts_rto_after_real_guarded_kernel_handover(tmp_path, monkeypatch, mode):
    data = payload()
    with pair(tmp_path/'fresh', data, startup_mode=mode) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        b.step()
        deadline = a.authority.application_deadline_us
        accepted = dispatch_clock(monkeypatch, workers[0], clock, 'DATA')
        a.step()
        flight = a.flow._in_flight[0]
        assert len(accepted) == 1
        assert accepted[0] <= flight.handed_over_at <= clock()[1] / 1e6
        assert flight.sent_at == flight.handed_over_at and flight.attempts == 1
        assert flight.rto == a.flow.limits.initial_rto_s == .5
        # No elapsed RTO follows the actual return, despite .6s of dispatch work.
        assert a.step() is False
        assert a.counts['tx_DATA'] == 1 and a.flow.retransmits == 0
        assert a.authority.application_deadline_us == deadline
        assert workers[0].journal.snapshot()['lanes']['DATA'] == 1
        assert b.step() is True
        clock.advance(10000)
        assert b.finish_receive_ack() is True and a.step() is True
        assert b.received == data and a.flow.acked_frames() == 1


def test_retry_handover_spends_one_attempt_at_actual_dispatch_return(tmp_path, monkeypatch):
    data = payload()
    with pair(tmp_path/'retry', data) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        b.step()
        a.step()
        clock.advance(600000)
        accepted = dispatch_clock(monkeypatch, workers[0], clock, 'DATA')
        a.step()
        flight = a.flow._in_flight[0]
        assert len(accepted) == 1 and accepted[0] <= flight.handed_over_at
        assert flight.sent_at == flight.handed_over_at
        assert flight.attempts == 2 and a.flow.retransmits == 1 and flight.rto == 1
        a.step()
        assert a.counts['tx_DATA'] == 2 and a.flow.retransmits == 1
        assert workers[0].journal.snapshot()['lanes']['DATA'] == 2
        assert b.step() is True
        clock.advance(10000)
        assert b.finish_receive_ack() is True and a.step() is True


def test_four_frame_opportunity_keeps_individual_handover_times(tmp_path, monkeypatch):
    data = multi_window_payload()
    limits = IpLimits(max_plaintext=256, window_frames=4)
    with pair(tmp_path/'window', data, 'MR1.response', limits) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        b.step()
        accepted = dispatch_clock(monkeypatch, workers[0], clock, 'DATA', 50000)
        a.step()
        assert len(accepted) == a.counts['tx_DATA'] == a.flow.in_flight() == 4
        flights = list(a.flow._in_flight.values())
        assert [f.seq for f in flights] == [0, 1, 2, 3]
        assert all(at <= f.handed_over_at for at, f in zip(accepted, flights))
        assert all(a.handed_over_at < b.handed_over_at for a, b in zip(flights, flights[1:]))
        assert all(f.attempts == 1 and f.rto == .5 for f in flights)
        assert a.flow.queued_bytes() > 0 and a.flow._next_seq == 4
        a.step()
        assert a.counts['tx_DATA'] == 4 and a.flow.retransmits == 0
        assert workers[0].journal.snapshot()['lanes']['DATA'] == 4


@pytest.mark.parametrize('mode', [None, READY_MODE])
def test_ack_persist_interval_starts_after_actual_handover(tmp_path, monkeypatch, mode):
    data = payload()
    with pair(tmp_path/'ack', data, startup_mode=mode) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        accepted = dispatch_clock(monkeypatch, workers[1], clock, 'ACK')
        deadline = b.authority.application_deadline_us
        b.step()
        assert len(accepted) == 1 and accepted[0] <= b.flow._last_ack_at
        assert b.flow._startup_announce_left == 7 and not b.flow._ack_dirty
        b.step()
        assert b.counts['tx_ACK'] == 1 and b.flow._startup_announce_left == 7
        assert b.authority.application_deadline_us == deadline
        clock.advance(500000)
        b.step()
        assert len(accepted) == b.counts['tx_ACK'] == 2
        assert accepted[-1] <= b.flow._last_ack_at
        assert workers[1].journal.snapshot()['lanes']['ACK'] == 2


def test_credit_probe_handover_is_a_real_bounded_attempt_not_new_data(tmp_path, monkeypatch):
    data = multi_window_payload()
    with pair(tmp_path/'probe', data, 'MR1.response', IpLimits(max_plaintext=256)) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        queue_ack(b, credit=1, revision=1)
        a.step()
        assert a.flow.in_flight() == 1 and a.flow._next_seq == 1
        queue_ack(b, credit=0, revision=2, ack_seq=1)
        a.step()
        assert not a.flow.in_flight() and a.flow.acked_frames() == 1
        clock.advance(600000)
        accepted = dispatch_clock(monkeypatch, workers[0], clock, 'DATA')
        a.step()
        assert len(accepted) == 1 and a.flow.zero_window_probes == 1
        assert a.flow._probe_attempts == 1 and a.flow._probe_at > accepted[0]
        assert not a.flow._probe_outstanding and not a.flow.in_flight()
        assert a.flow._next_seq == 1 and a.flow.queued_bytes() > 0
        assert a.flow.retransmits == 0 and a.counts['tx_DATA'] == 2
        a.step()
        assert a.flow.zero_window_probes == 1 and a.counts['tx_DATA'] == 2
        assert workers[0].journal.snapshot()['lanes']['DATA'] == 2


@pytest.mark.parametrize('damage', ['expiry', 'revocation'])
@pytest.mark.parametrize('boundary', ['before_write', 'after_write'])
@pytest.mark.parametrize('lane', ['DATA', 'ACK'])
def test_handover_never_bypasses_original_live_guards(tmp_path, monkeypatch, damage, boundary, lane):
    data = payload()
    with pair(tmp_path/'guard', data) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        if lane == 'DATA':
            b.step()
        target = a if lane == 'DATA' else b
        worker = workers[0 if lane == 'DATA' else 1]
        deadline = target.authority.application_deadline_us
        before_dispatch = []
        original = TcpWorker.transmit

        def invalidate():
            if damage == 'expiry':
                clock.advance(60000000)
            else:
                (target.authority.path/'active').write_bytes(b'retired\n')

        def transmit(current, packet, current_lane, authority):
            if current is worker and current_lane == lane:
                before_dispatch.append(worker.journal.snapshot())
            if current is worker and current_lane == lane and boundary == 'before_write':
                invalidate()
            result = original(current, packet, current_lane, authority)
            if current is worker and current_lane == lane and boundary == 'after_write':
                invalidate()
            return result

        monkeypatch.setattr(TcpWorker, 'transmit', transmit)
        with pytest.raises(IpError):
            target.step()
        assert target.closed and target.authority.application_deadline_us == deadline
        assert len(before_dispatch) == 1
        before = before_dispatch[0]
        after = worker.journal.snapshot()
        count = int(boundary == 'after_write')
        assert after['attempts'] == before['attempts'] + count
        assert target.counts['tx_' + lane] == count
        if count:
            assert after['reserved_stream_bytes'] > before['reserved_stream_bytes']
            assert after['outcomes']['KERNEL_ACCEPTED'] == before['outcomes'].get('KERNEL_ACCEPTED', 0) + count
        else:
            assert after['reserved_stream_bytes'] == before['reserved_stream_bytes']


def test_partial_write_remains_unknown_without_successful_handover(tmp_path, monkeypatch):
    data = payload()
    with pair(tmp_path/'unknown', data) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        b.step()
        original = socket.socket.send
        original_transmit = TcpWorker.transmit
        calls = []
        before_dispatch = []

        def transmit(worker, packet, lane, authority):
            if worker is workers[0] and lane == 'DATA':
                before_dispatch.append(worker.journal.snapshot())
            return original_transmit(worker, packet, lane, authority)

        def send(sock, value, *args, **kwargs):
            if sock is workers[0].sock and before_dispatch:
                calls.append(True)
                if len(calls) == 1:
                    return original(sock, value[:3], *args, **kwargs)
                raise OSError('declared source partial write')
            return original(sock, value, *args, **kwargs)

        monkeypatch.setattr(socket.socket, 'send', send)
        monkeypatch.setattr(TcpWorker, 'transmit', transmit)
        with pytest.raises(OSError):
            a.step()
        assert a.closed and workers[0].closed and not a.flow._in_flight
        assert a.counts['tx_DATA'] == 0
        assert len(before_dispatch) == 1
        before = before_dispatch[0]
        record = workers[0].journal.snapshot()
        assert record['outcomes']['UNKNOWN'] == 1 and record['fenced']
        assert record['outcomes'].get('KERNEL_ACCEPTED', 0) == before['outcomes'].get('KERNEL_ACCEPTED', 0)
        assert record['known_kernel_accepted_stream_bytes'] == before['known_kernel_accepted_stream_bytes'] + 3
        assert record['reserved_stream_bytes'] > before['reserved_stream_bytes'] + 3
        assert workers[0].journal.db.execute("SELECT state,written FROM attempts WHERE lane='DATA'").fetchall() == [('UNKNOWN', 3)]
