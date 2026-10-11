"""SF5/6 source controls; configuration models do not qualify native RF."""
from dataclasses import replace
import math
import struct

import pytest

from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.hat_worker import HATWorker, journal_binding
from meshticulum.bench.serial_protocol import RadioProfile, SerialError
from meshticulum.bench.serial_worker import estimated_airtime_us
from meshticulum.bench.sx1262 import SX1262
from meshticulum.bench.wire import WireError
from tests.radio_profiles import SOURCE_ADMISSION
from tests.test_meshticulum_bench_sx1262 import Chip

FAST = RadioProfile(921375000, 500000, 5, 5, 0, 12, 0x42, 60000, 20, 10000000)


@pytest.mark.parametrize('sf', [5, 6])
@pytest.mark.parametrize('board', [1, 2])
def test_fast_profile_encodes_only_with_recommended_preamble(sf, board):
    profile = replace(FAST, spreading_factor=sf)
    raw = profile.encode(board_id=board)
    assert len(raw) == 30 and raw[8] == sf and struct.unpack('!H', raw[11:13])[0] == 12
    for preamble in (0, 8, 11, 65):
        with pytest.raises(SerialError, match='invalid_profile'):
            replace(profile, preamble=preamble).encode(board_id=board)


@pytest.mark.parametrize('sf', [4, 13])
def test_factors_outside_sx1262_range_still_refused(sf):
    with pytest.raises(SerialError, match='invalid_profile'):
        replace(FAST, spreading_factor=sf).encode(board_id=1)


def test_existing_native_waveform_admission_cannot_enable_fast_profile():
    with pytest.raises(WireError, match='unsupported_admitted_bench_profile'):
        SOURCE_ADMISSION.check('MY', True, FAST)
    admitted = RadioAdmission('MY', ((FAST.frequency_hz, FAST.bandwidth_hz,
        FAST.spreading_factor, FAST.coding_rate, FAST.chip_power_dbm, FAST.preamble,
        FAST.sync_word),), 1000, 60000, 20, 10000000)
    admitted.check('MY', True, FAST)
    with pytest.raises(WireError, match='unsupported_admitted_bench_profile'):
        admitted.check('MY', True, replace(FAST, spreading_factor=6))


@pytest.mark.parametrize('sf,bytes_,expected', [
    (5, 1, 2320), (5, 16, 4240), (5, 70, 10960), (5, 234, 32080), (5, 255, 34640),
    (6, 1, 4000), (6, 16, 7200), (6, 70, 18720), (6, 234, 53920), (6, 255, 58400),
])
def test_semtech_explicit_crc_fast_airtime_vectors(sf, bytes_, expected):
    assert estimated_airtime_us(replace(FAST, spreading_factor=sf), bytes_) == expected


def test_all_original_vectors_are_unchanged():
    for sf in range(7, 13):
        for bw in (62500, 125000, 250000, 500000):
            for cr in range(5, 9):
                profile = replace(FAST, spreading_factor=sf, bandwidth_hz=bw,
                                  coding_rate=cr, preamble=8)
                symbol_us = (1 << sf) * 1000000 / bw
                low_rate = int(symbol_us >= 16000)
                for size in range(1, 256):
                    coded = max(0, math.ceil((8 * size + 44 - 4 * sf) / (4 * (sf-2*low_rate))))
                    old = math.ceil((profile.preamble + 12.25 + coded * cr) * symbol_us)
                    assert estimated_airtime_us(profile, size) == old


@pytest.mark.parametrize('sf', [5, 6])
def test_direct_hat_configuration_uses_exact_fast_explicit_crc_and_bw_erratum(sf):
    chip, profile = Chip(), replace(FAST, spreading_factor=sf)
    radio = SX1262(chip)
    radio.configure(profile)
    assert bytes([0x8B, sf, 6, 1, 0]) in chip.commands
    assert bytes([0x8C]) + struct.pack('!HBBBB', 12, 0, 255, 1, 0) in chip.commands
    assert chip.reg[0x0889] & 4 == 0 and chip.tx_calls == 0


@pytest.mark.parametrize('sf', [5, 6])
def test_fast_done_reserves_full_correct_airtime_before_spi_and_never_refunds(tmp_path, sf):
    tmp_path.chmod(0o700)
    chip, profile = Chip(), replace(FAST, spreading_factor=sf)
    context, packet = b'c' * 32, b'x' * 255
    journal = AttemptJournal(tmp_path / 'attempts.sqlite',
        binding=journal_binding(context, chip.boot_id, profile),
        max_attempts=20, max_airtime_us=10000000, create=True)
    try:
        worker = HATWorker(chip, journal=journal, profile=profile, context_digest=context,
            authorize=lambda: True, authority_deadline=200, antenna_confirmed=True)
        estimate = estimated_airtime_us(profile, len(packet))
        reservation = math.ceil(estimate * 1.10) + 1000
        transfer = chip.transfer
        def checked_transfer(command):
            if command[0] == 0x83:
                state = journal.snapshot()
                assert state['attempts'] == 1 and state['reserved_estimated_airtime_us'] == reservation
                assert journal.db.execute('SELECT state FROM attempts').fetchone()[0] == 'RESERVED'
            return transfer(command)
        chip.transfer = checked_transfer
        result = worker.transmit(packet)
        assert result.outcome == 'RF_DONE' and result.driver.estimated_airtime_us == estimate
        assert result.reserved_estimated_us == reservation and chip.tx_calls == 1
        assert journal.snapshot()['reserved_estimated_airtime_us'] == reservation
        assert journal.db.execute('SELECT state FROM attempts').fetchone()[0] == 'RF_DONE'
    finally:
        journal.close()


@pytest.mark.parametrize('sf', [5, 6])
def test_fast_unknown_keeps_full_charge_and_cannot_retry(tmp_path, sf):
    tmp_path.chmod(0o700)
    chip, profile = Chip(), replace(FAST, spreading_factor=sf)
    context, packet = b'c' * 32, b'x' * 255
    journal = AttemptJournal(tmp_path / 'attempts.sqlite',
        binding=journal_binding(context, chip.boot_id, profile),
        max_attempts=20, max_airtime_us=10000000, create=True)
    try:
        worker = HATWorker(chip, journal=journal, profile=profile, context_digest=context,
            authorize=lambda: True, authority_deadline=200, antenna_confirmed=True)
        chip.fault = 'start_failure'
        result = worker.transmit(packet)
        estimate = estimated_airtime_us(profile, len(packet))
        assert result.outcome == 'UNKNOWN' and worker.fenced and chip.tx_calls == 1
        assert result.reserved_estimated_us == math.ceil(estimate * 1.10) + 1000
        assert journal.snapshot()['reserved_estimated_airtime_us'] == result.reserved_estimated_us
        assert journal.db.execute('SELECT state FROM attempts').fetchone()[0] == 'UNKNOWN'
        assert not any(c[0] == 0x82 for c in chip.commands)
    finally:
        journal.close()
