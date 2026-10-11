# SPDX-License-Identifier: Apache-2.0
"""Release-input integrity controls; no board or compiler is invoked."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
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
