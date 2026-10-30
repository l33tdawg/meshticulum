# SPDX-License-Identifier: Apache-2.0
"""Release-input integrity controls; no board or compiler is invoked."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import subprocess
import pytest


@pytest.fixture
def recipe():
    path = Path(__file__).resolve().parents[1] / 'firmware/meshticulum-bench/prepare_firmware.py'
    spec = importlib.util.spec_from_file_location('firmware_recipe_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_substitution_is_rejected_before_any_output(recipe, tmp_path, monkeypatch):
    archive = tmp_path / 'source.tar.gz'
    archive.write_bytes(b'changed')
    row = dict(id='example', filename=archive.name, bytes=7,
               sha256=hashlib.sha256(b'correct').hexdigest())
    monkeypatch.setattr(recipe, 'lock', lambda: dict(inputs=[row]))
    monkeypatch.setattr(recipe.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(recipe.platform, 'machine', lambda: 'arm64')
    output = tmp_path / 'never-created'
    with pytest.raises(RuntimeError, match='firmware_archive_mismatch'):
        recipe.prepare(tmp_path, output)
    assert not output.exists()


@pytest.mark.parametrize('member_type', [tarfile.REGTYPE, tarfile.SYMTYPE])
def test_archive_path_or_link_cannot_escape_the_new_workspace(recipe, tmp_path, member_type):
    archive = tmp_path / 'bad.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        member = tarfile.TarInfo('../outside' if member_type == tarfile.REGTYPE else 'link')
        member.type = member_type
        if member_type == tarfile.SYMTYPE:
            member.linkname = '../outside'
            tar.addfile(member)
        else:
            member.size = 4
            tar.addfile(member, io.BytesIO(b'data'))
    with pytest.raises(tarfile.FilterError):
        recipe.extract(archive, tmp_path / 'output', None)
    assert not (tmp_path / 'outside').exists()


def test_changed_prepared_source_cannot_invoke_the_build_tool(recipe, tmp_path, monkeypatch):
    output = tmp_path / 'prepared'
    output.mkdir()
    (output / 'source.cpp').write_bytes(b'changed')
    record = dict(schema='meshticulum.raw-firmware-prepared.v1',
        lock_sha256=recipe.sha(recipe.HERE / 'inputs.lock.json'),
        recipe_sha256=recipe.sha(Path(recipe.__file__)),
        source={'source.cpp': hashlib.sha256(b'original').hexdigest()})
    (output / 'SOURCE.json').write_text(json.dumps(record))
    invoked = []
    monkeypatch.setattr(recipe.subprocess, 'check_output', lambda *a, **k: invoked.append(a))
    with pytest.raises(RuntimeError, match='prepared_firmware_source_drift'):
        recipe.build(output, tmp_path / 'never-executed-python', list(recipe.TARGETS))
    assert not invoked and not (output / 'evidence').exists()


def test_existing_archive_is_verified_instead_of_silently_refetched(recipe, tmp_path, monkeypatch):
    (tmp_path / 'input.tar.gz').write_bytes(b'bad')
    monkeypatch.setattr(recipe, 'lock', lambda: dict(inputs=[dict(id='x',filename='input.tar.gz',
        bytes=3,sha256=hashlib.sha256(b'yes').hexdigest(),url='https://example.invalid')]))
    calls = []
    monkeypatch.setattr(recipe.urllib.request, 'urlopen', lambda *a, **k: calls.append(a))
    with pytest.raises(RuntimeError, match='firmware_archive_mismatch'):
        recipe.fetch(tmp_path)
    assert not calls and (tmp_path / 'input.tar.gz').read_bytes() == b'bad'


def test_profile_build_setting_is_pinned_in_exact_application_identity(recipe, tmp_path):
    off, off_id = recipe.prepare_application(tmp_path / 'off')
    on, on_id = recipe.prepare_application(tmp_path / 'on', native_profile=True)
    again, again_id = recipe.prepare_application(tmp_path / 'again', native_profile=True)
    assert on == again and on_id == again_id and off_id != on_id
    assert set(off) == set(on) == {'main.cpp', 'protocol.hpp', 'gfsk_profile.hpp',
        'gfsk250_profile.hpp', 'burst_adapter.hpp', 'experimental/tx_burst.hpp',
        'native_profile.hpp', 'native_profile_config.hpp'}
    assert {name for name in off if off[name] != on[name]} == {'native_profile_config.hpp'}
    assert '#define MTBENCH_NATIVE_PROFILE 0' in (tmp_path / 'off/native_profile_config.hpp').read_text()
    assert '#define MTBENCH_NATIVE_PROFILE 1' in (tmp_path / 'on/native_profile_config.hpp').read_text()
    assert '#error' in (tmp_path / 'on/native_profile_config.hpp').read_text()


@pytest.mark.parametrize('option', [0, 1, None, 'true'])
def test_profile_option_requires_bool_before_creating_output(recipe, tmp_path, option):
    with pytest.raises(RuntimeError, match='invalid_native_profile_option'):
        recipe.prepare_application(tmp_path / 'never-created', native_profile=option)
    assert not (tmp_path / 'never-created').exists()


def test_generated_profile_config_rejects_external_override(recipe, tmp_path):
    recipe.prepare_application(tmp_path / 'on', native_profile=True)
    header = tmp_path / 'on/native_profile_config.hpp'
    command = ['clang++', '-x', 'c++', '-E', '-include', str(header), '-']
    valid = subprocess.run(command, input='MTBENCH_NATIVE_PROFILE\n', capture_output=True,
                           text=True, timeout=10)
    assert valid.returncode == 0 and valid.stdout.rstrip().endswith('1')
    changed = subprocess.run(command[:-1] + ['-DMTBENCH_NATIVE_PROFILE=0', '-'],
                             input='', capture_output=True, text=True, timeout=10)
    assert changed.returncode != 0 and 'pinned by the prepared application' in changed.stderr


def test_changed_profile_metadata_cannot_invoke_build_tool(recipe, tmp_path, monkeypatch):
    output = tmp_path / 'prepared'
    app = output / 'project/examples/meshticulum_bench'
    app.parent.mkdir(parents=True)
    application, identity = recipe.prepare_application(app, native_profile=True)
    record = dict(schema='meshticulum.raw-firmware-prepared.v1',
        lock_sha256=recipe.sha(recipe.HERE / 'inputs.lock.json'),
        recipe_sha256=recipe.sha(Path(recipe.__file__)),
        source=recipe.tree(output), application=application, identity=identity,
        build_options=dict(native_profile=False))
    (output / 'SOURCE.json').write_text(json.dumps(record))
    invoked = []
    monkeypatch.setattr(recipe.subprocess, 'check_output', lambda *a, **k: invoked.append(a))
    with pytest.raises(RuntimeError, match='firmware_application_identity_mismatch'):
        recipe.build(output, tmp_path / 'never-executed-python', list(recipe.TARGETS))
    assert not invoked and not (output / 'evidence').exists()
