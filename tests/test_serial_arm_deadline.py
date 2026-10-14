"""Observed short-lease timing and failure controls; synthetic serial, no RF."""
from dataclasses import replace
import struct

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.gfsk_profile import GfskProfile,RECORD
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder,Frame,Message
from meshticulum.bench.serial_worker import SerialWorker,GfskSerialWorker,journal_binding
from tests.test_meshticulum_bench_worker import BOOT,VERSION,Channel,PROFILE


class TimedChannel(Channel):
    def __init__(self,mode,*,start=100.0,disarm_delay=0.0,delivery_delay=0.0,reply_delay=0.0,read_overrun=False):
        super().__init__();self.time=start;self.mode=mode;self.disarm_delay=disarm_delay
        self.delivery_delay=delivery_delay;self.reply_delay=reply_delay;self.read_overrun=read_overrun
        self.pending_arm=None;self.on_disarm=lambda:None;self.mcu_lease_start=self.mcu_lease_ms=None

    def write(self,data,deadline):
        frame,=Decoder().feed(data)
        if frame.type is Message.DISARM:
            self.time+=self.disarm_delay;self.on_disarm()
        if frame.type not in (Message.ARM,Message.GFSK_ARM):return super().write(data,deadline)
        assert self.time<deadline
        self.writes.append(frame);started=self.time
        self.time+=self.delivery_delay;assert self.time<deadline
        self.mcu_lease_start=self.time
        self.mcu_lease_ms=RECORD.unpack(frame.body)[10] if self.mode=='gfsk' else struct.unpack('!IIBBbHBIIQ',frame.body)[7]
        kind=Message.GFSK_ARM_REPLY if self.mode=='gfsk' else Message.ARM_REPLY
        self.pending_arm=(Frame(kind,BOOT,frame.request,frame.body),started+self.reply_delay)

    def read(self,timeout):
        if self.pending_arm:
            frame,ready=self.pending_arm
            if self.read_overrun or self.time+timeout>=ready:
                self.time=max(self.time,ready);self.pending_arm=None;self.offer(frame)
            else:self.time+=timeout;return b''
        return super().read(timeout)


@pytest.fixture
def assemble(tmp_path):
    tmp_path.chmod(0o700);opened=[]
    def make(mode,lease=60000,**timing):
        profile=GfskProfile(921375000,0,lease,10,10000000) if mode=='gfsk' else replace(PROFILE,lease_ms=lease)
        channel=TimedChannel(mode,**timing);worker=(GfskSerialWorker if mode=='gfsk' else SerialWorker)(channel)
        identity=worker.discover(expected_board_id=1,expected_version=VERSION);context=b'd'*32
        journal=AttemptJournal(tmp_path/(str(len(opened))+'.sqlite'),
            binding=journal_binding(context,identity,profile),max_attempts=profile.max_boot_attempts,
            max_airtime_us=profile.max_boot_estimated_airtime_us,create=True,request_clock=lambda:100000000)
        opened.append(journal);return worker,channel,journal,profile,context
    yield make
    for journal in opened:journal.close()


def arm(parts,deadline,authorize=lambda:True):
    w,c,j,p,context=parts
    w.arm(journal=j,profile=p,context_digest=context,authorize=authorize,authority_deadline=deadline)
    return w,c,j,p,context


@pytest.mark.parametrize('mode',['lora','gfsk'])
def test_observed_73045ms_profile_survives_consumed_preparation_reserve(assemble,mode):
    parts=assemble(mode,73045,start=302085.817965,disarm_delay=.25,delivery_delay=.4,reply_delay=1.9)
    w,c,j,p,context=parts;deadline=302159.866307;body=p.encode(board_id=1);binding=j.binding;calls=[]
    delays=(.1,.2,.14536)
    def current():
        c.time+=delays[len(calls)];calls.append(True);return True
    arm(parts,deadline,current)
    started=c.mcu_lease_start-.4
    assert started+p.lease_ms/1000+.5>=deadline # The former whole-profile-fit check refuses.
    assert 70000<w.arm_lease_ms<73045 and c.mcu_lease_ms==w.arm_lease_ms
    assert w.profile is p and p.encode(board_id=1)==body and j.binding==binding
    assert j.max_attempts==p.max_boot_attempts and j.max_airtime_us==p.max_boot_estimated_airtime_us
    assert j.snapshot()['attempts']==j.snapshot()['reserved_estimated_airtime_us']==0
    assert w.deadline==deadline and w.arm_lease_end_upper_bound<deadline
    assert c.mcu_lease_start+w.arm_lease_ms/1000<deadline
    assert w.armed_until<=c.mcu_lease_start+w.arm_lease_ms/1000-.1
    assert len(calls)==3
    # Narrowing only the lease leaves every RF parameter and budget byte exact.
    actual=next(f.body for f in c.writes if f.type in (Message.ARM,Message.GFSK_ARM))
    assert actual==replace(p,lease_ms=w.arm_lease_ms).encode(board_id=1)


@pytest.mark.parametrize('mode',['lora','gfsk'])
def test_ample_authority_preserves_exact_original_ARM_bytes(assemble,mode):
    w,c,j,p,context=arm(assemble(mode),200)
    assert w.arm_lease_ms==p.lease_ms==60000
    assert c.writes[-1].body==p.encode(board_id=1) and w.armed_until==159.9


@pytest.mark.parametrize('remaining,accepted',[(3.1,True),(3.099999,False)])
@pytest.mark.parametrize('mode',['lora','gfsk'])
def test_minimum_lease_has_full_original_confirmation_room(assemble,mode,remaining,accepted):
    parts=assemble(mode,1000);w,c,j,p,_=parts
    if accepted:
        arm(parts,100+remaining);assert w.arm_lease_ms==1000 and w.arm_lease_end_upper_bound<100+remaining
    else:
        with pytest.raises(HardwareError,match='radio_authority_expired'):arm(parts,100+remaining)
        assert w.fenced and w.armed_until==0 and w.arm_lease_ms is None
        assert not any(f.type in (Message.ARM,Message.GFSK_ARM) for f in c.writes)
    assert j.snapshot()['attempts']==0


@pytest.mark.parametrize('when',['disarm','final_guard'])
@pytest.mark.parametrize('fault',['revoked','expired'])
def test_revocation_or_expiry_during_setup_never_submits_ARM(assemble,when,fault):
    parts=assemble('gfsk',73045);w,c,j,p,_=parts;live=[True];calls=[0]
    deadline=100.25 if when=='disarm' and fault=='expired' else 175
    def change():
        if fault=='revoked':live[0]=False
        else:c.time=deadline
    if when=='disarm':c.on_disarm=change
    def current():
        calls[0]+=1
        if when=='final_guard' and calls[0]==3:change()
        return live[0]
    with pytest.raises(HardwareError):arm(parts,deadline,current)
    assert w.fenced and w.arm_lease_ms is None and w.armed_until==0
    assert not any(f.type is Message.GFSK_ARM for f in c.writes) and j.snapshot()['attempts']==0


@pytest.mark.parametrize('delay,accepted',[(1.999,True),(2.0,False),(2.001,False)])
def test_late_ARM_reply_cannot_extend_two_second_confirmation_bound(assemble,delay,accepted):
    parts=assemble('gfsk',73045,delivery_delay=.5,reply_delay=delay,read_overrun=not accepted)
    w,c,j,p,_=parts;deadline=174
    if accepted:
        arm(parts,deadline)
        assert w.arm_lease_end_upper_bound<deadline
        assert w.armed_until<c.mcu_lease_start+w.arm_lease_ms/1000
    else:
        with pytest.raises(HardwareError,match='serial_reply_deadline'):arm(parts,deadline)
        assert w.fenced and w.armed_until==0 and w.arm_lease_ms is None
        with pytest.raises(HardwareError):w.receive()
    assert j.snapshot()['attempts']==0


@pytest.mark.parametrize('mutation',['profile','deadline','journal_binding','authorize'])
def test_callback_cannot_change_admitted_profile_or_original_deadline(assemble,mutation):
    parts=assemble('gfsk',73045);w,c,j,p,_=parts;calls=[0]
    def current():
        calls[0]+=1
        if calls[0]==3:
            if mutation=='profile':object.__setattr__(p,'lease_ms',90000)
            elif mutation=='deadline':w.deadline=1000
            elif mutation=='journal_binding':j.binding=b'z'*32
            else:w.authorize=lambda:True
        return True
    with pytest.raises(HardwareError,match='radio_arm_binding_changed'):arm(parts,174,current)
    assert not any(f.type is Message.GFSK_ARM for f in c.writes)
    assert w.fenced and w.arm_lease_ms is None


def test_receive_cannot_use_longer_admitted_profile_after_short_actual_lease(assemble):
    w,c,j,p,_=arm(assemble('gfsk',73045),174)
    c.time=w.armed_until
    assert c.time<w.deadline
    with pytest.raises(HardwareError,match='radio_lease_expired'):w.receive()
    with pytest.raises(HardwareError,match='radio_lease_too_short'):w.transmit(b'payload')
    assert j.snapshot()['attempts']==0


def test_receive_read_crossing_actual_lease_never_returns_ingested_packet(assemble):
    w,c,j,p,_=arm(assemble('gfsk',73045),174)
    c.time=w.armed_until-.01
    def crossing_read(timeout):
        c.time+=.02
        return Frame(Message.GFSK_RECEIVE,BOOT,0,struct.pack('!h',-925)+b'ciphertext').encode()
    c.read=crossing_read
    with pytest.raises(HardwareError,match='radio_lease_expired'):w.receive(.05)
    assert len(w._received)==1 and j.snapshot()['attempts']==0
