# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Source fault controls for the ROM round trip; these are not device evidence."""
import importlib.util
from pathlib import Path

import pytest

from tests.test_meshticulum_bench_flash import ROM, OLD, NEW, BINDING, flash, receipt

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'rollback_exercise', ROOT / 'firmware/meshticulum-bench/exercise_rollback.py')
exercise = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exercise)

BEFORE = dict(firmware='bench-test', mcu_boot='boot-1', disarmed=True,
              bench_arm_commands=0, bench_tx_commands=0)


def installed(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    state = flash.apply(rom, NEW, OLD, record, BINDING)
    state.update(phase='active_disarmed', application=BEFORE)
    record.save(state)
    rom.data[0x9000:0x9008] = b'NEW-BOOT'
    rom.reset_count = 0

    def reset():
        assert bytes(rom.data[0x10000:0x10000 + len(NEW)]) == NEW
        assert record.state()['phase'] == 'flash_verified'
        assert record.state()['rollback_exercise']['bench_verified'] is True
        rom.reset_count += 1

    rom.reset_app = reset
    rom.probe_app = lambda version: dict(BEFORE, mcu_boot='boot-2')
    return rom, record


def run(rom, record):
    return exercise.cycle(flash, rom, record, BINDING, NEW, BEFORE['firmware'], BEFORE)


def test_real_transaction_path_roundtrip_restores_old_but_only_boots_bench(tmp_path):
    rom, record = installed(tmp_path)
    before = bytes(rom.data)
    state = run(rom, record)
    assert bytes(rom.data) == before and rom.reset_count == 1
    assert len(rom.writes) == 3
    assert rom.writes[1][1] == record.read('app-before.bin')
    assert rom.writes[2][1][:len(NEW)] == NEW
    assert state['phase'] == 'active_disarmed' and state['application']['mcu_boot'] == 'boot-2'
    proof = state['rollback_exercise']
    assert proof['phase'] == 'complete' and proof['prior_verified'] and proof['bench_verified']
    assert proof['prior_full_image_md5'] == flash.md5(OLD)
    assert proof['outside_before'] == proof['outside_after_rollback'] == proof['outside_after_bench_restore']
    assert proof['prior_application_reset_commands'] == 0
    assert proof['bench_arm_commands'] == proof['bench_tx_commands'] == 0
    # The normal rollback remains valid after a successful exercise.
    flash.rollback(rom, record, BINDING)
    assert bytes(rom.data[0x10000:0x10000 + len(OLD)]) == OLD


@pytest.mark.parametrize('step', ['rollback', 'bench_partial', 'bench_readback', 'outside'])
def test_write_failures_never_request_an_application_boot(tmp_path, step):
    rom, record = installed(tmp_path)
    write = rom.write

    def failing(offset, payload):
        if len(rom.writes) == (1 if step == 'rollback' else 2):
            if step in ('rollback', 'bench_partial'):
                rom.failure = 'partial_once'
            elif step == 'bench_readback':
                rom.failure = 'bad_readback_once'
            elif step == 'outside':
                rom.data[0x120000] ^= 1
        write(offset, payload)

    rom.write = failing
    with pytest.raises(Exception):
        run(rom, record)
    assert rom.reset_count == 0 and record.state()['phase'] == 'recovery_required'


def test_failed_write_intent_commit_prevents_restoring_bench_or_booting_old(tmp_path):
    rom, record = installed(tmp_path)
    save = record.save

    def fail_once(state):
        if state['phase'] == 'writing':
            raise OSError('simulated_receipt_failure')
        save(state)

    record.save = fail_once
    with pytest.raises(OSError):
        run(rom, record)
    assert rom.reset_count == 0 and len(rom.writes) == 2
    assert bytes(rom.data[0x10000:0x10000 + len(OLD)]) == OLD
    assert record.state()['phase'] == 'recovery_required'


@pytest.mark.parametrize('change', ['app', 'backup', 'identity', 'repeated'])
def test_stale_or_corrupt_inputs_refuse_all_writes(tmp_path, change):
    rom, record = installed(tmp_path)
    state = record.state()
    if change == 'app':
        rom.data[0x10000 + 42] ^= 1
    elif change == 'backup':
        (record.directory / 'app-before.bin').write_bytes(b'corrupt')
    elif change == 'identity':
        state['binding']['unit'] = 'unit-b'
        record.save(state)
    else:
        state['rollback_exercise'] = {'phase': 'unknown'}
        record.save(state)
    with pytest.raises(Exception):
        run(rom, record)
    assert rom.reset_count == 0 and len(rom.writes) == 1


@pytest.mark.parametrize('fault', ['timeout', 'same_boot'])
def test_failed_bench_boot_check_does_not_boot_prior_firmware(tmp_path, fault):
    rom, record = installed(tmp_path)

    def probe(version):
        if fault == 'timeout':
            raise TimeoutError('simulated_probe_failure')
        return BEFORE

    rom.probe_app = probe
    with pytest.raises(Exception):
        run(rom, record)
    assert rom.reset_count == 1 and len(rom.writes) == 3
    assert bytes(rom.data[0x10000:0x10000 + len(NEW)]) == NEW
    assert record.state()['phase'] == 'recovery_required'
