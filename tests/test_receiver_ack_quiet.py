from unittest.mock import patch
import pytest
from meshticulum.bench.wire import WireError
from tests.test_burst_runtime import composition,queue_one

@pytest.mark.parametrize('enabled', [False, True])
def test_partial_train_quiet_boundary_and_original_guards(composition,enabled):
 case=composition(data=b'x'*10000,train_depth=8,train24=True)
 r=case.receiver
 original=(r.burst_quiet_us,r.burst_max_hold_us,r.sender_control_hold_us,case.authorities[1].deadlines)
 if enabled:r.set_receiver_ack_quiet_us(150000)
 quiet=150000 if enabled else 250000
 assert r.snapshot()['timing_policy']['receiver_ack_quiet_us']==quiet
 queue_one(case);case.channels[0].finish_packets();r.step()
 assert 0 < r.burst_received < 24
 assert r.ack_due_us-r.last_activity_us==quiet
 before=r.counts['tx_RF_DONE'];due=r.ack_due_us
 with patch.object(case.workers[1],'receive',return_value=None):
  case.clock.advance(due-1-case.clock()[1]);r.step()
  assert r.counts['tx_RF_DONE']==before
  case.clock.advance(due-case.clock()[1]);r.step()
  assert r.counts['tx_RF_DONE']==before+1
 assert (r.burst_quiet_us,r.burst_max_hold_us,r.sender_control_hold_us,case.authorities[1].deadlines)==original
 assert original[:3]==(250000,2000000,1000000)

@pytest.mark.parametrize('value',[True,None,100000,149999,150001,150000.0])
def test_invalid_values_refused(composition,value):
 r=composition(train_depth=8,train24=True).receiver
 with pytest.raises(WireError,match='invalid_receiver_ack_quiet_option'):r.set_receiver_ack_quiet_us(value)

@pytest.mark.parametrize('options',[{},dict(train_depth=8),dict(train_depth=8,train12=True)])
def test_only_sf5_train24(composition,options):
 r=composition(**options).receiver
 with pytest.raises(WireError,match='invalid_receiver_ack_quiet_option'):r.set_receiver_ack_quiet_us(150000)

def test_setup_only_once_before_activity(composition):
 case=composition(train_depth=8,train24=True);r=case.receiver
 r.set_receiver_ack_quiet_us(150000)
 with pytest.raises(WireError):r.set_receiver_ack_quiet_us(150000)
 case=composition(train_depth=8,train24=True);case.receiver.step()
 with pytest.raises(WireError):case.receiver.set_receiver_ack_quiet_us(150000)

def test_full_object_completion_does_not_wait_for_quiet(composition):
 case=composition(data=b'x'*100,train_depth=8,train24=True);r=case.receiver
 r.set_receiver_ack_quiet_us(150000);queue_one(case);case.channels[0].finish_packets();r.step()
 assert r.flow.complete
 assert r.burst_received==0
 assert r.counts['tx_RF_DONE']>=2

def test_replayed_packet_cannot_extend_quiet_deadline(composition):
 case=composition(data=b'x'*10000,train_depth=8,train24=True);r=case.receiver
 r.set_receiver_ack_quiet_us(150000);queue_one(case)
 packet=next(iter(case.channels[0].packets.values()))
 case.channels[0].finish_packets();r.step();due=r.ack_due_us
 case.clock.advance(1000);case.channels[0].deliver(packet);r.step()
 assert r.ack_due_us==due
 assert r.counts['rx_refused']>=1

@pytest.mark.parametrize('value',[True,149999,150000.0])
def test_public_invalid_option_refused_before_factory(value):
 from tests.test_radio_file_example import example
 with pytest.raises(WireError,match='invalid_receiver_ack_quiet_option'):
  example.FileTransfer(None,None,None,None,None,None,clock=None,window=None,chunk=0,receiver_ack_quiet_us=value)

def test_partial_train_keeps_original_two_second_cap(composition):
 case=composition(data=b'x'*10000,train_depth=8,train24=True);r=case.receiver
 r.set_receiver_ack_quiet_us(150000);queue_one(case);case.channels[0].finish_packets();r.step()
 case.sender.step()
 # Model an already-long partial train; next authenticated DATA may update
 # quiet eligibility but may not renew the original train-start cap.
 r.burst_started_us=case.clock()[1]-1800000
 cap=r.burst_started_us+2000000
 case.channels[0].finish_packets();r.step()
 assert r.ack_due_us==min(r.last_activity_us+150000,cap)
 assert r.ack_due_us<=cap
