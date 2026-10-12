# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Fault-injected SPI chip model. These controls are not RF evidence."""
from dataclasses import replace
import struct

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.hat_worker import HATWorker, journal_binding
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Outcome, RadioProfile
from meshticulum.bench.serial_worker import estimated_airtime_us
from meshticulum.bench.sx1262 import SX1262

PROFILE = RadioProfile(917375000, 125000, 8, 5, 0, 8, 0x42, 60000, 20, 10000000)


class Chip:
    boot_id = '21f9c178-6090-40d9-9359-ecf1c7b950b1'

    def __init__(self):
        self.time, self.mode = 100.0, 2
        self.reg = {0x0740: 0x14, 0x0741: 0x24, 0x08D8: 0xC8}
        self.commands, self.switches, self.resets = [], [], []
        self.packet_type = 0
        self.flags = 0
        self.fault = None
        self.tx = False
        self.tx_at = 0
        self.fifo = b''
        self.rx = bytes(range(255))
        self.tx_calls = 0

    def monotonic(self): return self.time
    def sleep(self, seconds): self.time += seconds
    def busy(self): return self.fault == 'busy'
    def reset(self, high): self.resets.append(high)
    def receive_switch(self, receive): self.switches.append(receive)

    def transfer(self, value):
        self.commands.append(value)
        result = bytearray(len(value))
        if self.fault == 'all_ones': return bytes([255]) * len(value)
        opcode, body = value[0], value[1:]
        if opcode == 0xC0:
            result[1] = self.mode << 4 | 2
        elif opcode == 0x80:
            if not (self.tx and self.fault == 'finish_failure'):
                self.mode = 2 + body[0]
                self.tx = False
        elif opcode == 0x0D:
            address = int.from_bytes(body[:2], 'big')
            for index, byte in enumerate(body[2:]): self.reg[address+index] = byte
        elif opcode == 0x1D:
            address = int.from_bytes(body[:2], 'big')
            result[3] = self.mode << 4 | 2
            result[4:] = bytes(self.reg.get(address+i, 0) for i in range(len(value)-4))
        elif opcode == 0x8A: self.packet_type = body[0]
        elif opcode == 0x11:
            result[1] = self.mode << 4 | 2
            result[2] = self.packet_type
        elif opcode == 0x17:
            result[1] = self.mode << 4 | 2
        elif opcode == 0x02:
            self.flags = 1 if self.fault == 'stale_irq' else 0
        elif opcode == 0x12:
            if self.tx and self.fault == 'irq_read_error': raise OSError('SPI fault')
            if self.tx and self.time-self.tx_at > 0.005:
                self.flags = {'silent': 0, 'timeout': 0x200, 'both': 0x201,
                              'unexpected_irq': 3}.get(self.fault, 1)
            result[1] = self.mode << 4 | 2
            result[2:4] = self.flags.to_bytes(2, 'big')
        elif opcode == 0x0E:
            assert body[0] == 0
            self.fifo = body[1:]
        elif opcode == 0x83:
            self.tx_calls += 1
            self.tx, self.tx_at, self.mode = True, self.time, 6
            assert 1 <= int.from_bytes(body, 'big') < 0xFFFFFF
            if self.fault == 'start_failure': raise OSError('partial TX SPI command')
        elif opcode == 0x82:
            self.tx, self.mode = False, 5
            assert 1 <= int.from_bytes(body, 'big') < 0xFFFFFF
        elif opcode == 0x13:
            result[1] = self.mode << 4 | 2
            result[2:] = bytes([len(self.rx), 0])
        elif opcode == 0x1E:
            result[2] = self.mode << 4 | 2
            result[3:] = self.rx[:len(value)-3]
        elif opcode == 0x14:
            result[1] = self.mode << 4 | 2
            result[2:] = bytes([142, 248, 136])
        else:
            assert opcode in (0x96, 0x07, 0x89, 0x98, 0x86, 0x9D, 0x95,
                              0x8E, 0x8B, 0x8C, 0x8F, 0x93, 0x08), hex(opcode)
        # Captured C replies set reserved bit 7 in ordinary read status,
        # but not GetStatus. A zero-only RFU assumption must regress here.
        status_index = {0x1D: 3, 0x11: 1, 0x17: 1, 0x12: 1,
                        0x13: 1, 0x1E: 2, 0x14: 1}.get(opcode)
        if status_index is not None:
            result[status_index] |= 0x80
        return bytes(result)


def configured():
    chip = Chip()
    radio = SX1262(chip)
    radio.configure(PROFILE)
    return chip, radio


@pytest.mark.parametrize('value', [0x22, 0xA2, 0x23, 0xA3, 0xB2])
def test_status_ignores_reserved_bits_from_real_chip(value):
    SX1262._valid_status(value)


@pytest.mark.parametrize('value', [0x00, 0xFF, 0xA6, 0xA8, 0xAA, 0x86, 0xF2])
def test_bad_modes_and_command_errors_still_rejected(value):
    with pytest.raises(HardwareError, match='sx1262_bad_status'):
        SX1262._valid_status(value)


@pytest.mark.parametrize('packet', [
    b'\xc0\0', b'\x1d\x07\x40\0\0', b'\x1e\0\0\0',
    b'\x11\0\0', b'\x12\0\0\0', b'\x13\0\0\0',
    b'\x14\0\0\0\0', b'\x17\0\0\0'])
def test_documented_reads_have_no_fixed_sleep_with_busy_low(packet):
    chip, radio = configured()
    started = chip.time
    checks, sleeps = [], []
    original_busy, original_sleep = chip.busy, chip.sleep
    def busy():
        checks.append(chip.time)
        return original_busy()
    def sleep(seconds):
        sleeps.append(seconds)
        original_sleep(seconds)
    chip.busy, chip.sleep = busy, sleep
    assert len(radio._transfer(packet)) == len(packet)
    assert checks == [started, started] and not sleeps
    assert chip.time == started


def test_internal_irq_busy_after_read_is_waited_before_the_next_spi_command():
    chip, radio = configured()
    original = chip.transfer
    busy_until = chip.time
    def busy():
        return chip.time < busy_until
    def transfer(packet):
        nonlocal busy_until
        assert not busy(), 'SPI command issued while internal IRQ holds BUSY'
        reply = original(packet)
        if packet[0] == 0x12:
            busy_until = chip.time + 0.00035
        return reply
    chip.busy, chip.transfer = busy, transfer
    started = chip.time
    assert radio.irq() == 0
    assert chip.time - started >= 0.00035
    radio.status()
    assert chip.commands[-2:] == [b'\x12\0\0\0', b'\xc0\0']


def test_write_waits_for_delayed_busy_then_rejects_completed_command_error():
    chip, radio = configured()
    original = chip.transfer
    pending = None
    def busy():
        return pending is not None and pending[0] <= chip.time < pending[1]
    def transfer(packet):
        nonlocal pending
        if packet[0] == 0x8C:
            pending = (chip.time + 0.0000006, chip.time + 0.00035)
            original(packet)
            # The write MISO reports a previous successful command. It must
            # never replace the status of this completed command.
            return bytes([0x22]) * len(packet)
        if packet[0] == 0xC0 and pending is not None:
            assert chip.time >= pending[1], 'GetStatus queried before write completion'
            original(packet)
            return b'\0\xaa'
        return original(packet)
    chip.busy, chip.transfer = busy, transfer
    with pytest.raises(HardwareError, match='sx1262_bad_status_0xaa'):
        radio.start_transmit(b'packet', authority_deadline=160, antenna_confirmed=True)
    assert not chip.tx_calls and radio.fenced and chip.resets[-1] is False
    assert radio.attempts == 1 and radio.reserved_us == estimated_airtime_us(PROFILE, 6)


def test_standby_uses_one_fresh_completed_status_and_checks_its_mode():
    chip, radio = configured()
    offset = len(chip.commands)
    radio.standby()
    assert chip.commands[offset:] == [b'\x80\0', b'\xc0\0']
    original = chip.transfer
    def transfer(packet):
        reply = original(packet)
        # A valid command status alone is insufficient: standby must confirm
        # the RC mode from the completed command's GetStatus response.
        return b'\0\x62' if packet[0] == 0xC0 else reply
    chip.transfer = transfer
    with pytest.raises(HardwareError, match='sx1262_standby_failed'):
        radio.start_transmit(b'packet', authority_deadline=160, antenna_confirmed=True)
    assert not chip.tx_calls and radio.fenced and chip.resets[-1] is False


def test_unknown_opcode_retains_write_settle_delay():
    chip, radio = configured()
    chip.transfer = lambda packet: bytes(len(packet))
    started = chip.time
    radio._transfer(b'\xfe\0')
    assert chip.time - started == pytest.approx(0.0002)


def test_busy_timeout_after_tx_irq_keeps_attempt_unknown_and_fenced():
    chip, radio = configured()
    original = chip.transfer
    def transfer(packet):
        reply = original(packet)
        if packet[0] == 0x12 and chip.tx:
            chip.fault = 'busy'
        return reply
    chip.transfer = transfer
    radio.start_transmit(b'packet', authority_deadline=160, antenna_confirmed=True)
    result = radio.poll_transmit()
    assert result.outcome is Outcome.UNKNOWN and radio.fenced
    assert chip.tx_calls == radio.attempts == 1 and chip.resets[-1] is False


def test_time_spent_in_spi_does_not_extend_the_original_tx_deadline():
    chip, radio = configured()
    original = chip.transfer
    def transfer(packet):
        reply = original(packet)
        if packet[0] == 0x0E:
            chip.time += 0.20
        return reply
    chip.transfer = transfer
    data = b'packet'
    estimate = estimated_airtime_us(PROFILE, len(data))
    deadline = chip.time + (estimate * 1.10 + 100000) / 1000000 + 0.055
    with pytest.raises(HardwareError, match='sx1262_authority_deadline'):
        radio.start_transmit(data, authority_deadline=deadline, antenna_confirmed=True)
    assert not chip.tx_calls and radio.fenced and chip.resets[-1] is False
    assert radio.attempts == 1 and radio.reserved_us == estimate


def finish(chip, radio):
    for _ in range(5000):
        result = radio.poll_transmit()
        if result is not None: return result
        chip.sleep(0.001)
    raise AssertionError('unbounded TX')


def test_crystal_configuration_does_not_transmit_and_applies_semtech_errata():
    chip, radio = configured()
    assert not chip.tx_calls and chip.mode == 2
    assert all(command[0] != 0x97 for command in chip.commands)  # no TCXO
    assert chip.reg[0x0736] & 4  # standard IQ; manufacturer Python sample reverses it
    assert chip.reg[0x08D8] & 0x1E == 0x1E
    assert chip.reg[0x0889] & 4
    assert bytes([chip.reg[0x0740], chip.reg[0x0741]]) == b'\x44\x24'
    assert b'\x98\xe1\xe9' in chip.commands


def test_full_255_byte_transmit_has_finite_timer_and_checked_completion():
    chip, radio = configured()
    data = bytes(range(255))
    radio.start_transmit(data, authority_deadline=160, antenna_confirmed=True)
    result = finish(chip, radio)
    assert chip.fifo == data and chip.tx_calls == 1
    assert result.outcome is Outcome.RF_DONE and result.irq_flags == 1
    assert result.estimated_airtime_us == estimated_airtime_us(PROFILE, 255)
    assert chip.mode == 2 and chip.switches[-1] is True
    assert False in chip.switches


@pytest.mark.parametrize('fault', ['timeout', 'both', 'silent', 'unexpected_irq',
                                  'irq_read_error', 'finish_failure'])
def test_timeout_and_invalid_driver_evidence_fence_without_success(fault):
    chip, radio = configured()
    chip.fault = fault
    radio.start_transmit(b'packet', authority_deadline=160, antenna_confirmed=True)
    result = finish(chip, radio)
    assert result.outcome is Outcome.UNKNOWN and radio.fenced
    assert chip.resets[-1] is False and chip.switches[-1] is True
    with pytest.raises(HardwareError, match='sx1262_not_ready'):
        radio.start_transmit(b'again', authority_deadline=160, antenna_confirmed=True)
    assert chip.tx_calls == 1


@pytest.mark.parametrize('fault', ['stale_irq', 'start_failure', 'busy'])
def test_failed_start_holds_reset_and_never_retries(fault):
    chip, radio = configured()
    chip.fault = fault
    with pytest.raises((HardwareError, OSError)):
        radio.start_transmit(b'packet', authority_deadline=160, antenna_confirmed=True)
    assert radio.fenced and chip.resets[-1] is False
    assert chip.tx_calls == (1 if fault == 'start_failure' else 0)


def test_antenna_and_deadline_refusals_happen_before_radio_io():
    chip, radio = configured()
    count = len(chip.commands)
    with pytest.raises(HardwareError, match='antenna_not_confirmed'):
        radio.start_transmit(b'packet', authority_deadline=160, antenna_confirmed=False)
    with pytest.raises(HardwareError, match='authority_deadline'):
        radio.start_transmit(b'packet', authority_deadline=chip.time, antenna_confirmed=True)
    assert len(chip.commands) == count and radio.attempts == 0


def test_timed_rx_can_hold_maximum_packet_and_parses_signed_signal_values():
    chip, radio = configured()
    radio.start_receive(authority_deadline=160)
    command = next(c for c in reversed(chip.commands) if c[0] == 0x82)
    assert int.from_bytes(command[1:], 'big') * 15.625 > estimated_airtime_us(PROFILE, 255)
    chip.flags = 2
    packet = radio.poll_receive()
    assert packet.packet == bytes(range(255)) and packet.rssi_dbm == -71 and packet.snr_db == -2
    assert chip.reg[0x0902] == 0 and chip.reg[0x0944] & 2
    assert not radio.receiving and chip.mode == 2 and not chip.tx_calls


@pytest.mark.parametrize('flags', [0x42, 0x22, 0x202])
def test_bad_or_timed_out_receive_is_never_returned(flags):
    chip, radio = configured()
    radio.start_receive(authority_deadline=160)
    chip.flags = flags
    assert radio.poll_receive() is None
    assert not radio.fenced and not chip.tx_calls


def test_observed_finite_rx_timeout_status_rearms_without_fabricating_a_packet():
    chip, radio = configured()
    original = chip.transfer
    def timed_receive(value):
        result = original(value)
        if value[0] == 0x12 and chip.flags == 0x200:
            return bytes([result[0], 0xA6, 0x02, 0x00])
        return result
    chip.transfer = timed_receive
    for _ in range(3):
        radio.start_receive(authority_deadline=160)
        chip.mode, chip.flags = 2, 0x200
        assert radio.poll_receive() is None
        assert not radio.receiving and not radio.fenced
        assert chip.flags == 0 and chip.reg[0x0902] == 0 and chip.reg[0x0944] & 2
    assert chip.tx_calls == radio.attempts == 0


@pytest.mark.parametrize('status,flags,receiving,transmitting', [
    (0xA6, 0, True, False), (0xA6, 0x202, True, False),
    (0xA6, 0x200, False, False), (0xA6, 0x200, True, True),
    (0xD6, 0x200, True, False), (0xA8, 0x200, True, False),
    (0xAA, 0x200, True, False), (0xFF, 0x200, True, False)])
def test_rx_timeout_exception_cannot_mask_unrelated_errors(status, flags, receiving, transmitting):
    chip, radio = configured()
    original = chip.transfer
    def response(value):
        if value[0] == 0x12:
            return bytes([0, status]) + flags.to_bytes(2, 'big')
        return original(value)
    chip.transfer = response
    radio.receiving, radio.transmitting = receiving, transmitting
    with pytest.raises(HardwareError, match='sx1262_bad_status'):
        radio.irq()


def test_500khz_modulation_erratum_and_ldro_at_slow_phy():
    for profile, bit, low_rate in [(replace(PROFILE, bandwidth_hz=500000), 0, 0),
                                   (replace(PROFILE, spreading_factor=12, bandwidth_hz=62500), 4, 1)]:
        chip = Chip(); radio = SX1262(chip); radio.configure(profile)
        assert chip.reg[0x0889] & 4 == bit
        assert next(c for c in chip.commands if c[0] == 0x8B)[-1] == low_rate


def test_sf5_500khz_profile_has_a_measurable_airtime_budget_without_transmitting():
    profile = replace(PROFILE, frequency_hz=921375000, bandwidth_hz=500000,
                      spreading_factor=5)
    chip = Chip(); radio = SX1262(chip); radio.configure(profile)
    modulation = next(command for command in chip.commands if command[0] == 0x8B)
    assert modulation[1:] == bytes((5, 6, 1, 0))
    assert estimated_airtime_us(profile, 255) == 34384
    assert 193 * 1000000 / estimated_airtime_us(profile, 255) > 5500
    assert chip.tx_calls == radio.attempts == 0


def test_spi_worker_reserves_before_set_tx_and_records_unknown_on_failure(tmp_path):
    tmp_path.chmod(0o700)
    chip = Chip()
    context = b'c'*32
    journal = AttemptJournal(tmp_path/'attempts.sqlite',
        binding=journal_binding(context, chip.boot_id, PROFILE), max_attempts=20,
        max_airtime_us=10000000, create=True)
    try:
        worker = HATWorker(chip, journal=journal, profile=PROFILE, context_digest=context,
            authorize=lambda: True, authority_deadline=200, antenna_confirmed=True,
        monotonic=lambda: chip.time, time_basis='model-monotonic')
        original = chip.transfer
        def checked_transfer(command):
            if command[0] == 0x83:
                assert journal.snapshot()['attempts'] == 1
                assert journal.db.execute('SELECT state FROM attempts').fetchone()[0] == 'RESERVED'
            return original(command)
        chip.transfer = checked_transfer
        chip.fault = 'start_failure'
        result = worker.transmit(b'encrypted packet')
        assert result.outcome == 'UNKNOWN' and journal.snapshot()['fenced']
        assert journal.snapshot()['reserved_estimated_airtime_us'] > 0
        assert chip.tx_calls == 1
    finally:
        journal.close()


@pytest.mark.parametrize('late',['before','during_standby','after_standby'])
def test_completed_receive_is_preserved_before_transmit_even_during_standby(late):
    chip,radio=configured();radio.start_receive(authority_deadline=160)
    original=chip.transfer;entered_standby=False
    def transfer(command):
        nonlocal entered_standby
        reply=original(command)
        if command[0]==0x80:
            entered_standby=True
            if late=='during_standby':chip.flags=2
        if late=='after_standby' and entered_standby and command[0]==0xc0:chip.flags=2
        return reply
    chip.transfer=transfer
    if late=='before':chip.flags=2
    start=len(chip.commands)
    assert radio.start_transmit(b'outbound',authority_deadline=160,antenna_confirmed=True) is False
    assert not chip.tx_calls and radio.receive_pending and not radio.transmitting
    assert not any(c[0] in (0x02,0x0e,0x83) for c in chip.commands[start:])
    assert radio.attempts==int(late!='before')  # retain a late refusal's paid local attempt
    if late!='before':
        assert not radio.receiving and chip.mode==2
        chip.transfer=original
    assert radio.poll_receive().packet==bytes(range(255))
    assert not radio.receive_pending
    radio.start_receive(authority_deadline=160)
    assert radio.start_transmit(b'outbound',authority_deadline=160,antenna_confirmed=True) is True
    assert chip.tx_calls==1


@pytest.mark.parametrize('flags',[0x20,0x40,0x22,0x42,0x202])
def test_header_crc_or_timeout_irq_does_not_block_transmit(flags):
    chip,radio=configured();radio.start_receive(authority_deadline=160);chip.flags=flags
    assert radio.start_transmit(b'outbound',authority_deadline=160,antenna_confirmed=True) is True
    assert chip.tx_calls==1 and not radio.receive_pending


@pytest.mark.parametrize('late',['before','during_standby','after_standby'])
def test_worker_settles_pending_receive_refusal_and_drains_it_under_same_lease(tmp_path,late):
    tmp_path.chmod(0o700);chip=Chip();context=b'c'*32
    journal=AttemptJournal(tmp_path/'attempts.sqlite',binding=journal_binding(context,chip.boot_id,PROFILE),
        max_attempts=20,max_airtime_us=10000000,create=True)
    try:
        worker=HATWorker(chip,journal=journal,profile=PROFILE,context_digest=context,
            authorize=lambda:True,authority_deadline=200,antenna_confirmed=True,
            monotonic=lambda:chip.time,time_basis='model-monotonic')
        worker.receive();original_end=worker.lease_deadline;original=chip.transfer;entered_standby=False
        def transfer(command):
            nonlocal entered_standby
            reply=original(command)
            if command[0]==0x80:
                entered_standby=True
                if late=='during_standby':chip.flags=2
            if late=='after_standby' and entered_standby and command[0]==0xc0:chip.flags=2
            return reply
        chip.transfer=transfer
        if late=='before':chip.flags=2
        result=worker.transmit(b'outbound')
        assert result.outcome=='NOT_SENT' and result.driver is None and chip.tx_calls==0
        state=journal.snapshot();assert state['attempts']==1 and not state['fenced']
        assert state['reserved_estimated_airtime_us']==result.reserved_estimated_us>0
        assert journal.db.execute('SELECT state FROM attempts').fetchone()[0]=='NOT_SENT'
        chip.transfer=original
        _,received=worker.receive();assert received.packet==bytes(range(255))
        assert worker.radio.receiving and not worker.radio.receive_pending
        assert worker.lease_deadline==original_end
        assert worker.transmit(b'new-outbound').outcome=='RF_DONE' and chip.tx_calls==1
    finally:journal.close()
