"""Explicit train length, scalar authority and common-deadline controls."""
from dataclasses import replace
import pytest
from meshticulum.bench.burst_protocol import (CAPABILITY,CAPABILITY8,CAPABILITY12,OPEN,
    POLICY_REPAIR_TRAIN,POLICY_TRAIN12,capability_for_depth,capability_request_body,
    policy_for_train12,policy_train_frames,policy_depth,validate_policy,open_frame,OpenBinding,check_capability)
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Frame,Message
from meshticulum.bench.tx_burst import BurstContractError,Stage
from meshticulum.bench.wire import FrameKind,WireError
from tests.test_burst_worker import setup,Channel,BOOT,VERSION,PROFILE
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model

SF5=replace(PROFILE,spreading_factor=5,preamble=12)

def test_only_explicit_v9_uses_twelve_with_eight_slots():
    assert policy_for_train12()==dict(POLICY_REPAIR_TRAIN,schema='meshticulum.data-burst-policy.v9',train_frames=12)
    assert policy_depth(POLICY_TRAIN12)==8 and policy_train_frames(POLICY_TRAIN12)==12
    assert policy_train_frames(POLICY_REPAIR_TRAIN)==8
    copied=policy_for_train12();copied['train_frames']=8
    assert policy_for_train12()['train_frames']==12
    with pytest.raises(BurstContractError):validate_policy(copied)

@pytest.mark.parametrize('change',[{'train_frames':True},{'train_frames':12.0},{'train_frames':16},
    {'max_depth':12},{'max_depth':4},{'max_hold_us':2_000_001},{'reverse_listen_us':99_999},
    {'inter_packet_rx_us':4999},{'flow_window_frames':48},{'selective_repair_train':False},{'extra':True}])
def test_policy_refuses_shape_and_guard_changes(change):
    with pytest.raises(BurstContractError):validate_policy(dict(POLICY_TRAIN12,**change))

def test_capability_and_open_exact_new_shape_preserve_old_bytes():
    assert capability_request_body()==b'' and capability_request_body(8)==b'\x08'
    assert capability_for_depth()==CAPABILITY and capability_for_depth(8)==CAPABILITY8
    assert capability_request_body(8,train_frames=12)==b'\x08\x0c'
    assert len(CAPABILITY12)==25 and CAPABILITY12[:7]==b'MBT1\x08\x0c\x07'
    for train,prefix in [(8,b'\x08'),(12,b'\x08\x0c')]:
        f=open_frame(BOOT,2,b'x'*32,1,3_000_000,depth=8,train_frames=train)
        assert f.body==prefix+OPEN.pack(b'x'*32,1,3_000_000)
        reply=replace(f,type=Message.BURST_OPEN_REPLY)
        assert OpenBinding.from_frame(reply,boot=BOOT,request=2,depth=8,train_frames=train).train_frames==train
        with pytest.raises(BurstContractError):OpenBinding.from_frame(reply,boot=BOOT,request=2,depth=8,train_frames=20-train)
    f=Frame(Message.BURST_CAPABILITIES_REPLY,BOOT,2,CAPABILITY12)
    check_capability(f,boot=BOOT,request=2,depth=8,train_frames=12)
    with pytest.raises(BurstContractError):check_capability(f,boot=BOOT,request=2,depth=8)

@pytest.mark.parametrize('depth,train',[(4,12),(8,16),(8,True),(8,12.0),(12,12)])
def test_invalid_native_shape_never_encodes(depth,train):
    with pytest.raises(BurstContractError):capability_request_body(depth,train_frames=train)
    with pytest.raises(BurstContractError):open_frame(BOOT,2,b'x'*32,1,3_000_000,depth=depth,train_frames=train)

def test_old_eight_firmware_refuses_before_arm_journal_or_attempt():
    channel=Channel(train_depth=8,profile=SF5);worker=BurstSerialWorker(channel,policy=POLICY_TRAIN12)
    with pytest.raises((HardwareError,BurstContractError)):worker.discover(expected_board_id=1,expected_version=VERSION)
    assert worker.journal is None and channel.attempts==0 and channel.arm_request==0

def test_twelve_scalar_admissions_reuse_eight_slots_without_resetting_common_deadline(setup):
    worker,channel,journal,_=setup(profile=SF5,train_depth=8,train12=True)
    results=[worker.queue_data(bytes([i])*255,logical_id=bytes([i+1])*32)for i in range(8)]
    original=worker._burst_deadline
    assert len(worker.pending_physical)==8 and not worker.has_capacity
    for i in range(4):channel.event(results[i].request,Stage.RF_DONE)
    worker.poll_physical();assert len(worker.pending_physical)==4 and worker.has_capacity
    for i in range(8,12):results.append(worker.queue_data(bytes([i])*255,logical_id=bytes([i+1])*32))
    assert len(worker.pending_physical)==8 and worker._burst_count==12 and not worker.has_capacity
    channel.event(results[4].request,Stage.RF_DONE);worker.poll_physical()
    assert len(worker.pending_physical)==7 and not worker.has_capacity
    before=journal.snapshot()
    with pytest.raises(HardwareError,match='burst_queue_capacity'):worker.queue_data(b'x',logical_id=b'z'*32)
    assert journal.snapshot()==before and worker._burst_deadline==original
    assert {r['descriptor'].deadline_us for r in worker._physical.values()}=={original}
    for r in results[5:]:channel.event(r.request,Stage.RF_DONE)
    worker.poll_physical();worker.assert_drained()
    assert worker._burst_deadline is None and worker._burst_count==0 and worker.has_capacity
    assert journal.snapshot()['attempts']==12 and len(worker._released_events)==len(worker._release_replies)==8
    assert all(r['released'] and r['state']=='RF_DONE'for r in journal.queued_records())

def test_unknown_after_slot_reuse_retains_debt_and_fences(setup):
    worker,channel,journal,_=setup(profile=SF5,train_depth=8,train12=True)
    old=[worker.queue_data(bytes([i]),logical_id=bytes([i+1])*32)for i in range(8)]
    channel.event(old[0].request,Stage.RF_DONE);worker.poll_physical();channel.fault='partial_write'
    result=worker.queue_data(b'ninth',logical_id=b'z'*32)
    assert result.outcome=='UNKNOWN' and journal.snapshot()['attempts']==9 and worker.fenced
    assert not worker.has_capacity and len(worker.pending_physical)==8

@pytest.mark.parametrize('preamble',[12,32])
def test_original_conservative_twelve_fit_keeps_two_second_bound(preamble):
    from meshticulum.bench.serial_worker import estimated_airtime_us
    profile=replace(SF5,preamble=preamble)
    fit=12*(estimated_airtime_us(profile,255)+100000+5000)+100000+50000
    assert fit==({12:1825680,32:1841040}[preamble]) and fit<=1980000
    assert 16*(estimated_airtime_us(profile,255)+105000)+150000>1980000

def test_peer_binding_changes_while_logical_credit_rto_authority_and_caps_do_not(composition):
    old=composition(train_depth=8,repair_train=True);new=composition(train_depth=8,train12=True)
    assert old.sender.burst_frames==8 and new.sender.burst_frames==12
    assert new.receiver.burst_max_hold_us==old.receiver.burst_max_hold_us==2_000_000
    assert new.receiver.burst_quiet_us==old.receiver.burst_quiet_us==250_000
    assert not old.sender.flow.limits.selective_stall_retry
    assert new.sender.flow.limits.selective_stall_retry
    assert not new.receiver.flow.limits.selective_stall_retry
    assert replace(new.sender.flow.limits,selective_stall_retry=False)==old.sender.flow.limits
    assert new.sender.snapshot()['timing_policy']['selective_stall_retry'] is True
    assert old.authorities[0].operation_limits==new.authorities[0].operation_limits
    assert old.authorities[0].contract.limits==new.authorities[0].contract.limits
    assert old.authorities[0].carrier_profile.initial_rto_seconds==new.authorities[0].carrier_profile.initial_rto_seconds==2.22
    assert old.workers[0]._depth==new.workers[0]._depth==8
    packet=old.sender.cipher.seal(b'other policy',FrameKind.DATA,authorize=lambda:True)
    with pytest.raises(WireError,match='authentication_failed'):new.receiver.cipher.open(packet,authorize=lambda:True)
    assert new.journals[0].snapshot()['attempts']==0

@pytest.mark.parametrize('faults',[{}, {'drop_data':{3:1,8:1,15:1}},
    {'drop_data':{3:2,8:1},'drop_ack_seqs':{3:1}}, {'drop_final_ack':True},
    {'host_work_us':(40_000,10_000)}])
def test_execution_model_twelve_stays_bounded_and_paid(tmp_path,faults):
    def observe(index,endpoint,now):
        assert endpoint.burst_frames==12 and endpoint.burst_sent<12
        if index==0:assert len(endpoint.worker.pending_physical)<=8
    results,air=model.exercise(tmp_path,train_depth=8,train12=True,host_pump=True,
        receipt_confirmation=True,sender_cap=96,payload=b'x'*(24*193),observe=observe,**faults)
    model.assert_complete(results,air)
    assert air.channels[0].peak<=8 and not air.channels[0].events
    for result,channel in zip(results,air.channels):
        assert result['snapshot']['attempt_journal']['attempts']==channel.attempts
        assert result['settled_states']==[('RF_DONE',channel.attempts)]
