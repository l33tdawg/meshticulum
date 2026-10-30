"""V7 exact owner disposition, charged faults and fresh proof clocks; source only."""
from types import SimpleNamespace
import pytest
from meshticulum.contracts.negotiation import CommitOutcome
from meshticulum.flow.flow import FlowLimits, ReliableFlow, StartupGrant, FlowState
from tests.test_ember_meshticulum_flow_port import Port, NOW_US, QUANTUM
from tests.test_flow_selective_fast_retry import ack


def setup(*, enabled=True):
    port=Port(initial_credit_bytes=256)
    link=port.link();link.now=port.now_us/1e6
    limits=FlowLimits(frame_payload_bytes=QUANTUM,window_frames=4,initial_credit_frames=4,
        max_reassembly_frames=8,max_delivered_bytes=8192,max_queued_bytes=8192,
        selective_fast_retry=True,selective_repeat_retry=enabled)
    flow=ReliableFlow('a',link,limits,receive=False,startup=StartupGrant(4))
    port.owner.accept_grant(port.contract.limits.initial_credit_bytes)
    flow.send(bytes(range(QUANTUM))*12);flow.pump(link.now,max_frames=4,drain=False)
    at(SimpleNamespace(port=port,link=link),.1)
    ack(flow,positions=(1,2,3),credit=4,at=link.now)
    at(SimpleNamespace(port=port,link=link),.2)
    return SimpleNamespace(port=port,link=link,flow=flow,pending=flow._in_flight[0])


def at(case,seconds):
    case.port.now_us=NOW_US+round(seconds*1e6);case.link.now=case.port.now_us/1e6


@pytest.mark.parametrize('deferred',[False,True])
@pytest.mark.parametrize('outcome',[CommitOutcome.ACCEPTED,CommitOutcome.UNKNOWN,CommitOutcome.REFUSED_CARRIER])
def test_exact_disposition_controls_epoch_but_all_invoked_debt_is_retained(deferred,outcome):
    c=setup();p=c.pending;before=c.port.ledger.snapshot(c.port.now_us)['debt_own_us']
    c.port.outcome=outcome;c.link.defer_to_host=deferred
    assert c.flow.retry_selective_gap(c.link.now)
    if deferred:
        assert p.attempts==1 and c.flow.repeat_retry_epochs==0
        c.link.defer_to_host=False;at(c,.3);c.link.dispatch_queued()
    invoked=outcome in (CommitOutcome.ACCEPTED,CommitOutcome.UNKNOWN)
    assert p.attempts==1+int(invoked)
    assert c.port.owner.rf_attempts==4+int(invoked)
    # Known-unsent scheduler/carrier settlement still follows existing ledger rules.
    assert c.port.ledger.snapshot(c.port.now_us)['debt_own_us']>=before
    assert c.flow.repeat_retry_epochs==int(outcome is CommitOutcome.ACCEPTED)
    assert p.fast_retry_offered is (outcome is not CommitOutcome.ACCEPTED)
    assert c.link._held is None and not c.flow._deferred_retries
    if outcome is CommitOutcome.UNKNOWN:
        assert c.flow._fenced and c.flow.send_state is FlowState.FAILED
        calls=len(c.port.carrier_calls);c.flow.pump(c.link.now+10,max_frames=4,drain=False)
        assert len(c.port.carrier_calls)==calls


@pytest.mark.parametrize('deferred',[False,True])
def test_post_settlement_proof_time_is_distinct_from_unchanged_retry_timer(deferred):
    c=setup();c.link.defer_to_host=deferred
    c.port.on_send=lambda:setattr(c.port,'now_us',c.port.now_us+40_000)
    original_anchor=c.link.now
    assert c.flow.retry_selective_gap(c.link.now)
    if deferred:
        c.link.defer_to_host=False;at(c,.3);original_anchor=c.link.now
        c.link.dispatch_queued()
    assert c.pending.handed_over_at==original_anchor
    assert c.pending.proof_epoch_at==pytest.approx(original_anchor+.04)
    assert c.pending.selective_evidence_at==c.pending.proof_epoch_at
    assert c.pending.rto==1.0
    # Fresh higher receipt with an earlier local timestamp cannot populate proof.
    c.port.on_send=lambda:None;at(c,.4);c.flow.pump(c.link.now,max_frames=3,drain=False)
    ack(c.flow,positions=(1,2,3,4,5,6),credit=4,revision=3,at=original_anchor+.02)
    assert c.pending.selective_evidence==set()
    assert not c.flow.retry_selective_gap(c.link.now+.1)


@pytest.mark.parametrize('deferred',[False,True])
@pytest.mark.parametrize('fault',['exception','backwards','before_current','nonfinite','interrupt'])
def test_clock_fault_at_exact_settlement_keeps_spent_attempt_and_fences(monkeypatch,deferred,fault):
    c=setup();routing=[False];original=c.port.owner._route_settlement
    def route(*a,**k):
        routing[0]=True
        try:return original(*a,**k)
        finally:routing[0]=False
    monkeypatch.setattr(c.port.owner,'_route_settlement',route)
    def clock():
        if routing[0]:
            if fault=='exception':raise RuntimeError('synthetic clock')
            if fault=='interrupt':raise KeyboardInterrupt('synthetic interruption')
            if fault=='before_current':return c.port.now_us-1
            return NOW_US-1 if fault=='backwards' else float('nan')
        return c.port.now_us
    c.link.monotonic_us=clock;c.link.defer_to_host=deferred
    if deferred:
        assert c.flow.retry_selective_gap(c.link.now);c.link.defer_to_host=False;at(c,.3)
        call=c.link.dispatch_queued
    else:call=lambda:c.flow.retry_selective_gap(c.link.now)
    if fault=='interrupt':
        with pytest.raises(KeyboardInterrupt,match='synthetic interruption'):call()
    else:call()
    assert c.pending.attempts==2 and c.port.owner.rf_attempts==5
    assert c.flow.retransmits==1 and c.flow.repeat_retry_epochs==0
    assert c.flow._fenced and c.pending.fast_retry_offered
    assert c.link._held is None and c.link._repeat_handover is None


def test_foreign_scheduler_dispatch_preserves_exact_deferred_outcome_identity():
    c=setup();c.link.defer_to_host=True
    assert c.flow.retry_selective_gap(c.link.now)
    c.link.defer_to_host=False;at(c,.4)
    result=c.port.dispatch()
    assert result.outcome is CommitOutcome.ACCEPTED
    assert c.pending.attempts==2 and c.flow.repeat_retry_epochs==1
    assert c.pending.proof_epoch_at==c.link.now
    assert c.link._repeat_handover is None


@pytest.mark.parametrize('fault',['authority','expiry','owner'])
def test_unsent_deferred_attempt_cannot_rearm_or_refund(monkeypatch,fault):
    c=setup();c.link.defer_to_host=True;assert c.flow.retry_selective_gap(c.link.now)
    c.link.defer_to_host=False;at(c,.3);before=c.port.ledger.snapshot(c.port.now_us)['debt_own_us']
    if fault=='authority':c.link.authorize=lambda request:None
    if fault=='owner':c.port.owner.settle_sending()
    if fault=='expiry':c.port.now_epoch=2200
    c.link.dispatch_queued()
    assert c.pending.attempts==1 and c.pending.fast_retry_offered and c.flow.repeat_retry_epochs==0
    assert c.port.owner.rf_attempts==4
    assert c.port.ledger.snapshot(c.port.now_us)['debt_own_us']>=before
