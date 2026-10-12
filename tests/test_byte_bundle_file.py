"""Atomic, bounded whole-file finalization with real signed parts and files."""
from copy import deepcopy
import hashlib
import json
import os
import tracemalloc

import pytest

from meshticulum.resources import byte_bundle as bb, knowledge_pack as kp
from tests.test_byte_bundle import Clock, inputs, package, payload, receive, signer


def ready(path, p, clock):
    receiver = receive(path, p, clock)
    for index, raw in enumerate(inputs(p)):
        receiver.accept(index, raw)
    return receiver


@pytest.mark.parametrize('n', [100000, 1000000])
def test_streamed_finalization_has_bounded_reads_and_memory(tmp_path, signer, n, monkeypatch):
    p = package(signer, n); path = tmp_path / 'receiver'; observed = []; original = os.pread
    with ready(path, p, Clock()) as receiver:
        def bounded(fd, count, offset):
            if os.fstat(fd).st_size == n:
                observed.append(count)
                assert count <= bb.PART_BYTES
            return original(fd, count, offset)
        monkeypatch.setattr(os, 'pread', bounded)
        tracemalloc.start()
        try:
            result = receiver.finish_file()
            _, peak = tracemalloc.get_traced_memory()
        finally: tracemalloc.stop()
    assert observed and max(observed) <= 23900
    assert (path / 'bundle.bin').read_bytes() == payload(n)
    assert result['read_block_bytes'] == 23900 and result['whole_file_sha256'] == hashlib.sha256(payload(n)).hexdigest()
    if n == 1000000:
        assert peak < 1000000  # No whole-file bytearray or full-file reread allocation.
    print(json.dumps({'source_only': True, 'decoded_bytes': n, 'finish_file_peak_traced_bytes': peak,
                      'largest_finalization_read': max(observed)}))


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
