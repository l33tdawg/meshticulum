"""Selective retry through real owner/scheduler settlement; modeled carrier only.

These controls assert logical admission and durable-budget semantics, not RF
delivery. ACKs are explicit test inputs to the reusable reliable-flow API.
"""
from types import SimpleNamespace

import pytest

from meshticulum.contracts.negotiation import CommitOutcome
from meshticulum.flow.flow import (FlowError, FlowLimits, FlowState, ReliableFlow, StartupGrant,
    build_ack_frame, parse_frame, remote_startup_credit)
from meshticulum.flow.flow_port import FlowPortLink, MeshticulumFlowOwner
from tests.test_ember_meshticulum_flow_port import NOW_US, QUANTUM, Port, contracts


def transfer(*, enabled=None, frames=5):
    port = Port(initial_credit_bytes=256)
    options = {} if enabled is None else dict(selective_fast_retry=enabled)
    limits = FlowLimits(frame_payload_bytes=QUANTUM, window_frames=4,
        initial_credit_frames=4, max_reassembly_frames=8, max_delivered_bytes=4096,
        max_queued_bytes=8192, **options)
    link = port.link()
    flow = ReliableFlow('a', link, limits, flow_id=1, generation=1, receive=False,
                        startup=StartupGrant(remote_startup_credit(limits)))
    port.owner.accept_grant(port.contract.limits.initial_credit_bytes)
    flow.send(bytes(range(QUANTUM)) * frames)
    return SimpleNamespace(port=port, link=link, flow=flow, limits=limits)


def at(case, when):
    case.link.now = when
    case.port.now_us = NOW_US + round(when * 1_000_000)


def receipt(case, *, base=0, sack=0, revision=2):
    raw = build_ack_frame(flow_id=1, generation=1, ack_seq=base, credit=4,
                          sack=sack, grant_revision=revision, limits=case.limits)
    case.flow.absorb(parse_frame(raw), case.link.now)


def selective_gap(case):
    case.flow.pump(0, max_frames=4)
    assert len(case.port.carrier_calls) == 4
    at(case, .1)
    receipt(case, sack=sum(1 << (32 - offset) for offset in (1, 2, 3)))
    assert case.flow.acked_frames() == 3 and set(case.flow._in_flight) == {0}
    at(case, .2)
    pending = case.flow._in_flight[0]
    assert case.link.now < pending.sent_at + pending.rto
    return pending


def markers(flow):
    return sum(len(value) for value in flow._deferred_retries.values())


def defer_fast(case):
    before = (case.flow._next_seq, case.flow.queued_bytes(), case.port.owner.rf_attempts,
              case.flow._in_flight[0].attempts, case.flow._in_flight[0].sent_at,
              case.flow._in_flight[0].rto)
    case.link.defer_to_host = True
    try:
        assert case.flow.retry_selective_gap(case.link.now) is True
        for _ in range(20):
            assert case.flow.retry_selective_gap(case.link.now) is False
    finally:
        case.link.defer_to_host = False
    assert before == (case.flow._next_seq, case.flow.queued_bytes(), case.port.owner.rf_attempts,
                      case.flow._in_flight[0].attempts, case.flow._in_flight[0].sent_at,
                      case.flow._in_flight[0].rto)
    assert markers(case.flow) == 1 and case.link._held is not None
    assert case.flow.retransmits == case.flow.fast_retransmits == 0
    assert case.flow.fast_retry_offers == 1 and not case.flow.selective_retry_ready()


@pytest.mark.parametrize('enabled', [None, False])
def test_default_and_explicit_off_keep_existing_timeout_retry(enabled):
    case = transfer(enabled=enabled)
    pending = selective_gap(case)
    assert not case.flow.selective_retry_ready()
    assert case.flow.retry_selective_gap(case.link.now) is False
    assert case.port.owner.rf_attempts == 4 and case.flow.retransmits == 0
    at(case, pending.sent_at + pending.rto + .001)
    case.flow.pump(case.link.now, max_frames=0)
    assert case.port.owner.rf_attempts == 5 and case.flow.retransmits == 1
    assert case.flow.fast_retry_offers == case.flow.fast_retransmits == 0


def test_opt_in_pump_alone_does_not_bypass_explicit_host_retry_gate():
    case = transfer(enabled=True)
    selective_gap(case)
    case.flow.pump(case.link.now, max_frames=0)
    assert case.flow.selective_retry_ready() and case.port.owner.rf_attempts == 4
    defer_fast(case)


@pytest.mark.parametrize('outcome', [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_deferred_fast_retry_spends_once_at_its_actual_handover(outcome):
    case = transfer(enabled=True)
    pending = selective_gap(case)
    old_rto = pending.rto
    defer_fast(case)
    at(case, .3)
    debt = case.port.ledger.snapshot(case.port.now_us)['debt_own_us']
    case.port.outcome = outcome
    assert case.link.dispatch_queued() is True
    assert case.port.owner.rf_attempts == 5 and len(case.port.carrier_calls) == 5
    assert pending.attempts == 2 and pending.handed_over_at == .3 and pending.rto == old_rto
    assert case.flow.retransmits == case.flow.fast_retransmits == 1
    assert case.flow._next_seq == 4 and case.flow.queued_bytes() == QUANTUM
    assert markers(case.flow) == 0 and case.link._held is None
    assert case.flow.retry_selective_gap(case.link.now) is False
    assert case.port.ledger.snapshot(case.port.now_us)['debt_own_us'] == debt + case.port.owner.cost.own_tx_us
    if outcome is CommitOutcome.UNKNOWN:
        assert case.port.scheduler.snapshot()['events']['unknown'] == 1


@pytest.mark.parametrize('refusal', ['carrier', 'authority', 'no_send'])
def test_known_unsent_fast_retry_cannot_steal_the_next_data_handover(refusal):
    case = transfer(enabled=True)
    pending = selective_gap(case)
    before = (pending.attempts, pending.sent_at, pending.rto)
    defer_fast(case)
    at(case, .3)
    if refusal == 'carrier':
        case.port.outcome = CommitOutcome.REFUSED_CARRIER
        assert case.link.dispatch_queued() is False
    else:
        if refusal == 'no_send':
            case.port.owner.settle_sending()
            result = case.port.dispatch()
            case.port.owner.resume_sending()
        else:
            result = case.port.dispatch(authorize=lambda request: None)
        assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert case.port.owner.rf_attempts == 4
    assert (pending.attempts, pending.sent_at, pending.rto) == before
    assert case.flow.retransmits == case.flow.fast_retransmits == 0
    assert markers(case.flow) == 0 and case.link._held is None
    assert not case.flow.retry_selective_gap(case.link.now), 'one fast offer is consumed even when known unsent'
    receipt(case, base=4, revision=3)
    assert case.flow.in_flight() == 0 and case.flow.acked_frames() == 4
    at(case, .4)
    case.port.outcome = CommitOutcome.ACCEPTED
    case.flow.pump(case.link.now, max_frames=1)
    assert case.port.owner.rf_attempts == 5 and case.flow._next_seq == 5
    assert case.flow.queued_bytes() == 0 and set(case.flow._in_flight) == {4}
    assert case.flow._in_flight[4].attempts == 1
    assert case.flow.retransmits == case.flow.fast_retransmits == 0
    assert parse_frame(case.link.sent[-1])['seq'] == 4


@pytest.mark.parametrize('outcome', [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_receipt_during_deferral_does_not_erase_retry_debt_or_create_new_data(outcome):
    case = transfer(enabled=True)
    selective_gap(case)
    defer_fast(case)
    debt = case.port.ledger.snapshot(case.port.now_us)['debt_own_us']
    at(case, .25)
    receipt(case, base=4, revision=3)
    assert case.flow.in_flight() == 0 and markers(case.flow) == 1
    assert case.port.ledger.snapshot(case.port.now_us)['debt_own_us'] == debt
    at(case, .3)
    case.port.outcome = outcome
    assert case.link.dispatch_queued() is True
    assert case.flow.in_flight() == 0 and case.flow._next_seq == 4
    assert case.flow.queued_bytes() == QUANTUM and case.port.owner.rf_attempts == 5
    assert case.flow.retransmits == case.flow.fast_retransmits == 1
    assert markers(case.flow) == 0
    assert case.port.ledger.snapshot(case.port.now_us)['debt_own_us'] == debt + case.port.owner.cost.own_tx_us
    if outcome is CommitOutcome.ACCEPTED:
        at(case, .4)
        case.flow.pump(case.link.now, max_frames=1)
        assert set(case.flow._in_flight) == {4} and case.flow._in_flight[4].attempts == 1
        assert case.port.owner.rf_attempts == 6 and case.flow._next_seq == 5
    else:
        assert case.port.scheduler.snapshot()['events']['unknown'] == 1


def test_foreign_dispatch_settles_only_its_own_flow_before_fast_retry():
    case = transfer(enabled=True)
    selective_gap(case)
    _, _, key, profile, contract = contracts(flow_id=2, initial_credit_bytes=256)
    owner = MeshticulumFlowOwner(owner_id=b'p' * 16, flow=key, contract=contract, profile=profile)
    case.port.scheduler.open_flow(key, contract, profile, attempt_owner=owner,
                                 now_us=case.port.now_us, now_epoch=case.port.now_epoch)
    owner.accept_grant(contract.limits.initial_credit_bytes)
    link = FlowPortLink(scheduler=case.port.scheduler, owner=owner, flow=key,
        authorize=case.port.authorize, broker=case.port.broker, carrier=case.port.carrier,
        monotonic_us=case.port.clock, epoch_clock=lambda: case.port.now_epoch)
    flow = ReliableFlow('a', link, case.limits, flow_id=2, generation=1, receive=False,
                        startup=StartupGrant(remote_startup_credit(case.limits)))
    flow.send(b'B' * QUANTUM)
    link.now = case.link.now
    link.defer_to_host = True
    flow.pump(link.now, max_frames=1)
    link.defer_to_host = False
    assert owner.rf_attempts == 0 and link._held is not None
    assert case.flow.retry_selective_gap(case.link.now) is True
    assert owner.rf_attempts == 1 and flow.in_flight() == 1
    assert case.port.owner.rf_attempts == 4 and case.flow.retransmits == 0
    assert case.link.foreign_dispatches == 1 and markers(case.flow) == 1
    at(case, .3)
    assert case.port.dispatch().outcome is CommitOutcome.ACCEPTED
    assert case.port.owner.rf_attempts == 5 and case.flow.fast_retransmits == 1
    assert owner.rf_attempts == 1 and flow.retransmits == 0
    assert [call.request.flow.flow_id for call in case.port.carrier_calls[-2:]] == [2, 1]


def test_full_size_final_timeout_retry_preserves_original_encoded_fin():
    case = transfer(enabled=True, frames=1)
    case.flow.pump(0, max_frames=1)
    original = case.link.sent[0]
    assert parse_frame(original)['last'] is True
    pending = case.flow._in_flight[0]
    at(case, pending.sent_at + pending.rto + .001)
    case.link.defer_to_host = True
    case.flow.pump(case.link.now, max_frames=0)
    case.link.defer_to_host = False
    assert case.link._held.payload == original
    at(case, case.link.now + .1)
    assert case.link.dispatch_queued() is True
    assert case.link.sent[-1] == original and markers(case.flow) == 0
    assert case.flow.retransmits == 1 and case.flow.fast_retransmits == 0


@pytest.mark.parametrize('outcome', [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN,
    CommitOutcome.REFUSED_CARRIER, CommitOutcome.REFUSED_SCHEDULER])
def test_complete_flow_settles_only_its_already_owed_fast_retry(outcome):
    case = transfer(enabled=True, frames=4)
    selective_gap(case)
    defer_fast(case)
    debt = case.port.ledger.snapshot(case.port.now_us)['debt_own_us']
    at(case, .25)
    receipt(case, base=4, revision=3)
    assert case.flow.complete and case.flow.send_state is FlowState.COMPLETE
    assert case.flow.queued_bytes() == case.flow.in_flight() == 0
    assert markers(case.flow) == 1, 'completion does not erase a queued outcome obligation'
    assert case.port.ledger.snapshot(case.port.now_us)['debt_own_us'] == debt
    at(case, .3)
    attempted = outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN)
    if outcome is CommitOutcome.REFUSED_SCHEDULER:
        result = case.port.dispatch(authorize=lambda request: None)
        assert result.outcome is outcome and not result.invoked
    else:
        case.port.outcome = outcome
        assert case.link.dispatch_queued() is attempted
    assert markers(case.flow) == 0 and case.link._held is None
    assert case.flow.complete and case.flow.send_state is FlowState.COMPLETE
    assert case.flow._next_seq == case.flow.acked_frames() == 4
    assert case.flow.queued_bytes() == case.flow.in_flight() == 0
    assert case.flow.retransmits == case.flow.fast_retransmits == int(attempted)
    assert case.port.owner.rf_attempts == 4 + int(attempted)
    assert case.port.ledger.snapshot(case.port.now_us)['debt_own_us'] == (
        debt + int(attempted) * case.port.owner.cost.own_tx_us)
    assert not case.flow.selective_retry_ready()
    assert not case.flow.retry_selective_gap(case.link.now)
    assert not case.flow.handover(0, at=case.link.now), 'settlement cannot be replayed'
    calls = len(case.port.carrier_calls)
    case.flow.pump(case.link.now)
    assert len(case.port.carrier_calls) == calls
    with pytest.raises(FlowError):
        case.flow.send(b'new transfer is not admitted by completion')


@pytest.mark.parametrize('deferred', [False, True])
@pytest.mark.parametrize('outcome', [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN,
                                   CommitOutcome.REFUSED_CARRIER])
def test_timeout_offer_consumes_stale_selective_opportunity_without_double_retry(deferred, outcome):
    case = transfer(enabled=True)
    pending = selective_gap(case)
    assert case.flow.selective_retry_ready()
    at(case, pending.sent_at + pending.rto + .001)
    assert not case.flow.retry_selective_gap(case.link.now), 'an expired RTO is no longer early recovery'
    case.port.outcome = outcome
    case.link.defer_to_host = deferred
    case.flow.pump(case.link.now, max_frames=0)
    case.link.defer_to_host = False
    assert not case.flow.selective_retry_ready(), 'even a refused/deferred timeout consumes the opportunity'
    if deferred:
        assert markers(case.flow) == 1 and case.port.owner.rf_attempts == 4
        at(case, case.link.now + .1)
        assert case.link.dispatch_queued() is (outcome is not CommitOutcome.REFUSED_CARRIER)
    attempted = outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN)
    assert markers(case.flow) == 0
    assert case.flow.retransmits == int(attempted)
    assert pending.attempts == 1 + int(attempted)
    assert case.port.owner.rf_attempts == 4 + int(attempted)
    assert case.flow.fast_retry_offers == case.flow.fast_retransmits == 0
    calls = len(case.port.carrier_calls)
    receipt(case, sack=sum(1 << (32 - offset) for offset in (1, 2, 3)), revision=3)
    assert not case.flow.retry_selective_gap(case.link.now)
    assert len(case.port.carrier_calls) == calls, 'a newer revision of the same receipt is not new evidence'
