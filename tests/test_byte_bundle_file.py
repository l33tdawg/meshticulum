"""Atomic, bounded whole-file finalization with real signed parts and files."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from meshticulum.resources import byte_bundle as bb, knowledge_pack as kp
from tests.test_byte_bundle import Clock, inputs, package, payload, receive, signer


def ready(path, p, clock):
    receiver = receive(path, p, clock)
    for index, raw in enumerate(inputs(p)):
        receiver.accept(index, raw)
    return receiver


def memory_child(tmp_path, signer, n, *, retain_whole_file=False):
    # tracemalloc counts the entire process, including test-runner threads and
    # unrelated callbacks. Measure the real operation in an exec-spawned Python
    # process. Keep setup and output verification outside the traced interval.
    p = package(signer, n)
    for name, raw in (('manifest.json', p[0]), ('manifest.sig.json', p[1]),
                      ('trust.json', kp.canonical_json(p[2])), ('expected.bin', payload(n))):
        (tmp_path / name).write_bytes(raw)
    chunks = tmp_path / 'chunks'
    chunks.mkdir()
    for entry in p[4]['chunks']:
        (chunks / (entry['sha256'] + '.json')).write_bytes(kp.chunk_bytes(p[5][entry['sha256']]))
    root = Path(__file__).resolve().parents[1]
    helper = root / 'tests' / '_byte_bundle_file_memory_child.py'
    command = [sys.executable, '-B', str(helper), str(tmp_path)]
    if retain_whole_file:
        command.append('--retain-whole-file')
    child = subprocess.run(command, cwd=root, env={**os.environ, 'PYTHONPATH': str(root / 'src')},
                           capture_output=True, text=True, timeout=30)
    assert child.stdout, child.stderr
    return child, json.loads(child.stdout)


@pytest.mark.parametrize('n', [100000, 1000000])
def test_streamed_finalization_has_bounded_reads_and_memory(tmp_path, signer, n):
    child, report = memory_child(tmp_path, signer, n)
    assert child.returncode == 0, (child.stdout, child.stderr)
    assert report['content_verified'] and report['decoded_bytes'] == n
    assert report['largest_finalization_read'] <= 23900
    if n == 1000000:
        assert report['finish_file_peak_traced_bytes'] < 1000000
    print(json.dumps(report))


def test_memory_guard_refuses_a_retained_whole_file_allocation(tmp_path, signer):
    child, report = memory_child(tmp_path, signer, 1000000, retain_whole_file=True)
    assert child.returncode == 2, (child.stdout, child.stderr)
    assert report['content_verified'] and report['largest_finalization_read'] <= 23900
    assert report['retained_whole_file_bytes'] == 1000000
    assert report['finish_file_peak_traced_bytes'] >= 1000000
    assert report['within_memory_limit'] is False


def test_short_writes_are_completed_without_losing_bytes(tmp_path, signer, monkeypatch):
    p = package(signer); path = tmp_path / 'receiver'; original = os.write; writes = []
    with ready(path, p, Clock()) as receiver:
        def short(fd, value):
            writes.append(len(value)); return original(fd, value[:min(997, len(value))])
        monkeypatch.setattr(os, 'write', short)
        done = receiver.finish_file()
    assert len(writes) > 100 and done['complete']
    assert (path / 'bundle.bin').read_bytes() == payload()
    assert not list(path.glob('.bundle-*'))


@pytest.mark.parametrize('fault', ['write_error', 'zero_write', 'missing_part', 'expired', 'boot_changed', 'wrong_whole'])
def test_failed_finalization_cannot_publish_a_partial_file(tmp_path, signer, monkeypatch, fault):
    p = package(signer); path = tmp_path / 'receiver'; clock = Clock()
    if fault == 'wrong_whole':
        manifest = deepcopy(p[4]); desc = json.loads(manifest['pack']['provenance']['note']); desc['sha256'] = '0' * 64
        chunks = {}
        for index, entry in enumerate(manifest['chunks']):
            chunk = deepcopy(p[5][entry['sha256']]); chunk['bundle_part']['bundle_sha256'] = '0' * 64
            chunk['sha256'] = kp.content_address(chunk); entry['sha256'] = chunk['sha256']; chunks[chunk['sha256']] = chunk
            desc['parts'][index]['chunk_sha256'] = chunk['sha256']
        manifest['pack']['provenance']['note'] = kp.canonical_json(desc).decode(); raw = kp.manifest_bytes(manifest)
        p = raw, kp.canonical_json(kp.sign_manifest(raw, signer[0])), p[2], kp.sha256_hex(raw), manifest, chunks
    with ready(path, p, clock) as receiver:
        if fault in ('write_error', 'zero_write'):
            original = os.write; calls = []
            def refuse(fd, value):
                calls.append(True)
                if len(calls) == 1: return original(fd, value[:11])
                if fault == 'zero_write': return 0
                raise OSError('injected disk failure')
            monkeypatch.setattr(os, 'write', refuse)
        elif fault == 'missing_part': next((path / 'parts').iterdir()).unlink()
        elif fault == 'expired': clock.epoch = 2800000000
        elif fault == 'boot_changed': receiver.boot_reader = lambda: 'new-host'
        with pytest.raises((OSError, ValueError)): receiver.finish_file()
    assert not (path / 'bundle.bin').exists()
    assert not list(path.glob('.bundle-*'))


@pytest.mark.parametrize('where', ['staging', 'published', 'root_replaced', 'foreign_target'])
def test_finalization_detects_mutation_and_cleans_only_owned_inodes(tmp_path, signer, monkeypatch, where):
    p = package(signer); path = tmp_path / 'receiver'; clock = Clock(); held = tmp_path / 'held'
    with ready(path, p, clock) as receiver:
        if where == 'staging':
            original = receiver._live
            def tamper():
                original()
                for target in path.glob('.bundle-*'):
                    if target.stat().st_size == 100000: target.write_bytes(b'x' * 100000)
            monkeypatch.setattr(receiver, '_live', tamper)
        else:
            original = os.link
            def tamper(source, name, **kwargs):
                if where == 'root_replaced': path.rename(held); path.mkdir(mode=0o700)
                value = original(source, name, **kwargs)
                if where == 'published':
                    fd = os.open(name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=kwargs['dst_dir_fd'])
                    try: os.write(fd, b'x')
                    finally: os.close(fd)
                elif where == 'foreign_target':
                    os.unlink(name, dir_fd=kwargs['dst_dir_fd'])
                    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=kwargs['dst_dir_fd'])
                    try: os.write(fd, b'foreign')
                    finally: os.close(fd)
                return value
            monkeypatch.setattr(os, 'link', tamper)
        with pytest.raises(ValueError): receiver.finish_file()
    if where == 'foreign_target': assert (path / 'bundle.bin').read_bytes() == b'foreign'
    else: assert not (path / 'bundle.bin').exists()
    assert not list(path.glob('.bundle-*'))
    if held.exists():
        assert not (held / 'bundle.bin').exists() and not list(held.glob('.bundle-*'))


def test_existing_target_is_not_overwritten_and_resume_context_is_not_renewed(tmp_path, signer):
    p = package(signer); path = tmp_path / 'receiver'; clock = Clock()
    with ready(path, p, clock): pass
    original = (path / 'context.json').read_bytes()
    clock.epoch += 1000000; clock.mono += 1000000
    with receive(path, p, clock) as receiver:
        assert receiver.reused == 5
        done = receiver.finish_file()
        with pytest.raises(FileExistsError): receiver.finish_file()
    assert (path / 'context.json').read_bytes() == original
    assert done['reused_verified_parts'] == 5 and (path / 'bundle.bin').read_bytes() == payload()
    assert not list(path.glob('.bundle-*'))


@pytest.mark.parametrize('where', ['staging_read', 'after_link'])
def test_original_monotonic_expiry_during_finalization_removes_owned_output(tmp_path, signer, monkeypatch, where):
    p = package(signer); path = tmp_path / 'receiver'; clock = Clock()
    with ready(path, p, clock) as receiver:
        if where == 'staging_read':
            original = os.pread
            def expire(fd, count, offset):
                data = original(fd, count, offset)
                if os.fstat(fd).st_size == 100000: clock.mono = receiver.context['deadline_monotonic_us']
                return data
            monkeypatch.setattr(os, 'pread', expire)
        else:
            original = os.link
            def expire(*args, **kwargs):
                result = original(*args, **kwargs); clock.mono = receiver.context['deadline_monotonic_us']
                return result
            monkeypatch.setattr(os, 'link', expire)
        with pytest.raises(ValueError, match='original bundle deadline expired'): receiver.finish_file()
    assert not (path / 'bundle.bin').exists() and not list(path.glob('.bundle-*'))
