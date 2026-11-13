"""Non-consuming radio wake hints; no device actions in these tests."""
import struct
import pytest
from meshticulum.bench import linux_hat as h

EVENT=struct.pack('=QIIII6I',1,1,5,1,1,*([0]*6))

def rig(monkeypatch,*,queued=EVENT):
 io=h.LinuxHAT.__new__(h.LinuxHAT);io.lines_fd=42
 state={'high':True,'queue':queued,'read_sizes':[],'waits':[],'radio_irqs':2}
 io.dio1_ready=lambda:state['high']
 def read(fd,size):
  assert fd==42;state['read_sizes'].append(size)
  if not state['queue']:raise BlockingIOError()
  data,state['queue']=state['queue'][:size],state['queue'][size:];return data
 def select(r,w,x,t):
  assert r==[42] and 0<=t<=.005;state['waits'].append(t)
  return ([42]if state['queue']else [],[],[])
 monkeypatch.setattr(h.os,'read',read);monkeypatch.setattr(h.select,'select',select)
 return io,state

def test_high_then_ordinary_radio_drain_then_low_has_no_stale_wake(monkeypatch):
 io,s=rig(monkeypatch)
 assert io.wait_dio1(.005) is True
 assert s['radio_irqs']==2 and s['queue']==b'' and s['waits']==[]
 # Model the distinct, unchanged ordinary radio poll clearing its own IRQ.
 s['high']=False;s['radio_irqs']=0
 assert io.wait_dio1(.005) is False
 assert len(s['waits'])==1 and s['read_sizes']==[768]

@pytest.mark.parametrize('kind',['empty','arrival_during_read','fd_changed','closed','malformed','wrong_offset','oversized_queue'])
def test_high_drain_races_and_failures(monkeypatch,kind):
 io,s=rig(monkeypatch,queued=EVENT*20 if kind=='oversized_queue'else EVENT)
 if kind=='empty':s['queue']=b''
 if kind=='malformed':s['queue']=b'x'
 if kind=='wrong_offset':s['queue']=struct.pack('=QIIII6I',1,1,4,1,1,*([0]*6))
 original=h.os.read
 def read(fd,n):
  if kind=='closed':raise OSError('closed fd')
  if kind=='fd_changed':io.lines_fd=None;raise BlockingIOError()
  result=original(fd,n)
  if kind=='arrival_during_read':s['queue']+=EVENT
  return result
 monkeypatch.setattr(h.os,'read',read)
 if kind in ('fd_changed','closed','malformed','wrong_offset'):
  with pytest.raises((h.HardwareError,OSError)):io.wait_dio1(.005)
 else:
  assert io.wait_dio1(.005) is True # Even EAGAIN retains the observed high hint.
  if kind=='oversized_queue':assert len(s['queue'])==4*48
  if kind=='arrival_during_read':assert s['queue']==EVENT
 assert len(s['read_sizes'])<=1 and s['waits']==[] and s['radio_irqs']==2

def test_edge_arriving_between_low_level_and_select_still_wakes(monkeypatch):
 io,s=rig(monkeypatch,queued=b'')
 def level():s['high']=False;s['queue']=EVENT;return False
 io.dio1_ready=level
 assert io.wait_dio1(.005) is True
 assert len(s['waits'])==1 and s['read_sizes']==[768]
