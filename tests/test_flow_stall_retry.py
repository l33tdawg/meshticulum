"""Explicit one-SACK recovery for a saturated logical DATA window."""

import pytest

from meshticulum.flow.flow import DEFERRED, FlowError, FlowLimits, ReliableFlow, StartupGrant
from tests.test_flow_selective_fast_retry import Link, ack


def sender(*, enabled=True, frames=8, window=4, max_retries=4):
    link = Link()
    limits = FlowLimits(
        frame_payload_bytes=4,
        window_frames=window,
        initial_credit_frames=window,
        max_reassembly_frames=window,
        seq_modulus=65536,
        max_total_frames=64,
        max_queued_bytes=512,
        max_delivered_bytes=512,
        max_retries=max_retries,
        selective_fast_retry=True,
        selective_stall_retry=enabled,
    )
    flow = ReliableFlow('a', link, limits, startup=StartupGrant(window))
    flow.send(bytes(index % 251 for index in range(frames * 4)))
    flow.pump(0, max_frames=window, drain=False)
    return flow, link


def blocked_window(*, enabled=True, frames=8, window=4, max_retries=4):
    flow, link = sender(
        enabled=enabled,
        frames=frames,
        window=window,
        max_retries=max_retries,
    )
    ack(flow, positions=(1,), revision=2, credit=window, at=.1)
    assert flow._in_flight[0].selective_evidence == {1}
    assert not flow.selective_retry_ready()
    flow.pump(.11, max_frames=1, drain=False)
    assert flow.queued_bytes() > 0
    assert flow.in_flight() == min(window, flow._peer_credit)
    assert flow.selective_stall_ready() is enabled
    return flow, link


@pytest.mark.parametrize('value', [0, 1, None, 'true', [], {}])
def test_stall_option_is_a_strict_boolean(value):
    with pytest.raises(FlowError, match='selective_stall_retry'):
        FlowLimits(selective_fast_retry=True, selective_stall_retry=value)


def test_stall_option_requires_base_selective_policy():
    with pytest.raises(FlowError, match='requires selective'):
        FlowLimits(selective_stall_retry=True)


@pytest.mark.parametrize('enabled', [False, True])
def test_stall_is_opt_in_and_never_reduces_the_ordinary_three_receipt_rule(enabled):
    flow, link = blocked_window(enabled=enabled)
    assert not flow.selective_retry_ready()
    assert not flow.retry_selective_gap(.2)
    if not enabled:
        assert not flow.retry_selective_stall(.2)
        assert len(link.frames) == 5
        return
    before = (
        flow._next_seq,
        flow.queued_bytes(),
        flow._peer_credit,
        flow.acked_frames(),
    )
    assert flow.retry_selective_stall(.2)
    assert len(link.frames) == 6
    assert (
        flow._next_seq,
        flow.queued_bytes(),
        flow._peer_credit,
        flow.acked_frames(),
    ) == before
    assert flow.fast_retry_offers == flow.fast_retransmits == flow.retransmits == 1
    assert flow._in_flight[0].attempts == 2
    assert not flow.retry_selective_stall(.21)


def test_stall_requires_fresh_data_and_a_fully_occupied_allowance():
    exhausted, _ = sender(frames=4)
    ack(exhausted, positions=(1,), revision=2, credit=4, at=.1)
    assert exhausted.queued_bytes() == 0
    assert not exhausted.selective_stall_ready()

    unsent, _ = sender(frames=5)
    ack(unsent, positions=(1,), revision=2, credit=4, at=.1)
    assert unsent.in_flight() == 3 and unsent.queued_bytes() > 0
    assert not unsent.selective_stall_ready()


def test_stall_requires_accepted_later_selective_evidence():
    flow, _ = sender()
    ack(flow, positions=(), revision=2, credit=4, at=.1)
    flow.pump(.11, max_frames=1, drain=False)
    assert flow.in_flight() == 4
    assert not any(p.selective_evidence for p in flow._in_flight.values())
    assert not flow.selective_stall_ready()
    assert not flow.retry_selective_stall(.2)


def test_stall_cannot_precede_evidence_or_regress_its_clock():
    flow, link = blocked_window()
    assert not flow.retry_selective_stall(.09)
    assert flow.retry_selective_stall(.1)
    with pytest.raises(FlowError, match='regressed'):
        flow.retry_selective_stall(.09)
    assert len(link.frames) == 6


@pytest.mark.parametrize('now', [True, -1, float('nan'), float('inf'), 10**1000, 'now'])
def test_stall_refuses_invalid_time_without_offer(now):
    flow, link = blocked_window()
    with pytest.raises(FlowError):
        flow.retry_selective_stall(now)
    assert flow.fast_retry_offers == 0 and len(link.frames) == 5


def test_stall_preserves_ordinary_timeout_priority():
    flow, link = blocked_window()
    assert not flow.retry_selective_stall(1.1)
    assert flow.fast_retry_offers == 0 and len(link.frames) == 5
    flow.pump(1.1, max_frames=0, drain=False)
    assert flow.retransmits >= 1 and flow.fast_retransmits == 0


@pytest.mark.parametrize('outcome', [0, DEFERRED])
def test_refused_or_deferred_stall_spends_no_attempt_and_keeps_timeout_fallback(outcome):
    flow, link = blocked_window()
    pending = flow._in_flight[0]
    before = (pending.attempts, pending.sent_at, pending.rto)
    link.outcomes.append(outcome)
    assert flow.retry_selective_stall(.2)
    assert (pending.attempts, pending.sent_at, pending.rto) == before
    assert flow.fast_retry_offers == 1
    assert flow.fast_retransmits == flow.retransmits == 0
    assert not flow.retry_selective_stall(.21)


@pytest.mark.parametrize('kind', ['cancel', 'failed', 'fenced', 'peer_restart'])
def test_stall_preserves_lifecycle_and_peer_incarnation_guards(kind):
    flow, link = blocked_window()
    if kind == 'cancel':
        flow.cancel()
    if kind == 'failed':
        flow._fail_send('source_failure')
    if kind == 'fenced':
        flow._fenced = True
    if kind == 'peer_restart':
        link.boot += 1
    assert not flow.retry_selective_stall(.2)
    assert flow.fast_retry_offers == 0 and len(link.frames) == 5
    if kind == 'peer_restart':
        assert flow.failure == 'peer_restarted'


def test_stall_respects_attempt_ceiling():
    flow, link = blocked_window(max_retries=2)
    pending = flow._in_flight[0]
    pending.attempts = 2
    assert not flow.selective_stall_ready()
    assert not flow.retry_selective_stall(.2)
    assert len(link.frames) == 5
