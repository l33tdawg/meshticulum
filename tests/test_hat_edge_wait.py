import struct
import pytest
from meshticulum.bench.linux_hat import LinuxHAT,HardwareError,gpio_request
from tests.test_hat_activity_wait import rig

def test_only_owned_dio_rising_edge():
    r=gpio_request();a=r.config.attrs[2]
    assert (a.id,a.value,a.mask)==(1,20,8)
    assert r.event_buffer_size==16 and r.config.num_attrs==3

@pytest.mark.parametrize('kind',['low','high','event','stale','malformed','wrong_line'])
def test_bounded_transport_hint(monkeypatch,kind):
    io=LinuxHAT.__new__(LinuxHAT);io.lines_fd=42;calls=[]
    io.dio1_ready=lambda:kind=='high'
    def select(r,w,x,t):
        calls.append(('select',t));assert r==[42] and 0<=t<=.005
        return ([42] if kind not in ['low','high'] else [],[],[])
    def read(fd,size):
        calls.append(('read',size));assert fd==42 and size==768
        if kind=='stale':raise BlockingIOError()
        if kind=='malformed':return b'x'
        return struct.pack('=QIIII6I',123,1,4 if kind=='wrong_line' else 5,1,1,*([0]*6))
    monkeypatch.setattr('meshticulum.bench.linux_hat.select.select',select)
    monkeypatch.setattr('meshticulum.bench.linux_hat.os.read',read)
    if kind in ['malformed','wrong_line']:
        with pytest.raises(HardwareError):io.wait_dio1(.005)
    else:assert io.wait_dio1(.005)==(kind in ['high','event'])
    if kind=='high':assert calls==[('read',768)]

@pytest.mark.parametrize('kind',['normal','revoked','expired','owner','clock','invalid','failure'])
def test_event_wait_cannot_bypass_fresh_guard(rig,kind):
    chip,w,j,state=rig;before=list(chip.commands);calls=[];original=w._clock
    def wait(seconds):
        calls.append(seconds);assert 0<=seconds<=.005
        chip.time+=seconds
        if kind=='revoked':state['allowed']=False
        if kind=='expired':chip.time=w.lease_deadline
        if kind=='owner':w.io=object()
        if kind=='clock':w._clock=lambda:0
        if kind=='failure':raise OSError('event failure')
        return 1 if kind=='invalid' else False
    chip.wait_dio1=wait
    try:
        if kind=='normal':assert w.wait_for_activity(.005) is False
        else:
            with pytest.raises((HardwareError,OSError)):w.wait_for_activity(.005)
        assert len(calls)==1 and chip.commands==before and j.snapshot()['attempts']==0
    finally:w.io=chip;w._clock=original


def test_changed_clock_never_called(rig):
    chip,w,_,_=rig;original=w._clock;called=[]
    w._clock=lambda:called.append(True) or 0
    try:
        with pytest.raises(HardwareError,match='hat_owner_or_clock_changed'):
            w.wait_for_activity(.005)
        assert called==[]
    finally:w._clock=original


def test_high_level_cannot_replace_gpio_request():
    io=LinuxHAT.__new__(LinuxHAT);io.lines_fd=42
    def high():io.lines_fd=43;return True
    io.dio1_ready=high
    with pytest.raises(HardwareError,match='gpio_request_changed'):
        io.wait_dio1(.005)
