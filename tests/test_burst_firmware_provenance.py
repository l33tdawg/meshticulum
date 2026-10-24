"""Portable source-build provenance controls; no compiler or device is opened."""
import importlib.util
import json
from pathlib import Path
import shutil

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


def test_current_six_inputs_match_independent_build_provenance():
    result = VERIFY.verify(ROOT)
    assert result['application_inputs'] == 6 and not result['binary_checked']
    assert result['firmware']['bytes'] > 0


def test_source_edit_invalidates_recorded_firmware_identity(copied_source):
    source = copied_source / 'firmware/meshticulum-bench/burst_adapter.hpp'
    source.write_bytes(source.read_bytes() + b'\n// edited after build\n')
    with pytest.raises(ValueError, match='source_drift'):
        VERIFY.verify(copied_source)


@pytest.mark.parametrize('change,reason', [
    (lambda record: record.update(identity='mtb1-0000000000'), 'identity_drift'),
    (lambda record: record.update(recipe_sha256='0' * 64), 'recipe_drift'),
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
