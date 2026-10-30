"""Explicit terminal repair trains; real accounting with source-only radio models."""
from dataclasses import replace
import pytest
from meshticulum.bench.burst_protocol import (POLICY,POLICY8,POLICY32,POLICY_REPAIR_FILL,
    POLICY_TERMINAL_RETRY,POLICY_SCALAR_FILL_PAIR,POLICY_REPEAT_RETRY,POLICY_REPAIR_TRAIN,
    policy_for_repair_train,policy_repair_train,validate_policy)
from meshticulum.bench.tx_burst import BurstContractError,Stage
from meshticulum.bench.wire import FrameKind,WireError
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Frame,Message
from tests.test_burst_runtime import composition
from tests.test_burst_terminal_retry import tail_case as old_tail_case
from tests import test_burst_sf5_recovery as model


def test_policy_only_explicit_v8():
    previous=(POLICY,POLICY8,POLICY32,POLICY_REPAIR_FILL,POLICY_TERMINAL_RETRY,POLICY_SCALAR_FILL_PAIR,POLICY_REPEAT_RETRY)
    assert all(not policy_repair_train(p)for p in previous)
    assert policy_for_repair_train()==POLICY_REPAIR_TRAIN
    copied=policy_for_repair_train();copied['selective_repair_train']=False
    assert policy_for_repair_train()['selective_repair_train'] is True
    with pytest.raises(BurstContractError):validate_policy(copied)


@pytest.mark.parametrize('change',[{'selective_repair_train':1},{'selective_repair_train':False},
    {'selective_repair_train':'true'},{'schema':'meshticulum.data-burst-policy.v7'},
    {'selective_repeat_retry':False},{'selective_terminal_retry':False},{'selective_repair_fill':False},
    {'scalar_fill_pair':False},{'max_depth':4},{'train_frames':16},{'flow_window_frames':8},
    {'reverse_listen_us':0},{'inter_packet_rx_us':0},{'extra':True}])
def test_policy_never_widens_other_bounds(change):
    with pytest.raises(BurstContractError):validate_policy(dict(POLICY_REPAIR_TRAIN,**change))


def test_v7_limits_and_cipher_binding_remain_original(composition):
    old=composition(train_depth=8,repeat_retry=True);new=composition(train_depth=8,repair_train=True)
    assert old.sender.flow.limits==new.sender.flow.limits
    assert old.authorities[0].contract.limits==new.authorities[0].contract.limits
    assert old.authorities[0].deadlines==new.authorities[0].deadlines
    assert old.authorities[0].operation_limits==new.authorities[0].operation_limits
    assert old.sender.sender_control_hold_us==new.sender.sender_control_hold_us
    assert old.workers[0]._depth==new.workers[0]._depth==8
    assert 'selective_repair_train' not in old.sender.snapshot()['timing_policy']
    assert new.sender.snapshot()['timing_policy']['selective_repair_train'] is True
    packet=old.sender.cipher.seal(b'data',FrameKind.DATA,authorize=lambda:True)
    with pytest.raises(WireError,match='authentication_failed'):
        new.receiver.cipher.open(packet,authorize=lambda:True)
    assert new.journals[0].snapshot()['attempts']==0


def tail(composition):
    def make(data,**_):return composition(data,train_depth=8,repair_train=True)
    return old_tail_case(make,withhold=True)


def active(composition):
    case=tail(composition);case.channels[0].release_held();case.sender.step()
    assert case.sender._repair_train_active
    assert case.sender.flow.fast_retransmits==1
    return case


def test_initial_physical_drain_is_required_and_each_hole_pays_once(composition):
    case=tail(composition);sender=case.sender
    for _ in range(5):sender.step()
    assert sender.flow.fast_retry_offers==0 and case.journals[0].snapshot()['attempts']==8
    case.channels[0].release_held();sender.step()
    assert sender._repair_train_active and sender.flow.fast_retransmits==1
    for expected in (2,3):
        before=sender.counts['tx_QUEUED'];sender.step()
        assert sender.counts['tx_QUEUED']==before+1
        assert sender.flow.fast_retransmits==expected
        assert case.journals[0].snapshot()['attempts']==sender.owner.rf_attempts==8+expected
        assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0]==8+expected
    assert not sender._repair_train_active and sender.owner.no_send
    assert sender.burst_sent==0 and sender.next_send_us>case.clock()[1]
    assert {domain[-1]for domain in sender._pending_data_domains}=={3,4,6}
    for _ in range(10):sender.step()
    assert sender.flow.fast_retry_offers==3 and case.journals[0].snapshot()['attempts']==11
    assert all(not p.selective_evidence for p in sender.flow._in_flight.values())


@pytest.mark.parametrize('fault',['authority','owner_hold','capacity','parity','buffered','held','timer'])
def test_extra_hole_stops_before_nonce_or_handle_when_gate_changes(composition,monkeypatch,fault):
    case=active(composition);sender=case.sender;worker=case.workers[0]
    before=(sender.owner.rf_attempts,len(sender.owner.retained),case.journals[0].snapshot()['attempts'],case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0])
    if fault=='authority':monkeypatch.setattr(sender.authority,'application_live',lambda:False)
    elif fault=='owner_hold':sender.owner.settle_sending()
    elif fault=='capacity':monkeypatch.setattr(type(worker),'has_capacity',property(lambda self:False))
    elif fault=='parity':sender._physical_data[-1]={'pending_collection':True}
    elif fault=='buffered':monkeypatch.setattr(type(worker),'has_buffered_activity',property(lambda self:True))
    elif fault=='held':monkeypatch.setattr(sender.link,'_held',object())
    elif fault=='timer':
        due=min(p.sent_at+p.rto for p in sender.flow._in_flight.values())
        case.clock.advance(round(due*1e6)+1-case.clock()[1])
    try:
        sender._step_repair_train()
        assert not sender._repair_train_active and sender.owner.no_send
        assert before==(sender.owner.rf_attempts,len(sender.owner.retained),case.journals[0].snapshot()['attempts'],case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0])
    finally:sender._physical_data.pop(-1,None)


def test_unauthenticated_ingress_stops_train_without_new_offer(composition):
    case=active(composition);sender=case.sender
    case.channels[0].offer(Frame(Message.RECEIVE,case.workers[0].identity.boot,0,b'\x00'*4+b'not a packet'))
    sender.step()
    assert not sender._repair_train_active and sender.counts['rx_refused']==1
    assert sender.flow.fast_retry_offers==1 and case.journals[0].snapshot()['attempts']==9


@pytest.mark.parametrize('fault',['refused','partial_write','deferred'])
def test_extra_refusal_unknown_and_deferred_stop_normal_accounting(composition,fault):
    case=active(composition);sender=case.sender
    if fault=='deferred':sender.link.defer_to_host=True
    else:case.channels[0].fault=fault
    try:
        if fault=='partial_write':
            with pytest.raises(WireError):sender.step()
        else:sender.step()
        assert sender.flow.fast_retry_offers==2
        assert sender.flow.fast_retransmits==(2 if fault=='partial_write' else 1)
        assert not sender._repair_train_active
        assert case.journals[0].snapshot()['attempts']==(9 if fault=='deferred' else 10)
        assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0]==(9 if fault=='deferred' else 10)
        if fault=='partial_write':assert case.journals[0].snapshot()['fenced']
        if fault=='deferred':assert sender.link._held is not None
    finally:case.channels[0].fault=None


@pytest.mark.parametrize('faults',[
    {},{'drop_data':{3:1,4:1,6:1}},{'drop_data':{3:2,4:1,6:1}},
    {'drop_data':{3:1,4:1,6:1},'drop_ack_seqs':{3:1}},
    {'drop_data':{7:1}},{'drop_final_ack':True},
    {'drop_data':{3:1,4:1,6:1},'host_work_us':(40_000,10_000)},
    {'drop_data':{3:1,4:1,6:1},'host_work_us':(120_000,40_000)},
])
def test_half_duplex_fault_models_keep_debt_nonce_and_native_floors(tmp_path,faults):
    previous={}
    def observe(index,endpoint,now):
        assert endpoint.burst_sent<8 and len(endpoint._repair_train_domains)<=8
        count=endpoint.counts['tx_QUEUED'];assert count-previous.get(index,0)<=2
        previous[index]=count
    results,air=model.exercise(tmp_path,train_depth=8,repair_train=True,host_pump=True,
        receipt_confirmation=True,sender_cap=40,payload=b'x'*(8*193),observe=observe,**faults)
    model.assert_complete(results,air)
    assert not any(f.collision for f in air.frames)
    assert air.channels[0].peak<=8
    assert len({f.packet for f in air.frames})==len(air.frames)
    for result,channel in zip(results,air.channels):
        journal=result['snapshot']['attempt_journal']
        assert journal['attempts']==channel.attempts and journal['recovered_unknown']==0
        assert result['settled_states']==[('RF_DONE',journal['attempts'])]


def test_wrong_boot_on_initial_status_never_opens_train(composition,monkeypatch):
    case=tail(composition);original=case.channels[0].offer
    def wrong(frame):
        original(replace(frame,boot=b'3'*16)if frame.type is Message.BURST_STATUS_REPLY else frame)
    monkeypatch.setattr(case.channels[0],'offer',wrong);case.channels[0].release_held()
    with pytest.raises(HardwareError,match='boot_changed'):case.sender.step()
    assert case.sender.flow.fast_retry_offers==0
    assert not case.sender._repair_train_active
    assert case.journals[0].snapshot()['attempts']==8


def test_unknown_initial_terminal_does_not_open_train(composition):
    case=tail(composition);case.channels[0].release_held(Stage.UNKNOWN)
    with pytest.raises((HardwareError,WireError)):case.sender.step()
    assert not case.sender._repair_train_active
    assert case.sender.flow.fast_retry_offers==0
    assert case.journals[0].snapshot()['fenced']


@pytest.mark.parametrize('fault',['final_handover','final_receipt','outbound','no_evidence','cancel'])
def test_train_requires_current_terminal_proof_and_open_flow(composition,fault):
    case=active(composition);sender=case.sender
    if fault=='final_handover':sender.flow._original_final_handed_over=False
    elif fault=='final_receipt':sender.flow._peer_original_final_received=False
    elif fault=='outbound':sender.flow._outbound.extend(b'unsent')
    elif fault=='no_evidence':
        for p in sender.flow._in_flight.values():p.selective_evidence.clear()
    elif fault=='cancel':sender.flow.cancel()
    before=case.journals[0].snapshot()['attempts'];sender._step_repair_train()
    assert not sender._repair_train_active and sender.owner.no_send
    assert case.journals[0].snapshot()['attempts']==before==9


def test_crossing_ordinary_deadline_during_prepare_refuses_before_cipher(composition,monkeypatch):
    case=active(composition);sender=case.sender
    due=min(p.sent_at+p.rto for p in sender.flow._in_flight.values())
    case.clock.advance(round(due*1e6)-500-case.clock()[1])
    original=sender.authority.require_dispatch
    def crossing(frame):
        original(frame);case.clock.advance(1000)
    monkeypatch.setattr(sender.authority,'require_dispatch',crossing)
    sender.step()
    assert sender.counts['repair_train_noneligible_refused']==1
    assert sender.flow.fast_retry_offers==2 and sender.flow.fast_retransmits==1
    assert not sender._repair_train_active and sender.owner.no_send
    assert case.journals[0].snapshot()['attempts']==sender.owner.rf_attempts==9
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0]==9
    assert len(sender.owner.retained)==10  # The normal prepared known-unsent handle remains.


def test_repeat_domain_refusal_keeps_nonce_and_attempts(composition,monkeypatch):
    case=active(composition);sender=case.sender
    # A stale/buggy adapter offer is denied before cipher even after the old
    # request settled. Normal Flow never reuses its cleared epoch proof.
    next_seq=next(seq for seq,p in sender.flow._in_flight.items()if p.selective_evidence)
    domain=next(iter(sender._repair_train_domains))
    sender._repair_train_domains.add((*domain[:-1],next_seq))
    sender.step()
    assert sender.counts['repair_train_noneligible_refused']==1
    assert case.journals[0].snapshot()['attempts']==sender.owner.rf_attempts==9
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0]==9
    assert not sender._repair_train_active


@pytest.mark.parametrize('enabled',[False,True])
def test_half_duplex_many_holes_never_exceed_eight_in_repair_turn(tmp_path,enabled):
    counts=[]
    def observe(index,endpoint,now):
        if index==0:
            counts.append(len(endpoint._repair_train_domains))
            assert len(endpoint._repair_train_domains)<=8
    results,air=model.exercise(tmp_path,train_depth=8,repair_train=enabled,repeat_retry=not enabled,
        host_pump=True,receipt_confirmation=True,sender_cap=96,payload=b'x'*(24*193),
        drop_data={i:1 for i in range(9,18)},observe=observe)
    model.assert_complete(results,air)
    # The long injected original loss run already collides at DATA15 / ACK9
    # in v7. It happens before any repair, identically in both policy arms;
    # preserve that control rather than claiming the model is collision-free.
    collisions=[f for f in air.frames if f.collision]
    assert {(f.inner.get('seq'),f.inner.get('ack_seq'))for f in collisions}=={(15,None),(None,9)}
    attempts={}
    for frame in air.frames:
        if 'seq' in frame.inner:
            seq=frame.inner['seq'];attempts[seq]=attempts.get(seq,0)+1
            if attempts[seq]>1:assert not frame.collision
    assert air.channels[0].peak<=8
    assert results[0]['snapshot']['retransmits']==9
    if enabled:
        assert max(counts)==7  # Eighth handover immediately closes and clears the bounded set.
        assert results[0]['snapshot']['counts']['repair_train_extra']==7
    else:assert max(counts)==0
