"""Authenticated radio receipt queues yield to the existing guarded DATA pump.

The radio worker and clock are explicit source models. Cipher, frozen flow,
authority, scheduler and durable attempt/counter journals remain genuine.
"""
import pytest

from meshticulum.bench.authority import revoke
from meshticulum.bench.wire import FrameKind, WireError
from meshticulum.contracts.errors import ContractError
from meshticulum.flow.flow import build_ack_frame
from tests.test_meshticulum_bench_runtime import pair, finish


def queue_ack(receiver, *, credit, revision, ack_seq=0, corrupt=False):
    inner = build_ack_frame(flow_id=receiver.flow.flow_id, generation=1,
        ack_seq=ack_seq, credit=credit, sack=0, grant_revision=revision)
    packet = receiver.cipher.seal(inner, FrameKind.ACK,
                                  authorize=receiver.authority.live)
    if corrupt:
        packet = packet[:-1] + bytes([packet[-1] ^ 1])
    receiver.worker.transmit(packet)


def test_first_accepted_receipt_sends_before_queued_startup_repeats(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data)
    deadlines = [f.authority.application_deadline_us for f in (sender, receiver)]
    receiver.step()
    for _ in range(3):
        clock.advance(int(receiver.flow.limits.persist_interval_s * 1000000) + 1)
        receiver.step()
    assert len(workers[0].incoming) == 4 and sender.counts['rx_ACK'] == 0
    assert sender.step() is False
    assert sender.counts['rx_ACK'] == 1 and len(workers[0].sent) == 1
    assert len(workers[0].incoming) == 3 and len(workers[1].incoming) == 1
    assert sender.flow.in_flight() == 1 and sender.flow._peer_credit == 4
    clock.advance(1)  # A new journal counter sample needs a later modeled time.
    finish(pair)
    assert bytes(receiver.received) == data
    assert sender.flow.failure is receiver.flow.failure is None
    assert [f.authority.application_deadline_us for f in (sender, receiver)] == deadlines


@pytest.mark.parametrize('pair', ['sf7-500'], indirect=True)
def test_progress_receipt_sends_next_window_before_queued_repeats(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data)
    receiver.step()
    sender.step()
    # Keep DATA at the modeled radio boundary until the complete first window
    # is handed over; the sender may not infer peer credit or receipt.
    for _ in range(3):
        clock.advance(1)
        sender.step()
    assert len(workers[0].sent) == sender.flow.in_flight() == 4
    assert sender.flow.queued_bytes() > 0
    receiver.step()
    for _ in range(3):
        clock.advance(int(receiver.flow.limits.persist_interval_s * 1000000) + 1)
        receiver.step()
    assert len(workers[0].incoming) == 3
    assert sender.step() is False
    assert sender.counts['rx_ACK'] == 2 and len(workers[0].sent) == 5
    assert sender.flow.acked_frames() == 4 and sender.flow.in_flight() == 1
    assert len(workers[0].incoming) == 2


def test_zero_stale_impossible_and_corrupt_receipts_do_not_open_credit(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data)
    queue_ack(receiver, credit=0, revision=2)
    queue_ack(receiver, credit=4, revision=1)
    queue_ack(receiver, credit=4, revision=3, ack_seq=2)
    queue_ack(receiver, credit=4, revision=3, corrupt=True)
    assert sender.step() is False
    assert sender.counts['rx_ACK'] == 3 and sender.counts['rx_refused'] == 1
    assert sender.flow._peer_credit == 0 and not workers[0].sent
    assert sender.flow.stale_grants == sender.flow.impossible_acks == 1
    assert sender.flow._peer_grant_rev == 2 and not workers[0].incoming
    queue_ack(receiver, credit=4, revision=3)
    assert sender.step() is False
    assert len(workers[0].sent) == 1 and sender.flow._peer_credit == 4


@pytest.mark.parametrize('failure', ['expiry', 'revocation', 'owner'])
@pytest.mark.parametrize('boundary', ['before_read', 'after_credit'])
def test_receipt_yield_keeps_original_live_dispatch_guards(pair, monkeypatch, failure, boundary):
    data, clock, (sender, receiver), workers = pair
    sender.send(data)
    queue_ack(receiver, credit=4, revision=1)
    deadline = sender.authority.application_deadline_us
    before = workers[0].journal.snapshot()

    def invalidate():
        if failure == 'expiry':
            clock.advance(deadline - clock()[1])
        elif failure == 'revocation':
            revoke(sender.authority.path)
        else:
            workers[0].owned = False

    if boundary == 'after_credit':
        original = sender.flow.absorb

        def absorb(frame, now):
            original(frame, now)
            assert sender.flow._peer_credit == 4
            invalidate()

        monkeypatch.setattr(sender.flow, 'absorb', absorb)
    else:
        invalidate()
    try:
        sender.step()
    except (WireError, ContractError):
        pass
    assert not workers[0].sent
    assert sender.authority.application_deadline_us == deadline
    after = workers[0].journal.snapshot()
    assert after['attempts'] == before['attempts']
    assert after['reserved_estimated_airtime_us'] == before['reserved_estimated_airtime_us']
