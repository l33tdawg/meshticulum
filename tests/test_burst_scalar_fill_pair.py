"""Opt-in scalar pairs retain per-frame handover, debt and receive priority."""
from copy import deepcopy

import pytest

from meshticulum.bench.burst_protocol import (POLICY, POLICY8, POLICY32, POLICY_REPAIR_FILL,
    POLICY_TERMINAL_RETRY, POLICY_SCALAR_FILL_PAIR, policy_for_scalar_fill_pair,
    policy_scalar_fill_pair, validate_policy)
from meshticulum.bench.tx_burst import BurstContractError, Stage
from meshticulum.bench.wire import FrameKind, WireError
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model


def test_exact_policy_and_previous_defaults():
    assert policy_for_scalar_fill_pair() == POLICY_SCALAR_FILL_PAIR
    assert all(not policy_scalar_fill_pair(p) for p in
               (POLICY, POLICY8, POLICY32, POLICY_REPAIR_FILL, POLICY_TERMINAL_RETRY))
    copy = policy_for_scalar_fill_pair(); copy['scalar_fill_pair'] = False
    assert policy_for_scalar_fill_pair()['scalar_fill_pair'] is True
    with pytest.raises(BurstContractError): validate_policy(copy)


@pytest.mark.parametrize('change', [{'scalar_fill_pair':1}, {'scalar_fill_pair':'true'},
    {'scalar_fill_pair':False}, {'schema':'meshticulum.data-burst-policy.v5'},
    {'selective_terminal_retry':False}, {'selective_repair_fill':False},
    {'max_depth':4}, {'train_frames':4}, {'flow_window_frames':8},
    {'max_hold_us':2_000_001}, {'reverse_listen_us':0}, {'extra':True}])
def test_policy_refuses_unreviewed_shapes(change):
    with pytest.raises(BurstContractError): validate_policy(dict(POLICY_SCALAR_FILL_PAIR, **change))


def begin(case):
    case.sender.send(case.data)
    case.receiver.step()
    return case.sender


@pytest.mark.parametrize('enabled', [False, True])
def test_initial_fresh_pair_is_individually_accepted_and_charged(composition, enabled):
    case = composition(b'x'*(20*193), train_depth=8, terminal_retry=not enabled, scalar_fill_pair=enabled)
    sender = begin(case); deadline = sender.authority.application_deadline_us
    sender.step()
    count = 2 if enabled else 1
    assert sender.counts['tx_QUEUED'] == sender.flow._next_seq == sender.flow.in_flight() == count
    assert case.journals[0].snapshot()['attempts'] == sender.owner.rf_attempts == count
    assert len(sender.owner.retained) == len(case.workers[0].pending_physical) == count
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0] == count
    assert sender.authority.application_deadline_us == deadline
    assert sender.counts['scalar_fill_pair_extra'] == int(enabled)
    assert ('scalar_fill_pair' in sender.snapshot()['timing_policy']) == enabled
    assert not sender._scalar_fill_extra


def test_policy_changes_peer_binding_without_other_admission_changes(composition):
    old = composition(train_depth=8, terminal_retry=True)
    new = composition(train_depth=8, scalar_fill_pair=True)
    assert old.authorities[0].grant.digest != new.authorities[0].grant.digest
    for attr in ('deadlines','operation_limits'):
        assert getattr(old.authorities[0],attr) == getattr(new.authorities[0],attr)
    assert old.authorities[0].contract.limits == new.authorities[0].contract.limits
    assert old.sender.flow.limits == new.sender.flow.limits
    assert old.sender.sender_control_hold_us == new.sender.sender_control_hold_us
    assert old.workers[0]._depth == new.workers[0]._depth == 8
    packet = old.sender.cipher.seal(b'old policy', FrameKind.DATA, authorize=lambda:True)
    with pytest.raises(WireError, match='authentication_failed'):
        new.receiver.cipher.open(packet, authorize=lambda:True)
    assert new.journals[0].snapshot()['attempts'] == 0


def test_low_water_does_not_fill_entire_train(composition):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); sender.step()
    assert len(case.workers[0].pending_physical) == 2
    sender.step()
    assert sender.counts['tx_QUEUED'] == 3 and sender.counts['scalar_fill_pair_extra'] == 1
    for _ in range(5): sender.step()
    assert sender.counts['tx_QUEUED'] == 8 and sender.owner.no_send
    assert len(case.workers[0].pending_physical) == 8
    sender.step(); assert sender.counts['tx_QUEUED'] == 8


@pytest.mark.parametrize('fault',['authority','buffered','owner_hold','credit','turn','terminal','parity'])
def test_extra_offer_rechecks_live_boundary_after_first_acceptance(composition, monkeypatch, fault):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); worker = case.workers[0]; original = worker.queue_data
    first = []
    def queue(*args, **kwargs):
        result = original(*args, **kwargs)
        assert not first; first.append(result)
        if fault=='authority': monkeypatch.setattr(sender.authority,'application_live',lambda:False)
        if fault=='buffered': monkeypatch.setattr(type(worker),'has_buffered_activity',property(lambda self:True))
        if fault=='owner_hold': sender.owner.settle_sending()
        if fault=='credit': sender.flow._peer_credit = 1
        if fault=='turn': sender.next_send_us = case.clock()[1] + 1_000_000
        if fault=='terminal':
            case.channels[0].event(result.request, Stage.RF_DONE)
            worker._ingest(case.channels[0].read(0))
        if fault=='parity':
            # A real terminal collection can be behind the worker's release.
            sender._physical_data[-1] = {'pending_collection':True}
        return result
    monkeypatch.setattr(worker,'queue_data',queue)
    if fault=='turn':
        # The accepted handover normally clears next_send_us. Introduce a new
        # turn at the read-only extra-opportunity gate instead.
        original_extra = sender._pump_scalar_fill_pair
        def held(*args):
            sender.next_send_us = case.clock()[1] + 1_000_000
            return original_extra(*args)
        monkeypatch.setattr(sender,'_pump_scalar_fill_pair',held)
    try:
        sender.step()
        assert sender.counts['tx_QUEUED'] == 1 and not sender.counts['scalar_fill_pair_extra']
        assert case.journals[0].snapshot()['attempts'] == sender.owner.rf_attempts == 1
        assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0] == 1
    finally:
        sender._physical_data.pop(-1,None)


def test_no_outbound_final_frame_does_not_create_an_extra_offer(composition):
    case = composition(b'x'*193, train_depth=8, scalar_fill_pair=True)
    sender = begin(case); sender.step()
    assert sender.flow._next_seq == sender.counts['tx_QUEUED'] == 1
    assert not sender.counts['scalar_fill_pair_extra']


def test_deferred_dispatch_and_normal_pump_do_not_get_a_third_offer(composition):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); sender.link.defer_to_host = True
    sender.step()
    assert sender.link._held is not None and sender.counts['tx_QUEUED'] == 0
    sender.link.defer_to_host = False
    sender.step()
    assert sender.counts['tx_QUEUED'] == sender.flow._next_seq == 2
    assert case.journals[0].snapshot()['attempts'] == 2
    assert sender.counts['scalar_fill_pair_extra'] == 0


@pytest.mark.parametrize('fault',['refused','partial_write'])
def test_first_carrier_failure_never_enables_extra_offer(composition, fault):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); case.channels[0].fault = fault
    try:
        sender.step()
    except WireError:
        assert fault == 'partial_write'
    assert case.journals[0].snapshot()['attempts'] == 1
    assert sender.counts['scalar_fill_pair_extra'] == 0
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0] == 1
    if fault=='partial_write':
        assert case.journals[0].snapshot()['fenced']
    case.channels[0].fault = None


def test_ordinary_deadline_crossed_during_primary_handover_stops_extra_without_prepare(composition, monkeypatch):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); sender.step()
    case.channels[0].deliver = lambda packet:None
    case.channels[0].finish_packets(); sender._poll_physical(); sender.update_counter()
    assert not case.workers[0].pending_physical and sender.flow.in_flight()==2
    due = min(p.sent_at+p.rto for p in sender.flow._in_flight.values())
    case.clock.advance(round(due*1e6)-1_000-case.clock()[1])
    original=case.workers[0].queue_data
    def cross(*args,**kwargs):
        value=original(*args,**kwargs)
        case.clock.advance(2_000)
        return value
    monkeypatch.setattr(case.workers[0],'queue_data',cross)
    sender.step()
    assert case.clock()[1]/1e6 > due
    assert sender.counts['tx_QUEUED']==3 and sender.flow.retransmits==0
    assert sender.owner.rf_attempts==len(sender.owner.retained)==3
    assert case.journals[0].snapshot()['attempts']==3
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0]==3
    assert sender.counts['scalar_fill_pair_extra']==1  # Only the earlier initial pair.


@pytest.mark.parametrize('faults', [dict(), {'drop_data':{0:1}}, {'drop_data':{0:2}},
    {'drop_data':{0:1,2:1}}, {'drop_data':{7:1}}, {'drop_data':{31:1}},
    {'drop_data':{3:1,4:1,6:1}}, {'drop_final_ack':True},
    {'drop_data':{0:1},'drop_ack_seqs':{0:2}},
    {'host_work_us':(40_000,10_000)}, {'host_work_us':(120_000,40_000)},
    {'host_step_work_us':(18_000,3_000)}, {'host_step_work_us':(40_000,10_000)},
    {'drop_data':{0:1},'host_step_work_us':(18_000,3_000)}])
def test_scalar_pair_fault_model_keeps_nonce_debt_delivery_and_guards(tmp_path, faults):
    previous={}
    def observe(index,endpoint,now):
        assert endpoint.burst_sent < 8 and endpoint.flow.in_flight() <= 32
        count=endpoint.counts['tx_QUEUED']
        if index==0 and endpoint.flow.retransmits==0:
            assert count-previous.get(index,0)<=2
        previous[index]=count
    results,air=model.exercise(tmp_path,train_depth=8,scalar_fill_pair=True,
        host_pump=True,receipt_confirmation=True,sender_cap=96,payload=b'x'*(40*193),observe=observe,**faults)
    model.assert_complete(results,air)
    assert not any(frame.collision for frame in air.frames)
    assert air.channels[0].peak <= 8
    assert len({frame.packet for frame in air.frames})==len(air.frames)
    for result,channel in zip(results,air.channels):
        journal=result['snapshot']['attempt_journal']
        assert journal['attempts']==channel.attempts and journal['recovered_unknown']==0
        assert result['settled_states']==[('RF_DONE',journal['attempts'])]
    data=[frame for frame in air.frames if 'seq' in frame.inner]
    assert all(b.start-a.end>=model.INTER_PACKET_RX_US for a,b in zip(data,data[1:]))


@pytest.mark.parametrize('mono', [10**12+3, 2**42+1])
def test_large_monotonic_clock_does_not_fail_a_valid_scalar_pair(composition, monkeypatch, mono):
    from tests import test_burst_runtime as fixtures
    class LargeClock(fixtures.Clock):
        pass
    LargeClock.mono = mono
    monkeypatch.setattr(fixtures,'Clock',LargeClock)
    original_channel = fixtures.RadioChannel
    class LargeChannel(original_channel):
        @property
        def time(self): return self.clock()[1] / 1_000_000
        @time.setter
        def time(self, value):
            if value == 100. and self.clock()[1] > 100_000_000:
                return  # The base fixture's initial assignment is not a clock reset.
            original_channel.time.fset(self, value)
    monkeypatch.setattr(fixtures,'RadioChannel',LargeChannel)
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); sender.step()
    assert sender.counts['tx_QUEUED'] == 2 and sender.counts['scalar_fill_pair_extra'] == 1


@pytest.mark.parametrize('error', [KeyboardInterrupt, SystemExit, OSError])
def test_interruption_inside_extra_pump_preserves_first_debt_and_clears_temporary_flag(composition, monkeypatch, error):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); original = sender.flow.pump
    def interrupted(*args, **kwargs):
        if sender._scalar_fill_extra: raise error('controlled extra pump interruption')
        return original(*args, **kwargs)
    monkeypatch.setattr(sender.flow,'pump',interrupted)
    with pytest.raises(error):sender.step()
    assert not sender._scalar_fill_extra
    assert case.journals[0].snapshot()['attempts'] == sender.owner.rf_attempts == 1
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0] == 1


def test_unexpected_retry_in_extra_pump_is_refused_before_new_nonce_or_physical_attempt(composition, monkeypatch):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender = begin(case); expire = sender.flow._expire
    def injected(now):
        if sender._scalar_fill_extra:
            sender.flow._offer_retry(sender.flow._in_flight[0],now)
        else: expire(now)
    monkeypatch.setattr(sender.flow,'_expire',injected)
    sender.step()
    assert sender.counts['scalar_fill_nonfresh_refused'] == 1
    assert sender.counts['tx_QUEUED'] == 1
    assert case.journals[0].snapshot()['attempts'] == 1
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0] == 1
    # A refused prepared framework handle is retained, never disguised as no work.
    assert len(sender.owner.retained) > 1
    assert sender.ledger.snapshot(case.clock()[1])['debt_own_us'] > 0


def test_accepted_deferred_dispatch_then_refused_primary_does_not_mint_third_attempt(composition, monkeypatch):
    case = composition(b'x'*(20*193), train_depth=8, scalar_fill_pair=True)
    sender=begin(case);sender.link.defer_to_host=True;sender.step();sender.link.defer_to_host=False
    assert sender.link._held is not None and sender.counts['tx_QUEUED']==0
    original=case.workers[0].queue_data;calls=[]
    def refusing(*args,**kwargs):
        result=original(*args,**kwargs);calls.append(result)
        case.channels[0].fault='refused'
        return result
    monkeypatch.setattr(case.workers[0],'queue_data',refusing)
    sender.step()
    assert [result.outcome for result in calls]==['QUEUED','REFUSED']
    assert sender.counts['tx_QUEUED']==1 and sender.counts['scalar_fill_pair_extra']==0
    assert case.journals[0].snapshot()['attempts']==2
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0]==2
    case.channels[0].fault=None


@pytest.mark.parametrize('frames',[8,9,16])
def test_accepted_fast_repair_can_share_one_fresh_scalar_without_another_step(composition, frames):
    case=composition(b'x'*(frames*193),train_depth=8,scalar_fill_pair=True)
    sender=case.sender
    for endpoint in (sender,case.receiver):
        endpoint.enable_receipt_confirmation(b'c'*32,
            receiver_ready=None if endpoint.sends else lambda:False)
    begin(case)
    for _ in range(20):
        sender.step()
        if sender.counts['tx_QUEUED']==8:break
    assert sender.counts['tx_QUEUED']==8
    original=case.channels[0].deliver;seen=[]
    def lose(packet):
        seen.append(packet)
        if len(seen)>1:original(packet)
    case.channels[0].deliver=lose;case.channels[0].finish_packets();case.channels[0].deliver=original
    for _ in range(500):
        case.receiver.step();before=sender.counts['tx_QUEUED'];sender.step()
        if sender.flow.fast_retransmits:break
        case.clock.advance(1000)
    assert sender.flow.fast_retransmits==sender.flow.fast_retry_offers==1
    assert sender.counts['tx_QUEUED']-before==(1 if frames==8 else 2)
    assert sender.flow._in_flight[0].attempts==2
    if frames>8:
        assert sender.flow._next_seq==9 and sender.flow._in_flight[8].attempts==1
        assert len(case.workers[0].pending_physical)==2
    if frames in (8,9):
        assert not sender._repair_fill_active and sender.owner.no_send
        assert sender.next_send_us>case.clock()[1]
    if frames==9:assert sender.flow._in_flight[8].original_last
