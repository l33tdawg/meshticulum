"""Real TCP grant queues yield to DATA without changing authority or clocks."""
import select

import pytest

from meshticulum.bench.services import ServiceTransfer, resource
from meshticulum.flow.flow import build_ack_frame
from meshticulum.ip.authority import revoke
from meshticulum.ip.wire import FrameKind, IpError, READY_MODE
from tests.test_ip_transport import pair


def payload():
    return resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)


def queue_ack(receiver, *, credit, revision, ack_seq=0, corrupt=False):
    """An admitted peer sends a real authenticated or deliberately bad ACK."""
    inner = build_ack_frame(flow_id=1, generation=1, ack_seq=ack_seq,
                            credit=credit, sack=0, grant_revision=revision)
    with receiver.authority.application_lease():
        packet = receiver.cipher.seal(inner, FrameKind.ACK,
                                      authorize=receiver.authority.live)
        if corrupt:
            packet = packet[:-1] + bytes([packet[-1] ^ 1])
        receiver.worker.transmit(packet, 'ACK', receiver.authority)


@pytest.mark.parametrize('mode', [None, READY_MODE])
def test_first_accepted_credit_sends_before_queued_startup_repeats(tmp_path, mode):
    with pair(tmp_path/'queued', payload(), startup_mode=mode) as (flows, workers, stores, clock):
        a, b = flows
        source, target = ServiceTransfer(a), ServiceTransfer(b)
        source.send(payload())
        deadlines = [flow.authority.application_deadline_us for flow in flows]
        b.step()
        for _ in range(3):
            clock.advance(500000)
            b.step()
        assert b.counts['tx_ACK'] == 4 and a.counts['rx_ACK'] == 0
        assert a.step() is False
        assert a.counts['rx_ACK'] == 1 and a.counts['tx_DATA'] == 1
        assert stores[0]._db.execute('SELECT high FROM state').fetchone() == (1,)
        assert select.select([workers[0].sock], [], [], 0)[0]
        assert b.step() is True
        clock.advance(10000)
        assert b.finish_receive_ack() is True
        assert a.step() is True
        assert a.counts['rx_ACK'] == 5
        assert target.received() == payload()
        assert [flow.authority.application_deadline_us for flow in flows] == deadlines
        assert workers[1].journal.snapshot()['lanes']['ACK'] == 5


def test_zero_stale_impossible_and_corrupt_ack_do_not_open_or_yield_credit(tmp_path):
    with pair(tmp_path/'refused', payload()) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(payload())
        queue_ack(b, credit=0, revision=2)
        queue_ack(b, credit=4, revision=1)
        queue_ack(b, credit=4, revision=3, ack_seq=2)
        queue_ack(b, credit=4, revision=3, corrupt=True)
        assert a.step() is False
        assert a.counts['rx_ACK'] == 3 and a.counts['rx_refused'] == 1
        assert a.flow._peer_credit == 0 and a.counts['tx_DATA'] == 0
        assert a.flow.stale_grants == 1 and a.flow.impossible_acks == 1
        assert a.flow._peer_grant_rev == 2
        assert not select.select([workers[0].sock], [], [], 0)[0]
        assert workers[0].journal.snapshot()['lanes'].get('DATA', 0) == 0
        queue_ack(b, credit=4, revision=3)
        assert a.step() is False
        assert a.flow._peer_credit == 4 and a.counts['tx_DATA'] == 1


@pytest.mark.parametrize('failure', ['expiry', 'revocation'])
@pytest.mark.parametrize('boundary', ['before_read', 'after_credit'])
def test_opening_credit_never_skips_original_live_guard(tmp_path, monkeypatch, failure, boundary):
    with pair(tmp_path/'live', payload()) as (flows, workers, stores, clock):
        a, b = flows
        ServiceTransfer(a).send(payload())
        queue_ack(b, credit=4, revision=1)
        deadline = a.authority.application_deadline_us
        before = workers[0].journal.snapshot()

        def invalidate():
            if failure == 'expiry':
                clock.advance(60000000)
            elif boundary == 'before_read':
                revoke(a.authority.path)
            else:
                # Deliberate mutation while the shared lease is held must be
                # caught by the existing fresh read before the next effect.
                (a.authority.path/'active').write_bytes(b'retired\n')

        if boundary == 'after_credit':
            original = a.flow.absorb

            def absorb(frame, now):
                original(frame, now)
                assert a.flow._peer_credit == 4
                invalidate()

            monkeypatch.setattr(a.flow, 'absorb', absorb)
        else:
            invalidate()
        with pytest.raises(IpError):
            a.step()
        assert a.closed and a.counts['tx_DATA'] == 0
        assert a.authority.application_deadline_us == deadline
        after = workers[0].journal.snapshot()
        assert after['attempts'] == before['attempts']
        assert after['reserved_stream_bytes'] == before['reserved_stream_bytes']
