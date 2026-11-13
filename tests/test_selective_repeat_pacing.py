"""Host restraint over unchanged frozen flow; no physical delivery claims."""
from types import SimpleNamespace
import pytest
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.flow.flow import DEFERRED
from tests.test_flow_repeat_retry import sender
from tests.test_flow_selective_fast_retry import ack
from tests.test_burst_runtime import composition
from tests.test_burst_repair_train import tail


def host(enabled=True):
    flow, link = sender(frames=48, window=32)
    obj = object.__new__(BenchFlow)
    obj.flow, obj.link = flow, SimpleNamespace(now=0.)
    obj._pace_selective_repeats = enabled
    obj._paced_fast_offers, obj._repeat_not_before = {}, {}
    return obj, link


@pytest.mark.parametrize('enabled', [False, True])
def test_fresh_proof_cannot_burn_all_attempts_inside_original_rto(enabled):
    h, _ = host(enabled); p = h.flow._in_flight[0]
    original_limits = h.flow.limits
    for epoch in range(1, 6):
        ack(h.flow, positions=tuple(range(1, 3*epoch+1)), credit=32,
            revision=epoch+1, at=epoch*.1)
        h.link.now = epoch*.1+.01
        assert h._offer_selective() is (not enabled or epoch == 1)
    assert p.attempts == (2 if enabled else 6)
    assert h.flow.limits is original_limits and p.rto == 2.22
    h.flow.pump(p.sent_at+p.rto+.001, max_frames=0, drain=False)
    assert (h.flow.failure is None) is enabled
    if enabled:
        assert p.attempts == 3 and p.rto == 4.44
        assert h.flow.fast_retransmits == 1 and h.flow.retransmits > 1
    else:
        assert h.flow.failure == 'retry_ceiling'


@pytest.mark.parametrize('outcome', [0, DEFERRED])
def test_refusal_or_deferred_offer_does_not_start_timer(outcome):
    h, link = host(); link.outcomes.append(outcome)
    ack(h.flow, positions=(1,2,3), credit=32, at=.1); h.link.now=.2
    assert h._offer_selective() and not h._repeat_not_before
    if outcome is DEFERRED:
        assert h.flow.handover(0, at=.5, accepted=True)
        h._refresh_repeat_pacing(); p=h.flow._in_flight[0]
        assert h._repeat_not_before[0] == (p,p.sent_at+p.rto)
    else:
        assert not h._paced_fast_offers


def test_cancelled_deferred_offer_leaves_no_timer():
    h, link=host();link.outcomes.append(DEFERRED)
    ack(h.flow,positions=(1,2,3),credit=32,at=.1);h.link.now=.2
    assert h._offer_selective()
    assert h.flow.handover_cancelled(0,link.frames[-1])
    h._refresh_repeat_pacing()
    assert not h._paced_fast_offers and not h._repeat_not_before


def test_ack_does_not_renew_timer_and_retirement_clears_identity():
    h,_=host();ack(h.flow,positions=(1,2,3),credit=32,at=.1);h.link.now=.2
    assert h._offer_selective(); original=h._repeat_not_before[0]
    ack(h.flow,positions=(1,2,3,4,5,6),credit=32,revision=3,at=.3);h.link.now=.4
    assert not h._offer_selective() and h._repeat_not_before[0]==original
    ack(h.flow,base=7,positions=(),credit=32,revision=4,at=.5)
    h._refresh_repeat_pacing()
    assert not h._repeat_not_before


def test_grouped_repair_refuses_cooled_candidate_without_spending(composition):
    case=tail(composition);h=case.sender;h._pace_selective_repeats=True
    case.channels[0].release_held();h.step()
    assert h._repair_train_active and h.flow.fast_retransmits==1
    candidate=h._paced_selective_candidate(terminal=True)
    assert candidate is not None
    h._repeat_not_before[candidate.seq]=(candidate,h.link.now+candidate.rto)
    before=(h.flow.fast_retry_offers,h.owner.rf_attempts,case.journals[0].snapshot()['attempts'])
    h.step()
    assert not h._repair_train_active
    assert before==(h.flow.fast_retry_offers,h.owner.rf_attempts,case.journals[0].snapshot()['attempts'])


def test_stall_train_uses_same_cooldown_gate(composition):
    from tests.test_burst_stall_retry import stalled_case
    case=stalled_case(composition);h=case.sender;h._pace_selective_repeats=True
    for _ in range(300):
        case.receiver.step();h.step()
        if h.counts['stall_repair_turn']:break
        if case.workers[0].pending_physical:case.channels[0].finish_packets()
        case.clock.advance(10000)
    assert h._repair_train_mode=='stall' and h.flow.fast_retransmits==1
    candidate=h._paced_selective_candidate(stall=True)
    assert candidate is not None
    h._repeat_not_before[candidate.seq]=(candidate,h.link.now+candidate.rto)
    before=(h.flow.fast_retry_offers,h.owner.rf_attempts,case.journals[0].snapshot()['attempts'])
    h.step()
    assert not h._repair_train_active
    assert before==(h.flow.fast_retry_offers,h.owner.rf_attempts,case.journals[0].snapshot()['attempts'])


@pytest.mark.parametrize('value',[None,1,'true',True])
def test_public_option_requires_boolean_and_confirmation(value):
    from tests.test_radio_file_example import example
    from meshticulum.bench.wire import WireError
    with pytest.raises(WireError,match='invalid_selective_repeat_pacing_option'):
        example.FileTransfer(None,None,None,None,None,None,clock=None,window=None,
                             chunk=0,confirm_receipt=False,pace_selective_repeats=value)


def test_enable_only_before_activity_and_with_confirmation(composition):
    from meshticulum.bench.wire import WireError
    case=composition(train_depth=8,train24=True)
    h=case.sender
    with pytest.raises(WireError,match='invalid_selective_repeat_pacing_setup'):
        h.enable_selective_repeat_pacing()
    h.enable_receipt_confirmation(b'p'*32)
    h.enable_selective_repeat_pacing()
    assert h.snapshot()['timing_policy']['pace_selective_repeats'] is True
    with pytest.raises(WireError,match='invalid_selective_repeat_pacing_setup'):
        h.enable_selective_repeat_pacing()


def test_main_offer_waits_without_spending_then_original_timeout_recovers(composition):
    case=tail(composition);h=case.sender;h._pace_selective_repeats=True
    for seq,p in h.flow._in_flight.items():
        h._repeat_not_before[seq]=(p,p.sent_at+p.rto)
    case.channels[0].release_held()
    before=(h.owner.rf_attempts,case.journals[0].snapshot()['attempts'])
    for _ in range(3):h.step()
    assert h.flow.fast_retry_offers==0
    assert before==(h.owner.rf_attempts,case.journals[0].snapshot()['attempts'])
    deadline=min(p.sent_at+p.rto for p in h.flow._in_flight.values())
    case.clock.advance(max(0,int(deadline*1000000)-case.clock()[1])+1000)
    h.step()
    assert h.flow.fast_retry_offers==0 and h.flow.retransmits>0
    assert h.owner.rf_attempts>before[0] and h.flow.failure is None
