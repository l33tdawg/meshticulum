"""Delayed NSS/BUSY controls against the real driver; no physical RF evidence."""
import math

import pytest

from meshticulum.bench.gfsk_sx1262 import GfskSX1262
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.sx1262 import SX1262
from tests.test_gfsk_workers import GfskChip, P
from tests.test_meshticulum_bench_sx1262 import Chip, PROFILE


@pytest.fixture(params=['lora','gfsk125'])
def configured(request):
    chip,radio,profile=(Chip(),SX1262,PROFILE) if request.param=='lora' else (GfskChip(),GfskSX1262,P)
    device=radio(chip);device.configure(profile)
    return chip,device


@pytest.mark.parametrize('completion_us',[1.5,350])
def test_delayed_busy_assertion_never_queries_status_before_completion(configured,completion_us):
    chip,radio=configured;original=chip.transfer;pending=None;statuses=[]
    def busy():
        return pending is not None and pending[0]<=chip.time<pending[1]
    def transfer(packet):
        nonlocal pending
        assert not busy(), 'SPI began while BUSY was asserted'
        if packet[0]==0x8C:
            pending=(chip.time+0.0000006,chip.time+completion_us/1000000)
        if packet[0]==0xC0 and pending is not None:
            assert chip.time>=pending[1], 'completed status read too early'
            statuses.append(chip.time)
        return original(packet)
    chip.busy,chip.transfer=busy,transfer
    started=chip.time;radio._packet(255)
    assert len(statuses)==1 and statuses[0]>=started+completion_us/1000000
    assert chip.time>=started+0.000002 and chip.time-started<0.001
    assert not chip.tx_calls and not radio.fenced


def test_stuck_busy_after_write_is_bounded_and_never_starts_tx(configured):
    chip,radio=configured;original=chip.transfer;stuck=False
    def transfer(packet):
        nonlocal stuck
        reply=original(packet)
        if packet[0]==0x8C:stuck=True
        return reply
    chip.transfer,chip.busy=transfer,lambda:stuck
    started=chip.time
    with pytest.raises(HardwareError,match='sx1262_busy_timeout'):
        radio.start_transmit(b'packet',authority_deadline=160,antenna_confirmed=True)
    assert 0.150<=chip.time-started<0.151
    assert radio.fenced and chip.resets[-1] is False and not chip.tx_calls
    assert radio.attempts==1 and radio.reserved_us>0


def test_busy_processing_cannot_renew_the_original_tx_deadline(configured):
    chip,radio=configured;original=chip.transfer;pending=None
    def transfer(packet):
        nonlocal pending
        reply=original(packet)
        if packet[0]==0x8C:pending=(chip.time+0.0000006,chip.time+0.006)
        return reply
    chip.transfer=transfer
    chip.busy=lambda:pending is not None and pending[0]<=chip.time<pending[1]
    packet=b'packet';estimate=radio._airtime(radio.profile,len(packet))
    timeout_us=math.ceil(estimate*1.10)+100000
    deadline=chip.time+timeout_us/1000000+0.053
    with pytest.raises(HardwareError,match='sx1262_authority_deadline'):
        radio.start_transmit(packet,authority_deadline=deadline,antenna_confirmed=True)
    assert not chip.tx_calls and radio.fenced and chip.resets[-1] is False
    assert radio.attempts==1 and radio.reserved_us==estimate


def test_receive_completing_during_short_standby_settle_remains_pending(configured):
    chip,radio=configured;radio.start_receive(authority_deadline=160)
    transfer,sleep=chip.transfer,chip.sleep;standby_written=False;finished=False
    def write(packet):
        nonlocal standby_written
        result=transfer(packet)
        if packet[0]==0x80:standby_written=True
        return result
    def settle(seconds):
        nonlocal finished
        if standby_written and not finished:
            assert seconds>=0.0000006
            chip.flags=2;finished=True
        sleep(seconds)
    chip.transfer,chip.sleep=write,settle
    offset=len(chip.commands)
    assert radio.start_transmit(b'outbound',authority_deadline=160,antenna_confirmed=True) is False
    assert radio.receive_pending and not chip.tx_calls and radio.attempts==1
    assert not any(packet[0] in (0x02,0x0E,0x83) for packet in chip.commands[offset:])
    assert radio.poll_receive().packet==chip.rx and not radio.receive_pending


def test_nonadvancing_model_clock_does_not_create_a_settle_spin(configured):
    chip,radio=configured;sleeps=[];started=chip.time
    # Native LinuxHAT.sleep blocks in time.sleep. The existing explicit model
    # transport may record a sleep without advancing time; no new loop is added.
    chip.sleep=sleeps.append
    radio._packet(255)
    assert sleeps==[0.000002] and chip.time==started and not chip.tx_calls
