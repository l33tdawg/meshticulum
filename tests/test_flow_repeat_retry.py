"""Draft R13: fresh canonical proof per confirmed attempt, never reused receipts."""
from dataclasses import replace

import pytest

from meshticulum.flow.flow import (CarrierHandover, DEFERRED, FlowError, FlowLimits,
    FlowState, ReliableFlow, StartupGrant, parse_frame)
from tests.test_flow_selective_fast_retry import Link, ack


def sender(*, enabled=True, terminal=False, frames=24, window=8, modulus=65536, ceiling=6):
    link=Link()
    limits=FlowLimits(frame_payload_bytes=4,window_frames=window,initial_credit_frames=window,
        max_reassembly_frames=window,max_total_frames=128,max_queued_bytes=1024,max_delivered_bytes=1024,
        seq_modulus=modulus,max_retries=ceiling,initial_rto_s=2.22,min_rto_s=2.22,max_rto_s=12,
        selective_fast_retry=True,selective_terminal_retry=terminal,selective_repeat_retry=enabled)
    flow=ReliableFlow('a',link,limits,startup=StartupGrant(window))
    flow.send(bytes(range(frames*4)));flow.pump(0,max_frames=window,drain=False)
    return flow,link


def first(flow, positions=(1,2,3)):
    ack(flow,positions=positions,credit=flow.limits.window_frames,at=.1)
    assert flow.retry_selective_gap(.2)
    return flow._in_flight[0]


@pytest.mark.parametrize('value',[None,0,1,'true',[],{}])
def test_flag_is_strict_and_requires_fast(value):
    with pytest.raises(FlowError,match='selective_repeat_retry'):
        FlowLimits(selective_fast_retry=True,selective_repeat_retry=value)


def test_repeat_requires_base_fast():
    with pytest.raises(FlowError,match='requires selective'):
        FlowLimits(selective_repeat_retry=True)


@pytest.mark.parametrize('enabled',[False,True])
def test_new_receipts_after_lost_fast_repair_are_new_epoch_only_when_admitted(enabled):
    flow,link=sender(enabled=enabled);pending=first(flow,tuple(range(1,8)))
    assert pending.attempts==2 and pending.rto==2.22
    assert flow.repeat_retry_epochs==int(enabled)
    assert pending.selective_evidence==(set() if enabled else {1,2,3})
    for revision in (2,3,4):ack(flow,positions=(1,2,3),credit=8,revision=revision,at=.21)
    assert not flow.retry_selective_gap(.22)
    flow.pump(.23,max_frames=3,drain=False)
    assert flow._next_seq==11
    ack(flow,positions=(1,2,3,8,9,10),credit=8,revision=5,at=.3)
    before=(flow._next_seq,flow.queued_bytes(),flow.acked_frames(),flow._peer_credit)
    assert flow.retry_selective_gap(.4) is enabled
    assert pending.attempts==2+int(enabled) and pending.rto==2.22
    assert (flow._next_seq,flow.queued_bytes(),flow.acked_frames(),flow._peer_credit)==before
    if enabled:assert link.frames[8]==link.frames[-1] and pending.selective_evidence==set()


@pytest.mark.parametrize('outcome',[0,DEFERRED])
def test_offer_refusal_and_deferred_never_rearm(outcome):
    flow,link=sender();link.outcomes.append(outcome);pending=first(flow)
    assert pending.attempts==1 and pending.fast_retry_offered and flow.repeat_retry_epochs==0
    ack(flow,positions=(1,2,3,4,5,6),credit=8,revision=3,at=.3)
    assert not flow.retry_selective_gap(.4)
    if outcome is DEFERRED:
        assert flow.handover_cancelled(0,link.frames[-1])
        assert not flow.retry_selective_gap(.5)
    assert pending.attempts==1


def test_deferred_receipts_discarded_on_actual_handover_not_offer():
    flow,link=sender();link.outcomes.append(DEFERRED);pending=first(flow)
    ack(flow,positions=(1,2,3,4,5,6),credit=8,revision=3,at=.3)
    assert flow.handover(0,at=.5,accepted=True)
    assert pending.selective_evidence==set() and not pending.fast_retry_offered
    assert pending.attempts==2 and pending.handed_over_at==.5
    ack(flow,positions=(1,2,3,4,5,6),credit=8,revision=4,at=.6)
    assert not flow.retry_selective_gap(.61)
    flow.pump(.62,max_frames=3,drain=False)
    ack(flow,positions=(1,2,3,4,5,6,8,9,10),credit=8,revision=5,at=.7)
    assert pending.selective_evidence=={8,9,10}
    assert flow.retry_selective_gap(.8)


@pytest.mark.parametrize('at',[.19,float('nan'),float('inf'),-1,True])
def test_late_or_invalid_ack_time_cannot_create_new_epoch_proof(at):
    flow,link=sender();pending=first(flow)
    flow.pump(.21,max_frames=3,drain=False)
    ack(flow,positions=(1,2,3,8,9,10),credit=8,revision=3,at=at)
    assert not pending.selective_evidence and not flow.retry_selective_gap(.4)
    # A repeated canonical receipt cannot recover the discarded old-time proof.
    ack(flow,positions=(1,2,3,8,9,10),credit=8,revision=4,at=.5)
    assert not pending.selective_evidence and not flow.retry_selective_gap(.6)


def test_regressed_ack_after_current_epoch_cannot_add_second_or_third_proof():
    flow,_=sender();pending=first(flow)
    ack(flow,positions=(1,2,3,4),credit=8,revision=3,at=.6)
    assert pending.selective_evidence=={4}
    ack(flow,positions=(1,2,3,4,5,6),credit=8,revision=4,at=.5)
    assert pending.selective_evidence=={4} and not flow.retry_selective_gap(.7)


@pytest.mark.parametrize('kind',['stale','conflicting','impossible','owner_refused','foreign','generation'])
def test_only_fully_accepted_new_canonical_receipts_count(kind):
    flow,link=sender();pending=first(flow)
    options=dict(positions=(1,2,3,4,5,6),credit=8,revision=3,at=.3)
    if kind=='stale':options['revision']=1
    if kind=='conflicting':options['revision']=2
    if kind=='impossible':options['positions']=(4,5,8)
    if kind=='owner_refused':link.accept_receipt=False
    if kind=='foreign':options['flow_id']=2
    if kind=='generation':options['generation']=2
    ack(flow,**options)
    assert pending.selective_evidence==set() and not flow.retry_selective_gap(.4)


def test_proof_bound_stays_three_and_ordinary_timeout_keeps_priority_backoff():
    flow,_=sender();pending=first(flow)
    ack(flow,positions=(1,2,3,4,5,6,7),credit=8,revision=3,at=.4)
    assert len(pending.selective_evidence)==3
    due=pending.sent_at+pending.rto
    assert not flow.retry_selective_gap(due)
    flow.pump(due+.01,max_frames=0,drain=False)
    assert pending.attempts==3 and pending.rto==4.44
    assert pending.selective_evidence==set() and not pending.fast_retry_offered
    assert flow.repeat_retry_epochs==2
    assert not flow.retry_selective_gap(due+.02)


def test_normal_refused_timeout_consumes_opportunity_without_epoch():
    flow,link=sender();pending=first(flow)
    ack(flow,positions=(1,2,3,4,5,6,7),credit=8,revision=3,at=.4)
    link.outcomes.append(0);flow.pump(pending.sent_at+pending.rto+.01,max_frames=0,drain=False)
    assert pending.fast_retry_offered and pending.attempts==2 and flow.repeat_retry_epochs==1
    assert not flow.retry_selective_gap(3)


@pytest.mark.parametrize('accepted,at',[(False,.3),(True,.05),(True,float('nan'))])
@pytest.mark.parametrize('deferred',[False,True])
def test_unknown_or_bad_handover_clock_is_charged_fenced_without_rearm(accepted,at,deferred):
    flow,link=sender();link.outcomes.append(DEFERRED if deferred else CarrierHandover(accepted,at))
    pending=first(flow)
    if deferred:assert flow.handover(0,at=at,accepted=accepted)
    assert pending.attempts==2 and flow.retransmits==flow.fast_retransmits==1
    assert pending.fast_retry_offered and flow.repeat_retry_epochs==0
    assert flow._fenced and flow.send_state is FlowState.FAILED
    count=len(link.frames);flow.pump(10,max_frames=10,drain=False)
    assert len(link.frames)==count


def test_max_attempts_still_bounds_repeat_epochs_and_full_final_identity():
    flow,link=sender(frames=8,terminal=True,ceiling=3);pending=first(flow,tuple(range(1,7)))
    ack(flow,positions=tuple(range(1,8)),credit=8,revision=3,at=.3)
    assert flow.retry_selective_gap(.4,terminal=True)
    assert pending.attempts==3 and not flow.retry_selective_gap(.5,terminal=True)
    assert parse_frame(link.frames[7])['last'] and len(parse_frame(link.frames[7])['payload'])==4
    flow.pump(pending.sent_at+pending.rto+.1,max_frames=0,drain=False)
    assert flow.failure=='retry_ceiling'


def test_terminal_one_receipt_still_needs_new_proof_and_original_final_conditions():
    flow,_=sender(frames=8,terminal=True)
    ack(flow,positions=(1,2,3,7),credit=8,revision=2,at=.1)
    assert flow._peer_original_final_received and flow.retry_selective_gap(.2,terminal=True)
    ack(flow,positions=(1,2,3,7),credit=8,revision=3,at=.3)
    assert flow._in_flight[0].selective_evidence==set()
    # Other pre-existing holes may still have their first opportunity; consume
    # those separately, then prove the repeated snapshot cannot rearm hole0.
    for at in (.4,.41,.42):flow.retry_selective_gap(at,terminal=True)
    assert not flow.retry_selective_gap(.43,terminal=True)
    ack(flow,positions=(1,2,3,4,7),credit=8,revision=4,at=.5)
    assert not flow.retry_selective_gap(.6)
    assert flow.retry_selective_gap(.7,terminal=True)


def test_new_proof_handles_sequence_wrap():
    flow,link=sender(frames=24,window=4,modulus=16)
    for base,revision in ((4,2),(8,3),(12,4)):
        ack(flow,base=base,positions=(),credit=4,revision=revision,at=revision/10)
        flow.pump(revision/10,max_frames=4,drain=False)
    ack(flow,base=14,positions=(15,),credit=4,revision=5,at=.5)
    flow.pump(.5,max_frames=2,drain=False)
    ack(flow,base=14,positions=(15,0,1),credit=4,revision=6,at=.6)
    assert flow.retry_selective_gap(.7)
    flow.pump(.8,max_frames=3,drain=False)
    ack(flow,base=14,positions=(15,0,1,2,3,4),credit=4,revision=7,at=.9)
    assert flow._in_flight[14].selective_evidence=={2,3,4}
    assert flow.retry_selective_gap(1.0)
    assert parse_frame(link.frames[-1],16)['seq']==14


@pytest.mark.parametrize('terminal',['retired','cancelled'])
def test_owed_deferred_attempt_does_not_reopen_terminal_flight(terminal):
    flow,link=sender(frames=8);link.outcomes.append(DEFERRED);pending=first(flow)
    if terminal=='retired':ack(flow,base=8,positions=(),credit=8,revision=3,at=.3)
    else:flow.cancel()
    state=flow.send_state
    assert flow.handover(0,at=.4,accepted=True)
    assert flow.send_state is state and flow.repeat_retry_epochs==0
    assert flow.retransmits==1 and not flow._in_flight
