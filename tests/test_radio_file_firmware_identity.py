"""Offline manifest compaction uses the hardware example's identity validator."""
import json
import stat
import sys
from types import SimpleNamespace

import pytest

from meshticulum.bench.wire import WireError
from tests.test_radio_file_example import example, source_metadata


def compact_args(path, pin, output):
    return SimpleNamespace(firmware_source=path, firmware_source_sha256=pin, output=output)


def write_source(path, source):
    raw = json.dumps(source, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    path.write_bytes(raw)
    return raw, example.sha(raw)


def test_full_inventory_exports_compact_identity_with_original_digest(tmp_path):
    path, _, source = source_metadata(tmp_path)
    # Comparable to a prepared manifest with a 2.9 MiB dependency inventory.
    source['source'] = {'inventory': 'x' * 2909000}
    raw, pin = write_source(path, source)
    assert 2909000 < len(raw) < example.MAX_FIRMWARE_SOURCE_BYTES
    with pytest.raises(WireError, match='invalid_example_file'):
        example.firmware_identity(path, pin)

    output = tmp_path / 'identity.json'
    result = example.compact_firmware_identity(compact_args(path, pin, output))
    compact = output.read_bytes()
    assert len(compact) < 65536
    assert json.loads(compact) == {
        **{key: source[key] for key in ('schema', 'identity', 'application')},
        'source_manifest_sha256': pin,
    }
    assert result == dict(output=str(output), identity=source['identity'],
                          firmware_source_sha256=example.sha(compact),
                          source_manifest_sha256=pin)
    assert example.firmware_identity(output, result['firmware_source_sha256']) == source['identity']
    assert path.read_bytes() == raw
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.stat().st_nlink == 1
    assert not list(tmp_path.glob('.*.pending'))


def test_full_manifest_pin_mismatch_publishes_nothing(tmp_path):
    path, _, _ = source_metadata(tmp_path)
    output = tmp_path / 'identity.json'
    with pytest.raises(WireError, match='example_file_hash_mismatch'):
        example.compact_firmware_identity(compact_args(path, '0' * 64, output))
    assert not output.exists()


def test_exact_wired_queue_source_inventory_has_its_own_identity(tmp_path):
    path, _, source = source_metadata(tmp_path)
    source['application'].update({'burst_adapter.hpp': 'b' * 64,
                                  'experimental/tx_burst.hpp': 'c' * 64})
    source['identity'] = 'mtb1-' + example.sha(json.dumps(
        source['application'], sort_keys=True).encode())[:10]
    _, pin = write_source(path, source)
    assert example.firmware_identity(path, pin) == source['identity']
    del source['application']['experimental/tx_burst.hpp']
    source['identity'] = 'mtb1-' + example.sha(json.dumps(
        source['application'], sort_keys=True).encode())[:10]
    _, pin = write_source(path, source)
    with pytest.raises(WireError, match='firmware_source_mismatch'):
        example.firmware_identity(path, pin)


def test_profiled_inventory_pins_both_profiler_and_generated_build_option(tmp_path):
    path, _, source = source_metadata(tmp_path)
    source['application'].update({'burst_adapter.hpp': 'b' * 64,
        'experimental/tx_burst.hpp': 'c' * 64, 'native_profile.hpp': 'd' * 64,
        'native_profile_config.hpp': 'e' * 64})
    def identity():
        return 'mtb1-' + example.sha(json.dumps(
            source['application'], sort_keys=True).encode())[:10]
    source['identity'] = identity()
    _, pin = write_source(path, source)
    assert example.firmware_identity(path, pin) == source['identity']
    old_identity = source['identity']
    source['application']['native_profile_config.hpp'] = 'f' * 64
    source['identity'] = identity()
    _, pin = write_source(path, source)
    assert example.firmware_identity(path, pin) != old_identity
    # Neither a partial profiler inventory nor an unrecognized file is accepted,
    # even if its self-reported identity correctly hashes that inventory.
    for removed, added in [('native_profile.hpp', None),
                           ('native_profile_config.hpp', None),
                           (None, 'extra.hpp')]:
        original = dict(source['application'])
        if removed:
            del source['application'][removed]
        if added:
            source['application'][added] = 'a' * 64
        source['identity'] = identity()
        _, pin = write_source(path, source)
        with pytest.raises(WireError, match='firmware_source_mismatch'):
            example.firmware_identity(path, pin)
        source['application'] = original


@pytest.mark.parametrize('pin', [None, 0, '', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64])
def test_invalid_pin_is_refused_before_source_read(tmp_path, pin):
    output = tmp_path / 'identity.json'
    with pytest.raises(WireError, match='invalid_example_digest'):
        example.compact_firmware_identity(compact_args(tmp_path / 'absent', pin, output))
    assert not output.exists()


@pytest.mark.parametrize('fault', [
    'not_mapping', 'wrong_schema', 'missing_application', 'wrong_application_type',
    'missing_file', 'extra_file', 'bad_hash_type', 'bad_hash', 'wrong_identity',
])
def test_invalid_application_metadata_is_refused_without_output(tmp_path, fault):
    path, _, source = source_metadata(tmp_path)
    if fault == 'not_mapping':
        source = []
    elif fault == 'wrong_schema':
        source['schema'] = 'unrecognized'
    elif fault == 'missing_application':
        del source['application']
    elif fault == 'wrong_application_type':
        source['application'] = []
    elif fault == 'missing_file':
        del source['application']['main.cpp']
    elif fault == 'extra_file':
        source['application']['extra.cpp'] = 'a' * 64
    elif fault == 'bad_hash_type':
        source['application']['main.cpp'] = 0
    elif fault == 'bad_hash':
        source['application']['main.cpp'] = 'A' * 64
    elif fault == 'wrong_identity':
        source['identity'] = 'mtb1-' + '0' * 10
    _, pin = write_source(path, source)
    output = tmp_path / 'identity.json'
    with pytest.raises(WireError, match='example_firmware_source_mismatch'):
        example.compact_firmware_identity(compact_args(path, pin, output))
    assert not output.exists()


@pytest.mark.parametrize('kind', ['file', 'symlink'])
def test_existing_output_is_never_replaced(tmp_path, kind):
    path, pin, _ = source_metadata(tmp_path)
    retained = tmp_path / 'retained.json'
    retained.write_bytes(b'previous output')
    output = tmp_path / 'identity.json'
    if kind == 'file':
        output.write_bytes(b'existing')
    else:
        output.symlink_to(retained)
    with pytest.raises(FileExistsError):
        example.compact_firmware_identity(compact_args(path, pin, output))
    assert retained.read_bytes() == b'previous output'
    if kind == 'file':
        assert output.read_bytes() == b'existing'
    else:
        assert output.is_symlink() and output.readlink() == retained
    assert not list(tmp_path.glob('.*.pending'))


def test_oversized_source_is_refused_before_json_parsing(tmp_path):
    path = tmp_path / 'SOURCE.json'
    with path.open('wb') as source:
        source.truncate(example.MAX_FIRMWARE_SOURCE_BYTES + 1)
    output = tmp_path / 'identity.json'
    with pytest.raises(WireError, match='invalid_example_file'):
        example.compact_firmware_identity(compact_args(path, '0' * 64, output))
    assert not output.exists()


def test_cli_returns_compact_pin_without_hardware_access(tmp_path, monkeypatch, capsys):
    path, pin, source = source_metadata(tmp_path)
    output = tmp_path / 'identity.json'
    monkeypatch.setattr(sys, 'argv', ['radio_file.py', 'firmware-identity',
        '--firmware-source', str(path), '--firmware-source-sha256', pin,
        '--output', str(output)])
    example.main()
    result = json.loads(capsys.readouterr().out)
    assert result['source_manifest_sha256'] == pin
    assert example.firmware_identity(output, result['firmware_source_sha256']) == source['identity']
