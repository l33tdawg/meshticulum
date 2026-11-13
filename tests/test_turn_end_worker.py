"""Final-admission reservation uses original worker identity and paid lifecycle."""
from dataclasses import replace
import pytest
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Message
from meshticulum.bench.tx_burst import Stage
from tests.test_burst_worker import setup,PROFILE
SF5=replace(PROFILE,spreading_factor=5,preamble=32)

def queued(w,seal):
 from meshticulum.bench.wire import HEADER,MAGIC,VERSION
 packet=HEADER.pack(MAGIC,VERSION,4,0,b's'*16,1,len(seal.plaintext))+seal.plaintext+b't'*16
 w.bind_turn_end_packet(seal,packet)
 return w.queue_turn_end(seal,packet)

def reserve(w):return w.reserve_turn_end(logical_id=b'b'*32,owner_frame_id=2,plaintext=b'plain')

def test_first_admission_has_no_original_turn_and_keeps_fallback(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True)
 clocks=sum(x.type is Message.BURST_CLOCK for x in c.writes)
 with pytest.raises(HardwareError,match='open_train'):reserve(w)
 assert w.has_capacity and j.snapshot()['attempts']==0
 assert sum(x.type is Message.BURST_CLOCK for x in c.writes)==clocks

def test_known_open_final_admission_blocks_until_exact_drain_and_retire(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True)
 first=w.queue_data(b'a',logical_id=b'a'*32,owner_frame_id=1)
 identity=w.train_identity;clocks=sum(x.type is Message.BURST_CLOCK for x in c.writes)
 seal=reserve(w);assert not w.has_capacity
 last=queued(w,seal);assert last.outcome=='QUEUED'
 assert w.train_identity==(*identity[:3],identity[3]+1) and not w.has_capacity
 for result in (first,last):c.event(result.request,Stage.RF_DONE)
 w.drain_physical();assert w._turn_end_seal is seal and not w.has_capacity
 w.retire_drained_train();assert w._turn_end_seal is None and w.has_capacity
 assert sum(x.type is Message.BURST_CLOCK for x in c.writes)==clocks and j.snapshot()['attempts']==2

def test_raw_queue_callback_reentry_latches_even_when_callback_catches(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True);w.queue_data(b'a',logical_id=b'a'*32)
 seal=reserve(w);old=w.authorize
 def callback():
  with pytest.raises(HardwareError):w.queue_data(b'evil',logical_id=b'x'*32)
  return old()
 w.authorize=callback
 with pytest.raises(HardwareError):queued(w,seal)
 assert w._turn_end_seal is seal and w._turn_end_failed and j.snapshot()['attempts']==1

@pytest.mark.parametrize('name',['_turn_generation','_burst_deadline','_burst_count'])
def test_original_identity_mutation_refuses(setup,name):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True);w.queue_data(b'a',logical_id=b'a'*32)
 seal=reserve(w);setattr(w,name,getattr(w,name)+1)
 with pytest.raises(HardwareError):queued(w,seal)
 assert w._turn_end_seal is seal and w._turn_end_failed and j.snapshot()['attempts']==1

@pytest.mark.parametrize('fault',['partial_write','budget_refused'])
def test_failed_final_candidate_never_reopens_or_refunds(setup,fault):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True);w.queue_data(b'a',logical_id=b'a'*32)
 seal=reserve(w);original=w._burst_deadline;c.fault=fault
 with pytest.raises(HardwareError):queued(w,seal)
 assert w._turn_end_seal is seal and w._turn_end_failed and w._burst_deadline==original
 assert j.snapshot()['attempts']==2 and not w.has_capacity

def test_retirement_failure_keeps_seal(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True);one=w.queue_data(b'a',logical_id=b'a'*32)
 seal=reserve(w);two=queued(w,seal)
 for r in (one,two):c.event(r.request,Stage.RF_DONE)
 w.drain_physical();w.assert_drained=lambda:(_ for _ in ()).throw(HardwareError('test'))
 with pytest.raises(HardwareError):w.retire_drained_train()
 assert w._turn_end_seal is seal and w._burst_deadline==seal.deadline

def test_retirement_callback_mutation_cannot_clear_original_seal(setup):
 w,c,j,_=setup(profile=SF5,train_depth=8,train32=True);one=w.queue_data(b'a',logical_id=b'a'*32)
 seal=reserve(w);two=queued(w,seal)
 for r in (one,two):c.event(r.request,Stage.RF_DONE)
 w.drain_physical();original=w.assert_drained
 def changed():
  result=original();w._turn_generation+=1;return result
 w.assert_drained=changed
 with pytest.raises(HardwareError):w.retire_drained_train()
 assert w._turn_end_seal is seal and w._burst_deadline==seal.deadline and w._turn_end_failed
