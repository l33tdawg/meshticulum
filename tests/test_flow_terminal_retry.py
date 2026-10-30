"""Explicit terminal-only one-SACK recovery; no physical authority is inferred."""
from collections import deque

import pytest

from meshticulum.flow.flow import DEFERRED, FlowError, FlowLimits, ReliableFlow, StartupGrant, parse_frame
from tests.test_flow_selective_fast_retry import Link, ack


def sender(*, terminal=True, frames=4, window=4, modulus=65536, max_retries=4):
    link=Link()
    limits=FlowLimits(frame_payload_bytes=4,window_frames=window,
        initial_credit_frames=window,max_reassembly_frames=window,seq_modulus=modulus,
        max_delivered_bytes=1024,max_queued_bytes=1024,max_retries=max_retries,
        selective_fast_retry=True,selective_terminal_retry=terminal)
    flow=ReliableFlow('a',link,limits,startup=StartupGrant(window))
    flow.send(bytes(index%251 for index in range(frames*4)))
    flow.pump(0,max_frames=window,drain=False)
    return flow,link


def terminal_gap(flow, *, at=.1):
    ack(flow,base=0,positions=(1,3),revision=2,credit=2,at=at)


@pytest.mark.parametrize('value',[0,1,None,'true',[],{}])
def test_terminal_option_and_request_are_strict_booleans(value):
    with pytest.raises(FlowError,match='selective_terminal_retry'):
        FlowLimits(selective_fast_retry=True,selective_terminal_retry=value)
    flow,link=sender();terminal_gap(flow)
    with pytest.raises(FlowError,match='terminal retry request'):
        flow.selective_retry_ready(terminal=value)
    with pytest.raises(FlowError,match='terminal retry request'):
        flow.retry_selective_gap(.2,terminal=value)
    assert len(link.frames)==4


def test_terminal_option_requires_base_selective_policy():
    with pytest.raises(FlowError,match='requires selective'):
        FlowLimits(selective_terminal_retry=True)


@pytest.mark.parametrize('enabled',[False,True])
def test_noarg_default_and_bulk_pump_remain_three_receipts(enabled):
    flow,link=sender(terminal=enabled);terminal_gap(flow)
    assert not flow.selective_retry_ready()
    assert not flow.retry_selective_gap(.2)
    flow.pump(.3,max_frames=0,drain=False)
    assert len(link.frames)==4 and flow.fast_retry_offers==0
    assert flow.selective_retry_ready(terminal=True) is enabled
    assert flow.retry_selective_gap(.4,terminal=True) is enabled


def test_actual_tail123_124_126_two_two_one_evidence_after_final127():
    flow,link=sender(frames=128,window=32)
    for index,base in enumerate((32,64,96),start=2):
        ack(flow,base=base,positions=(),revision=index,credit=32,at=index/100)
        flow.pump(index/100,max_frames=32,drain=False)
    assert flow._original_final_handed_over and not flow.queued_bytes()
    assert parse_frame(link.frames[-1])['seq']==127 and parse_frame(link.frames[-1])['last']
    ack(flow,base=123,positions=(125,127),revision=5,credit=30,at=.1)
    assert flow._peer_original_final_received
    assert {seq:len(p.selective_evidence) for seq,p in flow._in_flight.items()}=={123:2,124:2,126:1}
    before=(flow._next_seq,flow._peer_credit,flow.acked_frames(),flow.queued_bytes())
    rtos={seq:p.rto for seq,p in flow._in_flight.items()}
    assert not flow.selective_retry_ready() and not flow.retry_selective_gap(.15)
    for index,seq in enumerate((123,124,126)):
        assert flow.retry_selective_gap(.2+index/100,terminal=True)
        frame=parse_frame(link.frames[-1]);assert frame['seq']==seq and not frame['last']
        assert flow._in_flight[seq].attempts==2 and flow._in_flight[seq].rto==rtos[seq]
    assert (flow._next_seq,flow._peer_credit,flow.acked_frames(),flow.queued_bytes())==before
    for revision in (5,6,7):
        ack(flow,base=123,positions=(125,127),revision=revision,credit=30,at=.3)
        assert not flow.retry_selective_gap(.4,terminal=True)
    assert flow.fast_retry_offers==flow.fast_retransmits==3 and len(link.frames)==131


def test_missing_final_disables_terminal_mode_even_with_later_evidence_for_other_hole():
    flow,link=sender()
    ack(flow,positions=(1,2),revision=2,credit=2)
    assert flow._original_final_handed_over and not flow._peer_original_final_received
    assert not flow.selective_retry_ready(terminal=True)
    assert not flow.retry_selective_gap(.2,terminal=True)
    assert len(link.frames)==4


def test_final_handover_does_not_invent_selective_evidence():
    flow,link=sender()
    assert flow._original_final_handed_over and not flow._peer_original_final_received
    assert all(not p.selective_evidence for p in flow._in_flight.values())
    assert not flow.retry_selective_gap(.2,terminal=True)
    assert len(link.frames)==4


@pytest.mark.parametrize('kind',['deferred','refused'])
def test_unhanded_final_and_impossible_future_final_receipt_do_not_enable(kind):
    flow,link=sender(frames=5)
    ack(flow,positions=(1,2,3),revision=2,credit=4)
    link.outcomes.append(DEFERRED if kind=='deferred' else 0)
    flow.pump(.2,max_frames=1,drain=False)
    assert flow.queued_bytes()==4 and not flow._original_final_handed_over
    ack(flow,positions=(1,2,3,4),revision=3,credit=4,at=.3)
    assert not flow._peer_original_final_received
    # Three existing receipts still qualify the bulk path; terminal-specific
    # permission cannot reduce its threshold or fabricate FINAL acceptance.
    assert flow._selective_evidence_required(True)==3
    if kind=='deferred':
        assert flow.handover(4,at=.4)
        assert flow._original_final_handed_over and not flow._peer_original_final_received
        ack(flow,positions=(1,2,3,4),revision=4,credit=4,at=.5)
        assert flow._peer_original_final_received


@pytest.mark.parametrize('kind',['stale','conflicting','owner_refused','foreign','generation'])
def test_rejected_final_receipt_cannot_enable_terminal_permission(kind):
    flow,link=sender()
    ack(flow,positions=(1,),revision=2,credit=3)
    options=dict(positions=(1,3),revision=3,credit=2,at=.2)
    if kind=='stale':options['revision']=1
    if kind=='conflicting':options['revision']=2
    if kind=='owner_refused':link.accept_receipt=False
    if kind=='foreign':options['flow_id']=2
    if kind=='generation':options['generation']=2
    ack(flow,**options)
    assert not flow._peer_original_final_received
    assert not flow.retry_selective_gap(.3,terminal=True)
    assert len(link.frames)==4


@pytest.mark.parametrize('outcome',[0,DEFERRED,1])
def test_one_terminal_opportunity_is_consumed_before_offer(outcome):
    flow,link=sender();terminal_gap(flow)
    link.outcomes.append(outcome)
    assert flow.retry_selective_gap(.2,terminal=True)
    assert flow.fast_retry_offers==1
    if outcome is DEFERRED:
        assert flow.handover_cancelled(0,link.frames[-1])
    # Other hole2 remains eligible, so inspect exact first-hole state and then
    # consume its separate single offer. Replays cannot reset either marker.
    assert flow._in_flight[0].fast_retry_offered
    assert flow.retry_selective_gap(.21,terminal=True)
    for revision in (2,3,4):
        ack(flow,positions=(1,3),revision=revision,credit=2,at=.3)
        assert not flow.retry_selective_gap(.4,terminal=True)
    assert flow.fast_retry_offers==2


@pytest.mark.parametrize('kind',['cancel','failed','fenced','peer_restart'])
def test_terminal_api_keeps_state_and_peer_incarnation_guards(kind):
    flow,link=sender();terminal_gap(flow)
    if kind=='cancel':flow.cancel()
    if kind=='failed':flow._fail_send('synthetic')
    if kind=='fenced':flow._fenced=True
    if kind=='peer_restart':link.boot+=1
    assert not flow.retry_selective_gap(.2,terminal=True)
    assert len(link.frames)==4


def test_terminal_receipts_respect_sequence_wrap():
    flow,link=sender(frames=18,window=4,modulus=16)
    for base,revision in ((4,2),(8,3),(12,4)):
        ack(flow,base=base,positions=(),revision=revision,credit=4,at=revision/100)
        flow.pump(revision/100,max_frames=4,drain=False)
    ack(flow,base=14,positions=(15,),revision=5,credit=3,at=.1)
    flow.pump(.1,max_frames=2,drain=False)
    assert flow._original_final_handed_over
    ack(flow,base=14,positions=(15,1),revision=6,credit=2,at=.2)
    assert set(flow._in_flight)=={14,0}
    assert flow._peer_original_final_received and not flow.selective_retry_ready()
    assert flow.retry_selective_gap(.21,terminal=True)
    assert parse_frame(link.frames[-1],16)['seq']==14
    assert flow.retry_selective_gap(.22,terminal=True)
    assert parse_frame(link.frames[-1],16)['seq']==0


@pytest.mark.parametrize('outcome',[0,DEFERRED,1])
def test_terminal_mode_cannot_reuse_evidence_after_ordinary_timeout_offer(outcome):
    flow,link=sender();terminal_gap(flow)
    link.outcomes.extend((outcome,outcome))
    flow.pump(1.01,max_frames=0,drain=False)
    assert all(p.fast_retry_offered for p in flow._in_flight.values())
    assert not flow.retry_selective_gap(1.02,terminal=True)
    assert flow.fast_retry_offers==0
    if outcome is DEFERRED:
        for frame in link.frames[-2:]:
            assert flow.handover_cancelled(parse_frame(frame)['seq'],frame)
    assert not flow.retry_selective_gap(1.03,terminal=True)


def test_lost_terminal_retry_keeps_rto_then_normal_timeout_doubles_and_hits_ceiling():
    flow,link=sender(max_retries=3);terminal_gap(flow)
    assert flow.retry_selective_gap(.2,terminal=True)
    assert flow._in_flight[0].rto==1.0
    # Retire hole2 while keeping0 missing, including accepted FINAL receipt.
    ack(flow,positions=(1,2,3),revision=3,credit=3,at=.3)
    flow.pump(1.21,max_frames=0,drain=False)
    assert flow._in_flight[0].attempts==3 and flow._in_flight[0].rto==2.0
    assert not flow.retry_selective_gap(1.22,terminal=True)
    flow.pump(3.22,max_frames=0,drain=False)
    assert flow.failure=='retry_ceiling' and len(link.frames)==6


@pytest.mark.parametrize('now',[True,-1,float('nan'),float('inf'),10**1000,'now'])
def test_terminal_mode_keeps_finite_clock_validation(now):
    flow,link=sender();terminal_gap(flow)
    with pytest.raises(FlowError):flow.retry_selective_gap(now,terminal=True)
    assert flow.fast_retry_offers==0 and len(link.frames)==4
