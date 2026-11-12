"""Empty unauthenticated PHY input is not a bus failure or accepted frame."""
import pytest
from meshticulum.bench.linux_hat import HardwareError
from tests.test_meshticulum_bench_sx1262 import configured
from tests.test_hat_worker_immediate_receive import rig


def test_empty_then_valid_receive_preserves_normal_irq_cleanup():
    chip,radio=configured();radio.start_receive(authority_deadline=160)
    chip.rx=b'';chip.flags=2;start=len(chip.commands)
    assert radio.poll_receive() is None
    assert radio.rx_errors==1 and not radio.fenced and not radio.receive_pending
    assert not radio.receiving and chip.flags==0 and chip.mode==2
    assert not any(c[0] in (0x1E,0x14) for c in chip.commands[start:])
    assert chip.reg[0x0902]==0 and chip.reg[0x0944]&2
    radio.start_receive(authority_deadline=160)
    chip.rx=b'valid nonempty PHY';chip.flags=2
    assert radio.poll_receive().packet==chip.rx
    assert radio.rx_errors==1 and radio.attempts==chip.tx_calls==0


@pytest.mark.parametrize('fault',['bad_status','short_reply','busy','stale_irq','readback','io'])
def test_empty_payload_does_not_relax_negative_hardware_guards(fault):
    chip,radio=configured();radio.start_receive(authority_deadline=160)
    chip.rx=b'';chip.flags=2;original=chip.transfer
    def transfer(command):
        result=original(command)
        if command[0]==0x13:
            if fault=='bad_status':return bytes([0,0xA8,0,0])
            if fault=='short_reply':return result[:-1]
            if fault=='io':raise OSError('injected receive SPI fault')
        if command[0]==0x1D and fault=='readback' and command[1:3]==b'\x09\x44':
            return result[:-1]+b'\0'
        return result
    chip.transfer=transfer
    if fault in ('busy','stale_irq'):chip.fault=fault
    with pytest.raises((HardwareError,OSError)):
        radio.poll_receive()
    assert radio.fenced and chip.resets[-1] is False
    assert chip.tx_calls==radio.attempts==0


def test_worker_rearms_empty_drop_inside_original_lease_then_receives(rig):
    chip,journal,worker=rig
    assert worker.receive(timeout=0) is None
    deadline=(worker.deadline,worker.lease_deadline)
    debt=journal.snapshot()
    chip.rx=b'';chip.flags=2
    assert worker.receive(timeout=0) is None
    assert worker.radio.rx_errors==1 and worker.radio.receiving and not worker.fenced
    assert deadline==(worker.deadline,worker.lease_deadline)
    assert journal.snapshot()==debt
    chip.rx=b'valid next PHY';chip.flags=2
    _,received=worker.receive(timeout=0)
    assert received.packet==chip.rx and worker.radio.receiving
    assert chip.tx_calls==0 and journal.snapshot()==debt


@pytest.mark.parametrize('fast',[False,True])
def test_inherited_gfsk_empty_is_rejected_without_delivery(fast):
    from tests.test_gfsk_workers import GfskChip, P
    from meshticulum.bench.gfsk_sx1262 import GfskSX1262
    from meshticulum.bench.gfsk250_sx1262 import Gfsk250SX1262
    from meshticulum.bench.gfsk250_profile import Gfsk250Profile
    chip=GfskChip()
    radio=(Gfsk250SX1262 if fast else GfskSX1262)(chip)
    profile=Gfsk250Profile(921375000,0,60000,10,10000000) if fast else P
    radio.configure(profile);radio.start_receive(authority_deadline=160)
    chip.rx=b'';chip.flags=2;start=len(chip.commands)
    assert radio.poll_receive() is None
    assert radio.rx_errors==1 and not radio.fenced and chip.flags==0
    assert not any(c[0] in (0x1E,0x14) for c in chip.commands[start:])
    radio.start_receive(authority_deadline=160);chip.rx=b'nonempty';chip.flags=2
    assert radio.poll_receive().packet==chip.rx and chip.tx_calls==0
