"""No device actions: exact modeled SPI behavior with opt-in observations."""
import argparse
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from meshticulum.bench.rx_timeline import ReceiveTimeline, CAPACITY, DETAIL_LIMIT
from meshticulum.bench.linux_hat import HardwareError
from tests.test_hat_worker_immediate_receive import rig


def test_fixed_capacity_overflow_and_detached_snapshot():
 t=ReceiveTimeline()
 for _ in range(CAPACITY+2):
  t.begin();t.finish('empty')
 s=t.snapshot();assert s['calls']==CAPACITY+2 and s['evicted']==2 and not s['complete']
 assert s['retained']==CAPACITY and s['rows'][0]['call']==3
 s['rows'][0]['outcome']='changed';assert t.snapshot()['rows'][0]['outcome']=='empty'


def test_detail_overflow_clock_failure_and_active_not_complete():
 t=ReceiveTimeline();t.begin();assert not t.snapshot()['complete']
 for _ in range(DETAIL_LIMIT+1):
  t.irq(32);item=t.rearm_start();t.rearm_finish(item,True)
 assert t.snapshot()['detail_dropped']==2
 with patch('meshticulum.bench.rx_timeline.time.monotonic_ns',side_effect=OSError('clock')):
  t.finish('failure')
 s=t.snapshot();assert s['clock_errors']==1 and not s['complete'] and s['rows'][0]['finished_us'] is None


def test_hash_anchor_only_last_packet_without_new_hashing():
 t=ReceiveTimeline();t.begin();t.finish('packet');t.anchor('a'*64);t.anchor('b'*64)
 assert t.snapshot()['rows'][0]['packet_sha256']=='a'*64
 t.begin();t.finish('empty');t.anchor('c'*64)
 assert t.snapshot()['rows'][1]['packet_sha256'] is None


def test_default_off_no_diagnostic_clock(rig):
 worker=rig[2]
 with patch('meshticulum.bench.rx_timeline.time.monotonic_ns',side_effect=AssertionError('diagnostic clock called')):
  assert worker.receive(timeout=0) is None
 assert worker.receive_timeline_snapshot() is None


@pytest.mark.parametrize('value',[1,None,'yes'])
def test_strict_option_before_io(value):
 from meshticulum.bench.hat_worker import HATWorker
 with pytest.raises(HardwareError,match='invalid_hat_receive_timeline'):
  HATWorker(None,journal=None,profile=None,context_digest=None,authorize=None,
            authority_deadline=None,antenna_confirmed=None,receive_timeline=value)


def test_public_option_binding_and_usb_refusal_before_io():
 from tests.test_radio_file_example import example
 p=argparse.ArgumentParser();example.add_host_timing_argument(p)
 assert not p.parse_args([]).hat_receive_timeline
 assert p.parse_args(['--hat-receive-timeline']).hat_receive_timeline
 args=SimpleNamespace();before=example.FileHardware._binding(args);args.hat_receive_timeline=True
 assert example.FileHardware._binding(args)!=before
 # Isolate only preexisting plan-limit parsing; invalid interface option must reject before hardware.
 with patch.object(example.OperationLimits,'from_plan',return_value=None),patch.object(example,'operation_journal_limits',return_value=None):
  with pytest.raises(ValueError,match='invalid_hat_receive_timeline_option'):
   example.run_object(SimpleNamespace(interface='usb',hat_receive_timeline=True),b'{}',None,None,None,None,None)


@pytest.mark.parametrize('flags,failure',[(0,None),(2,None),(32,None),(512,None),(2,'read'),(2,'rearm')])
def test_worker_spi_and_failure_parity(tmp_path,flags,failure):
 from meshticulum.bench.hat_worker import HATWorker,journal_binding
 from meshticulum.bench.attempts import AttemptJournal
 from tests.test_meshticulum_bench_sx1262 import Chip,PROFILE
 outcomes=[]
 for enabled in (False,True):
  folder=tmp_path/str(enabled);folder.mkdir(mode=0o700);chip=Chip();context=b't'*32
  j=AttemptJournal(folder/'a.db',binding=journal_binding(context,chip.boot_id,PROFILE),max_attempts=20,max_airtime_us=10000000,create=True)
  w=HATWorker(chip,journal=j,profile=PROFILE,context_digest=context,authorize=lambda:True,authority_deadline=200,
              antenna_confirmed=True,monotonic=lambda:chip.time,time_basis='model-monotonic',receive_timeline=enabled)
  try:
   w.receive(timeout=0);chip.flags=flags;chip.rx=b'physical packet';start=len(chip.commands);original=chip.transfer
   def transfer(command):
    if (failure=='read' and command[0]==0x13) or (failure=='rearm' and command[0]==0x82):raise OSError('exact fault')
    return original(command)
   chip.transfer=transfer
   if failure:
    with pytest.raises(OSError,match='exact fault'):w.receive(timeout=0)
    returned=None
   else:returned=w.receive(timeout=0)
   outcomes.append((chip.commands[start:],returned,w.fenced,w.radio.fenced,j.snapshot(),chip.tx_calls))
   if enabled:
    s=w.receive_timeline_snapshot();row=s['rows'][-1]
    assert s['complete'] and not s['active_call'] and row['started_us']<=row['finished_us']
    assert row['outcome']==('failure' if failure else 'packet' if flags==2 else 'empty')
    if flags:assert row['irq'][0]['flags']==flags
    if failure=='rearm':assert row['rearm'][-1]['complete'] is False
  finally:j.close()
 assert outcomes[0]==outcomes[1]


def test_authority_revocation_records_failure_preserves_debt(rig):
 chip,j,w=rig;t=ReceiveTimeline();w._receive_timeline=w.radio.receive_timeline=t
 before=j.snapshot();w._test_authorize_state['callback']=lambda:False
 with pytest.raises(HardwareError):w.receive(timeout=0)
 assert j.snapshot()==before and chip.tx_calls==0 and t.snapshot()['rows'][-1]['outcome']=='failure'

@pytest.mark.parametrize('bad',[-1,1.5,float('nan'),2**63,True])
def test_invalid_clock_marks_incomplete_without_authority_change(bad):
 t=ReceiveTimeline()
 with patch('meshticulum.bench.rx_timeline.time.monotonic_ns',return_value=bad):t.begin();t.finish('empty')
 assert t.snapshot()['clock_errors']==2 and not t.snapshot()['complete']


def test_regressing_clock_marks_incomplete():
 t=ReceiveTimeline()
 with patch('meshticulum.bench.rx_timeline.time.monotonic_ns',side_effect=[2000,1000]):t.begin();t.finish('empty')
 assert t.snapshot()['clock_errors']==1 and not t.snapshot()['complete']


def test_maximum_timeline_serialized_geometry_and_summary():
 import json
 t=ReceiveTimeline()
 with patch('meshticulum.bench.rx_timeline.time.monotonic_ns',return_value=2**63-1):
  for _ in range(CAPACITY):
   t.begin()
   for _ in range(DETAIL_LIMIT):t.irq(65535);item=t.rearm_start();t.rearm_finish(item,False)
   t.finish('failure');t._rows[-1]['packet_sha256']='f'*64
 raw=json.dumps(t.snapshot(),separators=(',',':')).encode()
 assert len(raw)<1600000
 assert len(raw)+2000000<4000000 # Leaves the former2MB C snapshot envelope intact.
 assert 'rows' not in t.summary() and len(json.dumps(t.summary()))<2048
 print('maximum current-schema timeline bytes',len(raw))


def test_real_flow_authenticated_data_control_anchor_and_reject(tmp_path,monkeypatch):
 from tests.test_radio_file_confirmation import confirming_pair
 from tests.test_radio_file_example import finish,close_pair
 from meshticulum.bench.sx1262 import RawReceive
 opened,transfers,workers,clock,_,_=confirming_pair(tmp_path,monkeypatch)
 anchors=[[],[]]
 try:
  for index,w in enumerate(workers):
   def anchor(digest,index=index):
    event=transfers[index].endpoint.events[-1]
    assert event['event']=='rx' and event['packet_sha256']==digest
    anchors[index].append((event['outer_kind'],digest))
   w.anchor_receive_timeline=anchor
  # Malformed physical ingress must never attach an authenticated anchor.
  original=workers[1].receive;first=[True]
  def receive(timeout=0):
   if first[0]:first[0]=False;return clock()[1]/1e6,RawReceive(-60,8,b'bad')
   return original(timeout)
  workers[1].receive=receive
  transfers[1].step();assert anchors[1]==[];clock.advance(20000)
  finish(transfers,clock)
  assert any(kind=='DATA' for kind,_ in anchors[1])
  assert any(kind=='CONTROL' for kind,_ in anchors[1])
  for i in range(2):
   events=[e for e in transfers[i].endpoint.events if e['event']=='rx']
   assert anchors[i]==[(e['outer_kind'],e['packet_sha256']) for e in events]
 finally:close_pair(opened)
