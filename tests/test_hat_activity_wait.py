"""Owned DIO1 wake controls; modeled GPIO does not establish RF goodput."""
import struct
import pytest
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.hat_worker import HATWorker,journal_binding
from meshticulum.bench.linux_hat import LinuxHAT,GPIO_GET_VALUES,HardwareError
from tests.test_meshticulum_bench_sx1262 import Chip,PROFILE

@pytest.fixture
def rig(tmp_path):
    chip=Chip();state={'allowed':True};context=b'w'*32
    journal=AttemptJournal(tmp_path/'a.db',binding=journal_binding(context,chip.boot_id,PROFILE),max_attempts=20,max_airtime_us=10000000,create=True)
    worker=HATWorker(chip,journal=journal,profile=PROFILE,context_digest=context,authorize=lambda:state['allowed'],authority_deadline=200,antenna_confirmed=True,monotonic=lambda:chip.time,time_basis='model-monotonic')
    worker.receive(timeout=0)
    try:yield chip,worker,journal,state
    finally:worker.disarm();journal.close()

def test_existing_line_three_only_no_abi_change(monkeypatch):
    io=LinuxHAT.__new__(LinuxHAT);io.lines_fd=123;calls=[]
    def ioctl(fd,command,value):
        calls.append((fd,command,struct.unpack('=QQ',value)));value[:]=struct.pack('=QQ',8,8)
    monkeypatch.setattr('meshticulum.bench.linux_hat.fcntl.ioctl',ioctl)
    assert io.dio1_ready() is True and calls==[(123,GPIO_GET_VALUES,(0,8))]
    io.lines_fd=None
    with pytest.raises(HardwareError,match='closed_gpio'):io.dio1_ready()

def test_high_level_returns_without_sleep_spi_or_debt(rig):
    chip,w,j,_=rig;before=list(chip.commands);paid=j.snapshot();now=chip.time
    chip.dio1_ready=lambda:True
    assert w.wait_for_activity(.005) is True
    assert chip.time==now and chip.commands==before and j.snapshot()==paid

def test_low_level_wait_bounded_with_original_lease(rig):
    chip,w,j,_=rig;before=list(chip.commands);original=w.lease_deadline;now=chip.time;sleeps=[]
    chip.dio1_ready=lambda:False
    def sleep(seconds):sleeps.append(seconds);chip.time+=seconds
    chip.sleep=sleep
    assert w.wait_for_activity(.005) is False
    assert 0<chip.time-now<=.005+1e-10 and all(0<x<=.00025 for x in sleeps)
    assert w.lease_deadline==original and chip.commands==before and j.snapshot()['attempts']==0

def test_unsupported_transport_preserves_none_fallback(rig):
    chip,w,_,_=rig;now=chip.time
    assert w.wait_for_activity(.005) is None and chip.time==now

@pytest.mark.parametrize('kind',['expired','revoked','owner_changed','read_failure','invalid_result'])
def test_no_wake_hint_bypasses_fresh_authority_or_read_failure(rig,kind):
    chip,w,j,state=rig;before=list(chip.commands)
    def read():
        if kind=='expired':chip.time=w.lease_deadline
        if kind=='revoked':state['allowed']=False
        if kind=='owner_changed':w.io=Chip()
        if kind=='read_failure':raise OSError('gpio read failed')
        return 1 if kind=='invalid_result' else True
    chip.dio1_ready=read
    with pytest.raises((HardwareError,OSError)):w.wait_for_activity(.005)
    assert chip.commands==before and j.snapshot()['attempts']==0
    w.io=chip

def test_sleep_cannot_cross_original_lease_silently(rig):
    chip,w,j,_=rig;chip.dio1_ready=lambda:False;w.lease_deadline=chip.time+.0003;end=w.lease_deadline
    with pytest.raises(HardwareError):w.wait_for_activity(.005)
    assert w.lease_deadline==end and j.snapshot()['attempts']==0

@pytest.mark.parametrize('timeout',[-.1,.006,float('nan'),True])
def test_invalid_wait_bounds_refused(rig,timeout):
    _,w,_,_=rig
    with pytest.raises(HardwareError,match='invalid_hat_activity_timeout'):w.wait_for_activity(timeout)

def test_gpio_level_is_only_hint_and_not_consumed(rig):
    chip,w,_,_=rig;reads=[];chip.dio1_ready=lambda:reads.append(True) or True;chip.flags=2
    assert w.wait_for_activity(.005) is True and chip.flags==2
    assert w.receive(timeout=0)[1].packet==chip.rx and len(reads)==1
