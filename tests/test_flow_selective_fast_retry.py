"""Proposed opt-in flow revision: authenticated receipt evidence, not RF proof."""
from collections import deque
from types import SimpleNamespace

import pytest

from meshticulum.flow.flow import (DEFERRED, FlowError, FlowLimits, FlowState,
    ReliableFlow, StartupGrant, build_ack_frame, parse_frame)


class Link:
    def __init__(self):
        self.boot = 1
        self.frames = []
        self.outcomes = deque()
        self.accept_receipt = True

    def incarnation(self, peer):
        return self.boot

    def send(self, endpoint, frame, **kwargs):
        self.frames.append(frame)
        return self.outcomes.popleft() if self.outcomes else 1

    def profile(self, endpoint):
        return SimpleNamespace(capacity_bytes_per_second=1_000_000)

    def queued_bytes(self, endpoint):
        return 0

    def reflect_acknowledged(self, **kwargs):
        return self.accept_receipt


def sender(*, enabled=True, frames=5, window=4, modulus=65536, max_retries=4):
    link = Link()
    limits = FlowLimits(frame_payload_bytes=4, window_frames=window,
        initial_credit_frames=window, max_reassembly_frames=window, seq_modulus=modulus,
        max_total_frames=64, max_queued_bytes=512, max_delivered_bytes=512,
        max_retries=max_retries, selective_fast_retry=enabled)
    flow = ReliableFlow('a', link, limits, startup=StartupGrant(window))
    flow.send(bytes(index % 251 for index in range(frames * 4)))
    flow.pump(0, max_frames=window, drain=False)
    return flow, link


def ack(flow, *, base=0, positions=(1, 2, 3), revision=2, credit=1, at=.1,
        flow_id=1, generation=1):
    sack = sum(1 << (32 - offset) for position in positions
               if 0 < (offset := (position - base) % flow.seq_modulus) <= 32)
    raw = build_ack_frame(flow_id=flow_id, generation=generation, ack_seq=base,
        credit=credit, sack=sack, grant_revision=revision, limits=flow.limits,
        modulus=flow.seq_modulus)
    flow.absorb(parse_frame(raw, flow.seq_modulus), at)


def state(flow):
    return [(p.seq, p.attempts, p.sent_at, p.rto, p.handed_over_at,
             tuple(sorted(p.selective_evidence)), p.fast_retry_offered)
            for p in flow._in_flight.values()]


@pytest.mark.parametrize('value', [0, 1, None, '', [], object()])
def test_fast_retry_policy_is_strict_boolean(value):
    with pytest.raises(FlowError, match='selective_fast_retry'):
        FlowLimits(selective_fast_retry=value)


def test_three_distinct_later_sacks_enable_one_explicit_offer_not_pump():
    flow, link = sender()
    ack(flow, positions=(1,))
    assert not flow.selective_retry_ready()
    for revision in (2, 3, 4):
        ack(flow, positions=(1,), revision=revision)
    assert flow._in_flight[0].selective_evidence == {1}
    ack(flow, positions=(1, 2), revision=5)
    assert not flow.selective_retry_ready()
    ack(flow, positions=(1, 2, 3), revision=6)
    assert flow.selective_retry_ready()
    flow.pump(.2, max_frames=0, drain=False)
    assert len(link.frames) == 4 and flow.retransmits == 0
    original = link.frames[0]
    before = (flow._next_seq, flow.queued_bytes(), flow._peer_credit, flow.acked_frames())
    assert flow.retry_selective_gap(.2)
    assert link.frames[-1] == original
    assert (flow._next_seq, flow.queued_bytes(), flow._peer_credit, flow.acked_frames()) == before
    assert flow.fast_retry_offers == flow.fast_retransmits == flow.retransmits == 1
    for revision in (6, 7, 8):
        ack(flow, revision=revision)
        assert not flow.retry_selective_gap(.3)
    assert len(link.frames) == 5


@pytest.mark.parametrize('kind', ['future', 'stale', 'conflicting', 'foreign', 'generation', 'owner_refused'])
def test_unaccepted_receipt_cannot_supply_fast_retry_evidence(kind):
    flow, link = sender()
    ack(flow, positions=(1,), revision=2)
    before = state(flow)
    options = dict(positions=(1, 2, 3), revision=3)
    if kind == 'future': options['positions'] = (1, 2, 4)
    if kind == 'stale': options['revision'] = 1
    if kind == 'conflicting': options['revision'] = 2
    if kind == 'foreign': options['flow_id'] = 2
    if kind == 'generation': options['generation'] = 2
    if kind == 'owner_refused': link.accept_receipt = False
    ack(flow, **options)
    assert state(flow) == before and not flow.selective_retry_ready()
    assert not flow.retry_selective_gap(.2) and len(link.frames) == 4


def test_cumulative_receipt_does_not_guess_selective_evidence():
    flow, _ = sender()
    ack(flow, base=1, positions=(), credit=4)
    assert all(not pending.selective_evidence for pending in flow._in_flight.values())
    assert not flow.selective_retry_ready()


def test_evidence_is_bounded_to_three_sequences_per_live_hole():
    flow, _ = sender(frames=9, window=8)
    ack(flow, positions=tuple(range(1, 8)), credit=1)
    assert set(flow._in_flight) == {0}
    assert len(flow._in_flight[0].selective_evidence) == 3
    assert flow.selective_retry_ready()


def test_distinct_later_sequence_evidence_uses_wrap_ordering():
    flow, link = sender(frames=20, modulus=16)
    for base, revision in ((4, 2), (8, 3), (12, 4)):
        ack(flow, base=base, positions=(), revision=revision, credit=4, at=revision / 10)
        flow.pump(revision / 10, max_frames=4, drain=False)
    ack(flow, base=14, positions=(15,), revision=5, credit=3, at=.5)
    flow.pump(.5, max_frames=2, drain=False)
    ack(flow, base=14, positions=(15, 0, 1), revision=6, credit=1, at=.6)
    assert flow._in_flight[14].selective_evidence == {15, 0, 1}
    assert flow.retry_selective_gap(.7)
    retry = parse_frame(link.frames[-1], 16)
    assert retry['seq'] == 14 and not retry['last']


@pytest.mark.parametrize('kind', ['cancel', 'failed', 'fenced', 'peer_restart'])
def test_fast_api_cannot_bypass_terminal_or_peer_identity(kind):
    flow, link = sender()
    ack(flow)
    if kind == 'cancel': flow.cancel()
    if kind == 'failed': flow._fail_send('source_failure')
    if kind == 'fenced': flow._fenced = True
    if kind == 'peer_restart': link.boot += 1
    assert not flow.retry_selective_gap(.2)
    assert len(link.frames) == 4 and flow.fast_retry_offers == 0
    if kind == 'peer_restart': assert flow.failure == 'peer_restarted'


@pytest.mark.parametrize('now', [True, -1, float('nan'), float('inf'), 10**1000, 'now'])
def test_fast_api_refuses_invalid_time_without_offer(now):
    flow, link = sender()
    ack(flow)
    with pytest.raises(FlowError): flow.retry_selective_gap(now)
    assert len(link.frames) == 4 and flow.fast_retry_offers == 0


def test_fast_api_does_not_precede_receipt_evidence_or_regress_its_clock():
    flow, link = sender()
    ack(flow, at=.5)
    assert not flow.retry_selective_gap(.4)
    assert flow.retry_selective_gap(.5)
    with pytest.raises(FlowError, match='regressed'): flow.retry_selective_gap(.45)
    assert len(link.frames) == 5


def test_retry_ceiling_is_unchanged_and_not_failed_early_by_api():
    flow, link = sender(max_retries=2)
    ack(flow)
    flow.pump(1.1, max_frames=0, drain=False)
    assert flow._in_flight[0].attempts == 2
    assert not flow.retry_selective_gap(1.2) and flow.failure is None
    flow.pump(3.2, max_frames=0, drain=False)
    assert flow.failure == 'retry_ceiling' and len(link.frames) == 5


@pytest.mark.parametrize('outcome', [1, 0, DEFERRED])
def test_ordinary_timeout_offer_consumes_old_selective_evidence(outcome):
    flow, link = sender()
    ack(flow)
    link.outcomes.append(outcome)
    flow.pump(1.1, max_frames=0, drain=False)
    if outcome is DEFERRED:
        assert flow.handover_cancelled(0, link.frames[-1])
    assert not flow.selective_retry_ready()
    assert not flow.retry_selective_gap(1.101)
    assert len(link.frames) == 5 and flow.fast_retry_offers == flow.fast_retransmits == 0


def test_api_does_not_relabel_an_already_due_timeout_as_fast():
    flow, link = sender()
    ack(flow)
    assert not flow.retry_selective_gap(1.1)
    assert len(link.frames) == 4 and flow.fast_retry_offers == 0
    flow.pump(1.1, max_frames=0, drain=False)
    assert flow.retransmits == 1 and flow.fast_retransmits == 0


def test_refused_fast_offer_spends_no_attempt_and_timeout_remains_fallback():
    flow, link = sender()
    ack(flow)
    pending = flow._in_flight[0]
    before = (pending.attempts, pending.sent_at, pending.rto)
    link.outcomes.append(0)
    assert flow.retry_selective_gap(.2)
    assert (pending.attempts, pending.sent_at, pending.rto) == before
    assert flow.fast_retry_offers == 1 and flow.retransmits == flow.fast_retransmits == 0
    assert not flow.retry_selective_gap(.3)
    flow.pump(1.1, max_frames=0, drain=False)
    assert flow.retransmits == 1 and flow.fast_retransmits == 0


def test_full_size_original_final_flag_survives_deferred_timeout_and_retirement():
    flow, link = sender(enabled=False, frames=4)
    original = link.frames[-1]
    assert parse_frame(original)['last']
    ack(flow, base=3, positions=(), credit=4)
    link.outcomes.append(DEFERRED)
    flow.pump(1.1, max_frames=0, drain=False)
    assert link.frames[-1] == original
    ack(flow, base=4, positions=(), revision=3, credit=4, at=1.2)
    assert flow.complete
    assert flow.handover_cancelled(3, original)
    assert flow._deferred_retries == {} and flow.retransmits == 0
