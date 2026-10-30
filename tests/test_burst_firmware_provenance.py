"""Portable source-build provenance controls; no compiler or device is opened."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('verify_burst_firmware',
                                             ROOT / 'tools/verify_burst_firmware.py')
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


@pytest.fixture
def copied_source(tmp_path):
    names = ['provenance/MCU_BURST_FIRMWARE.json']
    names += ['firmware/meshticulum-bench/' + name for name in
              (*VERIFY.APPLICATION, 'inputs.lock.json', 'prepare_firmware.py')]
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return tmp_path


def test_historical_six_input_record_remains_valid_without_claiming_current_source():
    result = VERIFY.verify(ROOT)
    assert result['application_inputs'] == 6 and not result['binary_checked']
    assert result['firmware']['bytes'] > 0
    assert result['identity'] == 'mtb1-6cfde1fdd4'
    assert result['validation_scope'] == 'immutable_historical_build_record'
    assert result['historical_record_sha256'] == VERIFY.HISTORICAL_RECORD_SHA256
    # The new native-profile recipe prepares eight inputs with its own identity.
    assert result['current_source_matches'] is False
    assert result['current_inputs']['recipe_matches'] is False


def test_source_edit_does_not_rewrite_history_and_strict_current_check_refuses(copied_source):
    source = copied_source / 'firmware/meshticulum-bench/burst_adapter.hpp'
    source.write_bytes(source.read_bytes() + b'\n// edited after build\n')
    result = VERIFY.verify(copied_source)
    assert result['identity'] == 'mtb1-6cfde1fdd4'
    assert result['current_inputs']['recorded_application_files_match'] is False
    assert result['current_source_matches'] is False
    with pytest.raises(ValueError, match='source_drift'):
        VERIFY.verify(copied_source, require_current=True)


@pytest.mark.parametrize('change,reason', [
    (lambda record: record.update(identity='mtb1-0000000000'), 'identity_drift'),
    (lambda record: record.update(recipe_sha256='0' * 64), 'historical_firmware_record_changed'),
    (lambda record: record.update(device_identifier='forbidden'), 'record_shape'),
    (lambda record: record['builds'][1]['firmware'].update(sha256='0' * 64),
     'reproduction_changed'),
    (lambda record: record.update(rf_qualified=True), 'evidence_scope'),
])
def test_changed_or_nonallowlisted_provenance_is_rejected(copied_source, change, reason):
    path = copied_source / 'provenance/MCU_BURST_FIRMWARE.json'
    record = json.loads(path.read_bytes())
    change(record)
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match=reason):
        VERIFY.verify(copied_source)


def test_optional_binary_must_match_recorded_bytes_and_digest(copied_source):
    binary = copied_source / 'firmware.bin'
    binary.write_bytes(b'changed firmware')
    with pytest.raises(ValueError, match='binary_changed'):
        VERIFY.verify(copied_source, binary=binary)


@pytest.mark.parametrize('added', [
    {'native_profile.hpp': 'a' * 64},
    {'native_profile.hpp': 'a' * 64, 'native_profile_config.hpp': 'b' * 64},
])
def test_new_prepared_inventory_cannot_be_relabelled_as_historical_build_evidence(copied_source, added):
    path = copied_source / 'provenance/MCU_BURST_FIRMWARE.json'
    record = json.loads(path.read_bytes())
    record['application'].update(added)
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='invalid_burst_firmware_inputs'):
        VERIFY.verify(copied_source)


def test_consistent_replacement_inventory_and_identity_do_not_replace_history(copied_source):
    path = copied_source / 'provenance/MCU_BURST_FIRMWARE.json'
    record = json.loads(path.read_bytes())
    record['application']['main.cpp'] = 'a' * 64
    record['identity'] = 'mtb1-' + VERIFY.hashlib.sha256(
        json.dumps(record['application'], sort_keys=True).encode()).hexdigest()[:10]
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='historical_firmware_record_changed'):
        VERIFY.verify(copied_source)


def test_unknown_toolchain_fields_are_rejected(copied_source):
    path = copied_source / 'provenance/MCU_BURST_FIRMWARE.json'
    record = json.loads(path.read_bytes())
    record['toolchain']['extra'] = 'unreviewed'
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match='invalid_burst_firmware_toolchain'):
        VERIFY.verify(copied_source)


@pytest.fixture
def synthetic_matching_record(copied_source, monkeypatch):
    """A separately pinned synthetic checksum fixture, not firmware evidence."""
    path = copied_source / 'provenance/MCU_BURST_FIRMWARE.json'
    firmware = copied_source / 'firmware/meshticulum-bench'
    record = json.loads(path.read_bytes())
    record['application'] = {name: VERIFY.digest(firmware / name) for name in VERIFY.APPLICATION}
    record['identity'] = 'mtb1-' + VERIFY.hashlib.sha256(
        json.dumps(record['application'], sort_keys=True).encode()).hexdigest()[:10]
    record['recipe_sha256'] = VERIFY.digest(firmware / 'prepare_firmware.py')
    path.write_text(json.dumps(record))
    monkeypatch.setattr(VERIFY, 'HISTORICAL_RECORD_SHA256', VERIFY.digest(path))
    assert VERIFY.verify(copied_source, require_current=True)['current_source_matches'] is True
    return copied_source


@pytest.mark.parametrize('name,field,reason', [
    ('main.cpp', 'recorded_application_files_match', 'source_drift'),
    ('inputs.lock.json', 'lock_matches', 'lock_drift'),
    ('prepare_firmware.py', 'recipe_matches', 'recipe_drift'),
])
def test_exact_current_mode_detects_each_independent_input_change(
        synthetic_matching_record, name, field, reason):
    path = synthetic_matching_record / 'firmware/meshticulum-bench' / name
    path.write_bytes(path.read_bytes() + b'\n')
    result = VERIFY.verify(synthetic_matching_record)
    assert result['current_inputs'][field] is False
    assert result['current_source_matches'] is False
    with pytest.raises(ValueError, match=reason):
        VERIFY.verify(synthetic_matching_record, require_current=True)


def test_cli_labels_historical_scope_and_exposes_explicit_strict_mode(
        copied_source, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['verify_burst_firmware.py', '--root', str(copied_source)])
    VERIFY.main()
    output = json.loads(capsys.readouterr().out)
    assert output['validation_scope'] == 'immutable_historical_build_record'
    assert output['current_source_matches'] is False
    monkeypatch.setattr(sys, 'argv', [*sys.argv, '--require-current'])
    with pytest.raises(ValueError, match='(source|recipe)_drift'):
        VERIFY.main()


@pytest.mark.parametrize('value', [0, 1, None, 'true'])
def test_current_requirement_is_an_exact_boolean(copied_source, value):
    with pytest.raises(ValueError, match='invalid_current_source_requirement'):
        VERIFY.verify(copied_source, require_current=value)
