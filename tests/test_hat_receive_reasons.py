"""Observed software branches with exact SPI trace parity; no RF claims."""
import json
import pytest
from meshticulum.bench.rx_reasons import ReceiveReasons, NAMES, LIMIT
from meshticulum.bench.sx1262 import CRC_ERROR, HEADER_ERROR, TIMEOUT
from meshticulum.bench.linux_hat import HardwareError
from tests.test_meshticulum_bench_sx1262 import configured
from tests.test_hat_worker_immediate_receive import rig

@pytest.mark.parametrize('flags,payload,key',[(2,b'x','physical_packet_returned'),(2,b'','zero_length'),
 (CRC_ERROR,b'x','irq_crc'),(HEADER_ERROR,b'x','irq_header'),
 (CRC_ERROR|HEADER_ERROR,b'x','irq_crc_header'),(TIMEOUT,b'x','irq_timeout')])
def test_existing_spi_trace_and_outcomes_unchanged(flags,payload,key):
 traces=[]
 for enabled in (False,True):
  chip,radio=configured();radio.start_receive(authority_deadline=160)
  reasons=ReceiveReasons() if enabled else None;radio.receive_reasons=reasons
  chip.flags=flags;chip.rx=payload;start=len(chip.commands)
  result=radio.poll_receive();traces.append((chip.commands[start:],result,radio.rx_errors,radio.fenced))
  if enabled:assert reasons.snapshot()['counts'][key]==1
 assert traces[0]==traces[1]


def test_failure_records_overlap_and_does_not_mask_original_error():
 traces=[]
 for enabled in (False,True):
  chip,radio=configured();radio.start_receive(authority_deadline=160)
  reasons=ReceiveReasons() if enabled else None;radio.receive_reasons=reasons
  original=chip.transfer;chip.flags=2;chip.rx=b'x';start=len(chip.commands)
  def transfer(command):
   if command[0]==0x13:raise OSError('exact read failure')
   return original(command)
  chip.transfer=transfer
  with pytest.raises(OSError,match='exact read failure'):radio.poll_receive()
  traces.append((chip.commands[start:],radio.fenced))
  if enabled:
   counts=reasons.snapshot()['counts'];assert counts['poll_failure']==counts['packet_read_failure']==1
 assert traces[0]==traces[1]


def test_worker_rearm_and_revocation_overlap(rig):
 chip,journal,worker=rig
 reasons=ReceiveReasons();worker._receive_reasons=worker.radio.receive_reasons=reasons
 assert worker.receive(timeout=0) is None
 chip.flags=2;chip.rx=b'physical only';assert worker.receive(timeout=0)[1].packet==chip.rx
 counts=worker.receive_reason_snapshot()['counts']
 assert counts['rearm_attempt']==2 and counts['physical_packet_returned']==1
 before=journal.snapshot();worker._test_authorize_state['callback']=lambda:False
 with pytest.raises(HardwareError):worker.receive(timeout=0)
 assert reasons.snapshot()['counts']['receive_failure']==1
 assert journal.snapshot()==before and chip.tx_calls==0


def test_fixed_saturating_counters_and_detached_snapshot():
 r=ReceiveReasons();r._counts[0]=LIMIT;r.record(NAMES[0]);v=r.snapshot()
 assert v['saturated_mask']==1 and v['counts'][NAMES[0]]==LIMIT
 v['counts'][NAMES[0]]=0;assert r.snapshot()['counts'][NAMES[0]]==LIMIT
 assert len(json.dumps(r.snapshot()))<1024


def test_default_worker_has_no_diagnostics(rig):
 assert rig[2].receive_reason_snapshot() is None


def test_public_flag_and_file_binding():
 import argparse
 from types import SimpleNamespace
 from tests.test_radio_file_example import example
 parser=argparse.ArgumentParser();example.add_host_timing_argument(parser)
 assert not parser.parse_args([]).hat_receive_reasons
 assert parser.parse_args(['--hat-receive-reasons']).hat_receive_reasons
 args=SimpleNamespace();before=example.FileHardware._binding(args)
 args.hat_receive_reasons=True
 assert example.FileHardware._binding(args)!=before

@pytest.mark.parametrize('failure',[False,True])
def test_worker_io_trace_parity_including_rearm_failure(tmp_path,failure):
 from meshticulum.bench.hat_worker import HATWorker,journal_binding
 from meshticulum.bench.attempts import AttemptJournal
 from tests.test_meshticulum_bench_sx1262 import Chip,PROFILE
 traces=[]
 for enabled in (False,True):
  folder=tmp_path/str(enabled);folder.mkdir(mode=0o700)
  chip=Chip();context=b'c'*32
  journal=AttemptJournal(folder/'a.db',binding=journal_binding(context,chip.boot_id,PROFILE),max_attempts=20,max_airtime_us=10000000,create=True)
  worker=HATWorker(chip,journal=journal,profile=PROFILE,context_digest=context,authorize=lambda:True,
   authority_deadline=200,antenna_confirmed=True,monotonic=lambda:chip.time,time_basis='model-monotonic',receive_reasons=enabled)
  try:
   worker.receive(timeout=0);chip.flags=2;chip.rx=b'packet';start=len(chip.commands)
   original=chip.transfer
   def transfer(command):
    if failure and command[0]==0x82:raise OSError('exact rearm fault')
    return original(command)
   chip.transfer=transfer
   if failure:
    with pytest.raises(OSError,match='exact rearm fault'):worker.receive(timeout=0)
   else:assert worker.receive(timeout=0)[1].packet==chip.rx
   traces.append((chip.commands[start:],worker.fenced,worker.radio.fenced,journal.snapshot(),chip.tx_calls))
   if enabled:
    counts=worker.receive_reason_snapshot()['counts']
    assert counts['physical_packet_returned']==1 and counts['rearm_attempt']==2
    assert counts['rearm_failure']==counts['receive_failure']==int(failure)
  finally:journal.close()
 assert traces[0]==traces[1]

@pytest.mark.parametrize('value',[1,None,'yes'])
def test_worker_strict_optin_before_any_io(value):
 from meshticulum.bench.hat_worker import HATWorker
 with pytest.raises(HardwareError,match='invalid_hat_receive_reasons'):
  HATWorker(None,journal=None,profile=None,context_digest=None,authorize=None,
   authority_deadline=None,antenna_confirmed=None,receive_reasons=value)
