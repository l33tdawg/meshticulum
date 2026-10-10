# SPDX-License-Identifier: Apache-2.0
"""Artifact repeatability and retained-input/failed-output controls."""
import importlib.util
import io
from pathlib import Path
import tarfile
import pytest

SPEC = importlib.util.spec_from_file_location('normalize_sdist',
    Path(__file__).resolve().parents[1] / 'tools/normalize_sdist.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def archive(path, entries, stamp):
    with tarfile.open(path, 'w:gz') as out:
        for name, data, mode in entries:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), mode
            member.mtime, member.uid, member.gid = stamp, stamp, stamp
            member.uname, member.gname = str(stamp), str(stamp)
            member.pax_headers = {'mtime': str(stamp) + '.125', 'atime': str(stamp)}
            out.addfile(member, io.BytesIO(data))


def contents(path):
    with tarfile.open(path) as incoming:
        return {m.name: (incoming.extractfile(m).read(), m.mode)
                for m in incoming if m.isfile()}


def test_repeated_source_archives_preserve_content_and_modes_and_originals(tmp_path):
    entries = [('pkg/LICENSE', b'license bytes', 0o644),
               ('pkg/NOTICE', b'notice bytes', 0o644),
               ('pkg/tool.py', b'print(1)\n', 0o755)]
    first, second = tmp_path / 'first.tar.gz', tmp_path / 'second.tar.gz'
    archive(first, entries, 100)
    archive(second, list(reversed(entries)), 200)
    originals = first.read_bytes(), second.read_bytes()
    assert originals[0] != originals[1]
    one, two = tmp_path / 'one.tar.gz', tmp_path / 'two.tar.gz'
    MODULE.normalize(first, one, 123456)
    MODULE.normalize(second, two, 123456)
    assert one.read_bytes() == two.read_bytes()
    assert contents(one) == contents(first) == contents(second)
    assert (first.read_bytes(), second.read_bytes()) == originals


@pytest.mark.parametrize('names,reason', [
    (['pkg/LICENSE', 'pkg/LICENSE'], 'duplicate_archive_path'),
    (['pkg/LICENSE', '../outside'], 'unsafe_archive_path')])
def test_invalid_archive_creates_no_reviewable_output(tmp_path, names, reason):
    source, result = tmp_path / 'bad.tar.gz', tmp_path / 'result.tar.gz'
    archive(source, [(n, b'x', 0o644) for n in names], 100)
    with pytest.raises(ValueError, match=reason):
        MODULE.normalize(source, result, 123456)
    assert not result.exists()
    assert not list(tmp_path.glob('.sdist-*'))


def test_existing_output_and_inputs_are_never_overwritten(tmp_path):
    source, result = tmp_path / 'source.tar.gz', tmp_path / 'result.tar.gz'
    archive(source, [('pkg/LICENSE', b'license', 0o644)], 100)
    before = source.read_bytes()
    result.write_bytes(b'prior artifact')
    with pytest.raises(FileExistsError):
        MODULE.normalize(source, result, 123456)
    assert result.read_bytes() == b'prior artifact'
    assert source.read_bytes() == before
