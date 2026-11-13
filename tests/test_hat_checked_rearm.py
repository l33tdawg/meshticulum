import ctypes,struct
import pytest
from meshticulum.bench import linux_hat as lh
from meshticulum.bench.sx1262 import SX1262
from tests.test_meshticulum_bench_sx1262 import Chip,PROFILE
@pytest.fixture
def rig(monkeypatch):
 c=Chip();io=lh.LinuxHAT.__new__(lh.LinuxHAT);io.spi_fd=31;io.lines_fd=32;io.chip_fd=33;io.lock_fd=34;io._spi_hz=4000000;io.monotonic=c.monotonic;io.sleep=c.sleep
 def ioctl(fd,op,arg):
  if op==lh.SPI_MESSAGE:
   data=ctypes.string_at(arg.tx_buf,arg.len);reply=c.transfer(data);ctypes.memmove(arg.rx_buf,reply,len(reply));return len(reply)
  if op==lh.GPIO_SET_VALUES:
   v,m=struct.unpack('=QQ',arg)
   if m==1:c.reset(bool(v&1))
   elif m==2:c.receive_switch(bool(v&2))
   return 0
  if op==lh.GPIO_GET_VALUES:
   v,m=struct.unpack('=QQ',arg);arg[:]=struct.pack('=QQ',4 if c.busy() else 0,m);return 0
  raise AssertionError(op)
 monkeypatch.setattr(lh.fcntl,'ioctl',ioctl);return c,io

def setup(rig,on=True,clock=None):
 c,io=rig;r=SX1262(io,monotonic=clock or c.monotonic,checked_rearm=on);r.configure(PROFILE);r.start_receive(authority_deadline=c.time+10);return c,io,r

def drain(c,r,flags=2):c.flags=flags;out=r.poll_receive();assert not r.receiving;return out
LEGACY=[0x80,0xc0,0x02,0xc0,0x12,0x8c,0xc0,0x08,0xc0,0x82,0xc0]
@pytest.mark.parametrize('flags',[2,32,64,512])
def test_exact_commands(rig,flags):
 c,io,r=setup(rig);drain(c,r,flags);c.commands.clear();r.start_receive(authority_deadline=c.time+10);assert [x[0] for x in c.commands]==LEGACY[5:]
 surviving=list(c.commands)
 c.commands.clear();r.start_receive(authority_deadline=c.time+10);assert [x[0] for x in c.commands]==LEGACY
 assert c.commands[5:]==surviving

def test_default_off(rig):
 c,io,r=setup(rig,False);drain(c,r);c.commands.clear();r.start_receive(authority_deadline=c.time+10);assert [x[0] for x in c.commands]==LEGACY;assert not hasattr(io,'_radio_activity')
@pytest.mark.parametrize('action',['spi','reset','switch','status','failedspi','failedgpio'])
def test_intervening_activity(rig,action):
 c,io,r=setup(rig);drain(c,r)
 if action=='spi':io.transfer(b'\xc0\0')
 elif action=='reset':io.reset(True)
 elif action=='switch':io.receive_switch(True)
 elif action=='status':r.status()
 elif action=='failedspi':
  with pytest.raises(lh.HardwareError):io.transfer(b'')
 else:
  old=io.lines_fd;io.lines_fd=None
  with pytest.raises(lh.HardwareError):io.receive_switch(True)
  io.lines_fd=old
 c.commands.clear();r.start_receive(authority_deadline=c.time+10);assert [x[0] for x in c.commands]==LEGACY
@pytest.mark.parametrize('name',['_drained_handoff','_checked_rearm','_activity_reader','_rearm_transfer_epoch'])
def test_driver_sealed(rig,name):
 c,io,r=setup(rig)
 with pytest.raises(lh.HardwareError):setattr(r,name,None)
 with pytest.raises(lh.HardwareError):delattr(r,name)
@pytest.mark.parametrize('name',['transfer','reset','receive_switch','_set','_radio_activity','radio_activity_epoch'])
def test_transport_sealed(rig,name):
 c,io,r=setup(rig)
 with pytest.raises(lh.HardwareError):setattr(io,name,None)
 with pytest.raises(lh.HardwareError):delattr(io,name)
@pytest.mark.parametrize('change',['reset','spi','profile','fenced','pending'])
def test_ready_callback_mutation(rig,change):
 c,io=rig;state={'armed':False,'r':None,'calls':0}
 def clock():
  if state['armed']:
   state['calls']+=1
   if state['calls']==2:
    r=state['r'];state['armed']=False
    if change=='reset':io.reset(True)
    elif change=='spi':io.transfer(b'\xc0\0')
    elif change=='profile':r.profile=None
    elif change=='fenced':r.fenced=True
    else:r.receive_pending=True
  return c.time
 c,io,r=setup(rig,clock=clock);state['r']=r;drain(c,r);state['armed']=True;c.commands.clear()
 with pytest.raises(lh.HardwareError):r.start_receive(authority_deadline=c.time+10)
 assert r.fenced and c.resets[-1] is False;assert not any(x[0]==0x82 for x in c.commands)

def test_deadline_consumes(rig):
 c,io,r=setup(rig);drain(c,r)
 with pytest.raises(lh.HardwareError):r.start_receive(authority_deadline=c.time)
 assert r._drained_handoff is None
@pytest.mark.parametrize('value',[None,1,'yes'])
def test_strict_option(value):
 with pytest.raises(lh.HardwareError):SX1262(None,checked_rearm=value)

@pytest.mark.parametrize('change',['reset','spi'])
def test_reason_callback_cannot_mint_new_proof(rig,change):
 c,io,r=setup(rig)
 class Reasons:
  def record(self,name):
   if name=='physical_packet_returned':
    if change=='reset':io.reset(True)
    else:io.transfer(b'\x82\0\0\x40')
 r.receive_reasons=Reasons();drain(c,r);c.commands.clear();r.start_receive(authority_deadline=c.time+10)
 assert [x[0] for x in c.commands]==LEGACY

@pytest.mark.parametrize('change',['reset','spi','profile'])
def test_final_clear_clock_callback_cannot_mint_proof(rig,change):
 c,io=rig;state={'armed':False,'r':None,'fired':False}
 def clock():
  if state['armed'] and not state['fired'] and len(c.commands)>=3 and [x[0] for x in c.commands[-3:]]==[0x02,0xc0,0x12]:
   state['fired']=True
   if change=='reset':io.reset(True)
   elif change=='spi':io.transfer(b'\x82\0\0\x40')
   else:state['r'].profile=None
  return c.time
 c,io,r=setup(rig,clock=clock);state['r']=r;state['armed']=True;c.flags=2
 with pytest.raises(lh.HardwareError):r.poll_receive()
 assert state['fired'] and r.fenced and r._drained_handoff is None and c.resets[-1] is False

@pytest.mark.parametrize('fault',['all_ones','stale_irq','busy'])
def test_failed_checked_drain_never_publishes(rig,fault):
 c,io,r=setup(rig);c.flags=2;c.fault=fault
 with pytest.raises(lh.HardwareError):r.poll_receive()
 assert r.fenced and r._drained_handoff is None

def test_public_default_and_strict_worker_options():
 import argparse
 from examples import radio_file
 from meshticulum.bench.hat_worker import HATWorker
 parser=argparse.ArgumentParser();radio_file.add_host_timing_argument(parser)
 assert parser.parse_args([]).hat_checked_rearm is False
 assert parser.parse_args(['--hat-checked-rearm']).hat_checked_rearm is True
 for value in (None,1,'yes'):
  with pytest.raises(lh.HardwareError,match='invalid_hat_checked_rearm'):
   HATWorker(None,journal=None,profile=None,context_digest=None,authorize=None,authority_deadline=None,antenna_confirmed=None,checked_rearm=value)

@pytest.mark.parametrize('raw_action',[None,'reset','setrx','switch'])
def test_actual_worker_authorize_boundary(rig,tmp_path,raw_action):
 import time
 from meshticulum.bench.attempts import AttemptJournal
 from meshticulum.bench.hat_worker import HATWorker,journal_binding
 c,io=rig;io.monotonic=time.monotonic;io.boot_id=c.boot_id;context=b'c'*32;tmp_path.chmod(0o700)
 j=AttemptJournal(tmp_path/'attempts.sqlite',binding=journal_binding(context,c.boot_id,PROFILE),max_attempts=20,max_airtime_us=10000000,create=True)
 state={'worker':None,'fired':False};calls=[]
 def authorize():
  calls.append(1);w=state['worker']
  if raw_action and w is not None and w.radio._drained_handoff is not None and not state['fired']:
   state['fired']=True
   if raw_action=='reset':io.reset(True)
   elif raw_action=='setrx':io.transfer(b'\x82\0\0\x40')
   else:io.receive_switch(True)
  return True
 try:
  w=HATWorker(io,journal=j,profile=PROFILE,context_digest=context,authorize=authorize,authority_deadline=time.monotonic()+10,antenna_confirmed=True,checked_rearm=True)
  state['worker']=w;w.receive(timeout=0);c.flags=2;c.commands.clear();before=len(calls)
  result=w.receive(timeout=0)
  assert result is not None and result[1].packet==c.rx and w.radio.receiving
  assert len(calls)>before and j.snapshot()['attempts']==0 and not j.snapshot()['fenced']
  ops=[x[0] for x in c.commands]
  assert ops[-(11 if raw_action else 6):]==(LEGACY if raw_action else LEGACY[5:])
  assert state['fired']==bool(raw_action)
 finally:j.close()

@pytest.mark.parametrize('fault',['partial','status','setrx'])
def test_optimized_transfer_failure_contains_and_consumes(rig,monkeypatch,fault):
 c,io,r=setup(rig);drain(c,r);original=lh.fcntl.ioctl
 def fail(fd,op,arg):
  if op==lh.SPI_MESSAGE:
   packet=ctypes.string_at(arg.tx_buf,arg.len)
   if fault=='partial' and packet[0]==0x8c:return arg.len-1
   if fault=='status' and packet[0]==0xc0:
    ctypes.memmove(arg.rx_buf,b'\0\xff',2);return arg.len
   if fault=='setrx' and packet[0]==0x82:raise OSError('SetRx ambiguous')
  return original(fd,op,arg)
 monkeypatch.setattr(lh.fcntl,'ioctl',fail)
 with pytest.raises((OSError,lh.HardwareError)):r.start_receive(authority_deadline=c.time+10)
 assert r.fenced and r._drained_handoff is None and r._rearm_transfer_epoch is None and c.resets[-1] is False

def test_near_deadline_keeps_original_finite_timer(rig):
 c,io,r=setup(rig);drain(c,r);deadline=c.time+.025;c.commands.clear()
 r.start_receive(authority_deadline=deadline)
 command=next(x for x in c.commands if x[0]==0x82);ticks=int.from_bytes(command[1:],'big')
 assert 0<ticks<0xffffff
 assert ticks/64000 <= deadline-c.time-.01+.00002

@pytest.mark.parametrize('failure',['revoked','expired'])
def test_worker_fresh_guard_failure_never_uses_handoff(rig,tmp_path,failure):
 import time
 from meshticulum.bench.attempts import AttemptJournal
 from meshticulum.bench.hat_worker import HATWorker,journal_binding
 c,io=rig;io.monotonic=time.monotonic;io.boot_id=c.boot_id;context=b'd'*32;tmp_path.chmod(0o700)
 j=AttemptJournal(tmp_path/'attempts.sqlite',binding=journal_binding(context,c.boot_id,PROFILE),max_attempts=20,max_airtime_us=10000000,create=True)
 box={'w':None}
 def authorize():
  w=box['w']
  if w and w.radio._drained_handoff is not None:
   if failure=='revoked':return False
   w.deadline=0
  return True
 try:
  w=HATWorker(io,journal=j,profile=PROFILE,context_digest=context,authorize=authorize,authority_deadline=time.monotonic()+10,antenna_confirmed=True,checked_rearm=True)
  box['w']=w;w.receive(timeout=0);c.flags=2;c.commands.clear()
  with pytest.raises(lh.HardwareError):w.receive(timeout=0)
  assert w.radio.fenced and w.radio._drained_handoff is None and j.snapshot()['attempts']==0
  assert not any(x[0]==0x82 for x in c.commands)
 finally:j.close()
