"""New local capability, bounded static host slots and explicit turn lifecycle."""
from dataclasses import replace
import pytest
from meshticulum.bench.burst_protocol import (POLICY_TRAIN24,POLICY_TRAIN32,CAPABILITY24,CAPABILITY_TRAIN32,
 policy_for_train32,validate_policy,capability_request_body,check_capability)
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.serial_protocol import Frame,Message
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.tx_burst import BurstContractError,Stage
from tests.test_burst_worker import setup,Channel,PROFILE,BOOT,VERSION
from tests.test_burst_runtime import composition
SF5=replace(PROFILE,spreading_factor=5,preamble=32)

def test_exact_new_policy_and_old_firmware_refused_before_arm():
 assert policy_for_train32()==dict(POLICY_TRAIN24,schema='meshticulum.data-burst-policy.v11',train_frames=32,host_pending_limit=3,explicit_turn_retirement=True)
 assert capability_request_body(8,train_frames=32)==b'\x08\x20'
 assert CAPABILITY_TRAIN32[:7]==b'MBT3\x08\x20\x07'
 c=Channel(train_depth=8,train_frames=24,profile=SF5);w=BurstSerialWorker(c,policy=POLICY_TRAIN32)
 with pytest.raises((HardwareError,BurstContractError)):w.discover(expected_board_id=1,expected_version=VERSION)
 assert c.attempts==c.arm_request==0 and w.journal is None
 with pytest.raises(BurstContractError):check_capability(Frame(Message.BURST_CAPABILITIES_REPLY,BOOT,1,CAPABILITY24),boot=BOOT,request=1,depth=8,train_frames=32)

@pytest.mark.parametrize('change',[dict(host_pending_limit=4),dict(host_pending_limit=True),dict(max_depth=3),dict(train_frames=33),dict(explicit_turn_retirement=False),dict(max_hold_us=2000001),dict(inter_packet_rx_us=4999),dict(reverse_listen_us=99999)])
def test_no_relaxed_policy(change):
 with pytest.raises(BurstContractError):validate_policy(dict(POLICY_TRAIN32,**change))

def test_three_slots_and_empty_release_cannot_refresh_deadline(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True)
 rows=[w.queue_data(bytes([i])*255,logical_id=bytes([i+1])*32) for i in range(3)]
 original=w._burst_deadline;clocks=sum(x.type is Message.BURST_CLOCK for x in c.writes)
 assert not w.has_capacity and len(w.pending_physical)==3
 with pytest.raises(HardwareError,match='burst_queue_capacity'):w.queue_data(b'x',logical_id=b'z'*32)
 assert j.snapshot()['attempts']==3
 for r in rows:c.event(r.request,Stage.RF_DONE)
 w.drain_physical()
 assert w.drained_train_pending and not w.has_capacity and w._burst_deadline==original and w._burst_count==3
 with pytest.raises(HardwareError,match='burst_queue_capacity'):w.queue_data(b'x',logical_id=b'z'*32)
 assert sum(x.type is Message.BURST_CLOCK for x in c.writes)==clocks
 w.retire_drained_train();assert w.has_capacity and w._burst_deadline is None
 assert sum(x.type is Message.BURST_CLOCK for x in c.writes)==clocks and j.snapshot()['attempts']==3


def test_partial_write_cannot_retire_unknown(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True);c.fault='partial_write'
 assert w.queue_data(b'x',logical_id=b'x'*32).outcome=='UNKNOWN'
 with pytest.raises(HardwareError):w.retire_drained_train()
 assert w.fenced and j.snapshot()['attempts']==1


def test_budget_refusal_does_not_clear_original_turn(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True)
 first=w.queue_data(b'a',logical_id=b'a'*32);end=w._burst_deadline;c.fault='budget_refused'
 assert w.queue_data(b'b',logical_id=b'b'*32).outcome=='REFUSED'
 assert w._burst_deadline==end and w.train_admission_refused
 c.event(first.request,Stage.RF_DONE);w.drain_physical()
 with pytest.raises(HardwareError):w.retire_drained_train()
 w.retire_refused_train();assert w._burst_deadline==end and not w.has_capacity
 w.retire_drained_train();assert w._burst_deadline is None and j.snapshot()['attempts']==2


def test_runtime_establishes_reverse_hold_before_explicit_retirement(composition,monkeypatch):
 obj=composition(train_depth=8,train32=True)
 sender=obj.sender;w=sender.worker;c=obj.channels[0]
 row=w.queue_data(b'x',logical_id=b'x'*32);c.event(row.request,Stage.RF_DONE)
 # This worker-only row is not owned by BenchFlow; collect uses no synthetic flow result.
 w.drain_physical();observed=[];original=w.retire_drained_train
 prior_hold=sender.clock()[1]+sender.sender_control_hold_us+123456
 sender.next_send_us=prior_hold
 def retire():
  assert sender.next_send_us>=prior_hold
  observed.append(w._burst_deadline);original()
 monkeypatch.setattr(w,'retire_drained_train',retire)
 sender._poll_physical();assert observed and sender.counts['explicit_drained_train_turn']==1
 assert sender.burst_frames==32 and w._host_pending_limit==3

@pytest.mark.parametrize('field,value',[('_host_pending_limit',8),('_host_pending_limit',3.0),('_explicit_turn_retirement',False)])
def test_policy_tamper_refused_before_attempt(setup,field,value):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True);setattr(w,field,value)
 with pytest.raises(HardwareError,match='burst_policy_changed'):w.queue_data(b'x',logical_id=b'x'*32)
 assert j.snapshot()['attempts']==0 and c.attempts==0

@pytest.mark.parametrize('faults',[{},dict(drop_data={4:1}),dict(refuse_budget_at=9)])
def test_actual_encrypted_flow_runtime_closes_bounded_turns(tmp_path,faults):
 from tests import test_burst_sf5_recovery as model
 results,air=model.exercise(tmp_path,train_depth=8,train32=True,host_pump=True,
   receipt_confirmation=True,sender_cap=160,payload=b'x'*(64*193),**faults)
 model.assert_complete(results,air)
 assert air.channels[0].peak<=3
 assert results[0]['snapshot']['counts']['explicit_drained_train_turn']>0
 for result,channel in zip(results,air.channels):
  assert result['snapshot']['attempt_journal']['attempts']>=channel.attempts

@pytest.mark.parametrize('marker_kind',['matching','stale_turn','new_data','refused'])
def test_progress_marker_turn_binding_and_refusal_floor(composition,marker_kind):
 obj=composition(train_depth=8,train32=True);s=obj.sender;w=s.worker;c=obj.channels[0]
 first=w.queue_data(b'a',logical_id=b'a'*32);marker=w.train_identity
 if marker_kind=='stale_turn':marker=(marker[0],marker[1]-1,*marker[2:])
 if marker_kind=='new_data':second=w.queue_data(b'b',logical_id=b'b'*32);c.event(second.request,Stage.RF_DONE)
 c.event(first.request,Stage.RF_DONE);w.drain_physical()
 s._train_progress_receipt=marker;s.next_send_us=s.clock()[1]+1234
 prior=s.next_send_us;s._refused_turn_until_us=prior+5678 if marker_kind=='refused' else 0
 s._poll_physical()
 if marker_kind in ('matching','refused'):assert s.next_send_us==max(prior,s._refused_turn_until_us)
 else:assert s.next_send_us>prior
 assert s._train_progress_receipt is None and w._burst_deadline is None


def test_authenticated_progress_before_delayed_release_has_no_second_hold(tmp_path):
 from tests import test_burst_sf5_recovery as model
 checked=[];installed=[]
 def observe(index,endpoint,now):
  if index or installed:return
  installed.append(True);w=endpoint.worker;flush=w._flush_releases;retire=w.retire_drained_train
  def delayed_flush():
   if endpoint.counts['rx_ACK_progress']>0:flush()
  def checked_retire():
   if endpoint._train_progress_receipt==w.train_identity:
    checked.append(w.train_identity)
   retire()
  w._flush_releases=delayed_flush;w.retire_drained_train=checked_retire
 results,air=model.exercise(tmp_path,train_depth=8,train32=True,host_pump=True,
   receipt_confirmation=True,sender_cap=160,payload=b'x'*(40*193),observe=observe)
 model.assert_complete(results,air);assert checked
 assert air.channels[0].peak<=3


def test_delayed_prior_flight_ack_preserves_existing_progress_permission(tmp_path):
 from tests import test_burst_sf5_recovery as model
 state={}
 def observe(index,endpoint,now):
  if index:return
  w=endpoint.worker
  if not state:
   # Obtain the actual model radio through the worker's original channel.
   channel=next(v for v in vars(w).values() if isinstance(v,model.RadioChannel))
   offer=channel.offer
   state.update(channel=channel,offer=offer)
   def defer(frame):
    if frame.type is Message.RECEIVE:
     inner=channel.air.sealed.get(frame.body[4:],{})
     if inner.get('ack_seq',0)>0 and not state.get('delivered'):
      if 'saved' not in state:
       state.update(saved=frame,old_generation=w._turn_generation,
                    progress_before=endpoint.counts['rx_ACK_progress'])
      return
    offer(frame)
   channel.offer=defer
  if ('saved' in state and not state.get('delivered') and w.train_identity is not None
      and w._turn_generation>state['old_generation']):
   state['delivered']=True;state['new_identity']=w.train_identity
   state['offer'](state['saved'])
  if state.get('delivered') and endpoint.counts['rx_ACK_progress']>state['progress_before']:
   state['progress_observed']=True
 results,air=model.exercise(tmp_path,train_depth=8,train32=True,host_pump=True,
   receipt_confirmation=True,sender_cap=160,payload=b'x'*(40*193),observe=observe)
 model.assert_complete(results,air)
 assert state.get('progress_observed') and state['new_identity'][1]>state['old_generation']
 assert air.channels[0].peak<=3
