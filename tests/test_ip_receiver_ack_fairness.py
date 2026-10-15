"""Real TCP receive work reaches the original ACK pump before queued retries."""
import pytest

from meshticulum.bench.services import ServiceTransfer
from meshticulum.flow.flow import build_ack_frame, build_data_frame
from meshticulum.ip.authority import revoke
from meshticulum.ip.wire import FrameKind, IpError, READY_MODE
from tests.test_ip_transport import pair
from tests.test_ip_receive_fairness import payload


@pytest.mark.parametrize('mode', [None, READY_MODE])
def test_completed_data_is_acked_before_queued_authenticated_retransmissions(tmp_path, mode):
    data = payload()
    with pair(tmp_path/'queued-retries', data, startup_mode=mode) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        b.step()
        a.step()
        clock.advance(600000)
        a.step()
        clock.advance(1100000)
        a.step()
        assert a.counts['tx_DATA'] == 3 and a.flow.retransmits == 2
        assert a.flow.in_flight() == 1 and a.flow.acked_frames() == 0
        deadlines = [f.authority.application_deadline_us for f in flows]
        assert b.step() is True
        # The old adapter consumes all three packets here before servicing ACK.
        assert b.counts['rx_DATA'] == 1
        assert b.received == data and not b.flow.delivered and not b.flow._ack_dirty
        assert b.counts['tx_ACK'] == 2 and a.step() is True
        assert a.flow.acked_frames() == 1 and a.flow.in_flight() == 0
        # The unread retransmissions stay available and remain authenticated,
        # replay-accounted and bounded by the unchanged frozen ACK rate.
        for expected in (2, 3):
            b.step()
            assert b.counts['rx_DATA'] == expected
        assert b.received == data
        assert stores[1]._db.execute('SELECT high FROM state').fetchone() == stores[0]._db.execute('SELECT tx FROM state').fetchone()
        assert [f.authority.application_deadline_us for f in flows] == deadlines
        assert all(w.journal.snapshot()['peer_receipt_established'] is False for w in workers)


def queue(sender, inner, kind, *, corrupt=False):
    with sender.authority.application_lease():
        packet = sender.cipher.seal(inner, kind, authorize=sender.authority.live)
        if corrupt:
            packet = packet[:-1] + bytes([packet[-1] ^ 1])
        sender.worker.transmit(packet, kind.name, sender.authority)


@pytest.mark.parametrize('damage', ['tag', 'inner-flow', 'no-work-ack'])
def test_refused_or_no_receive_work_does_not_yield_before_real_data(tmp_path, damage):
    data = payload()
    with pair(tmp_path/damage, data) as (flows, workers, stores, clock):
        a, b = flows
        wrong = build_data_frame(flow_id=2 if damage == 'inner-flow' else 1,
            generation=1, seq=0, total=1, last=True, payload=data)
        if damage == 'no-work-ack':
            wrong = build_ack_frame(flow_id=1, generation=1, ack_seq=0,
                                   credit=4, sack=0, grant_revision=1)
        queue(a, wrong, FrameKind.ACK if damage == 'no-work-ack' else FrameKind.DATA,
              corrupt=damage == 'tag')
        good = build_data_frame(flow_id=1, generation=1, seq=0, total=1,
                                last=True, payload=data)
        queue(a, good, FrameKind.DATA)
        assert b.step() is True
        assert b.received == data and b.counts['rx_DATA'] == 1
        assert b.counts['rx_refused'] == int(damage != 'no-work-ack')
        assert b.counts['rx_ACK'] == int(damage == 'no-work-ack')
        assert stores[1]._db.execute('SELECT high FROM state').fetchone() == (2,)
        assert workers[1].journal.snapshot()['lanes'] == {'ACK': 1}


@pytest.mark.parametrize('failure', ['expiry', 'revocation'])
@pytest.mark.parametrize('boundary', ['before_read', 'after_receive_work'])
def test_receive_progress_keeps_original_guard_before_ack(tmp_path, monkeypatch, failure, boundary):
    data = payload()
    with pair(tmp_path/'live', data) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(data)
        b.step()
        a.step()
        deadline = b.authority.application_deadline_us
        before = workers[1].journal.snapshot()
        observed = []

        def invalidate():
            if failure == 'expiry':
                clock.advance(60000000)
            elif boundary == 'before_read':
                revoke(b.authority.path)
            else:
                (b.authority.path/'active').write_bytes(b'retired\n')

        if boundary == 'after_receive_work':
            original = b.flow.absorb

            def absorb(frame, now):
                previous = b.flow._ack_work_gen
                original(frame, now)
                if b.flow._ack_work_gen != previous:
                    assert b.flow.delivered == data
                    observed.append(b.flow.delivered)
                    invalidate()

            monkeypatch.setattr(b.flow, 'absorb', absorb)
        else:
            invalidate()
        with pytest.raises(IpError):
            b.step()
        if boundary == 'after_receive_work':
            assert observed == [data]
        assert b.closed and b.counts['tx_ACK'] == 1
        assert b.authority.application_deadline_us == deadline
        after = workers[1].journal.snapshot()
        assert after['attempts'] == before['attempts']
        assert after['reserved_stream_bytes'] == before['reserved_stream_bytes']
