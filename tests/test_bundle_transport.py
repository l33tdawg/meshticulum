"""File-backed signed resources only; no session, native or radio qualification."""
from copy import deepcopy
import hashlib
import json
import os

import pytest

from meshticulum.resources import bundle_transport as bt, knowledge_pack as kp, resource
from tests.test_byte_bundle import Clock, inputs, package, payload, receive, signer


def source(path, p):
    path.mkdir(mode=0o700)
    (path / 'chunks').mkdir(mode=0o700)
    (path / 'manifest.json').write_bytes(p[0])
    (path / 'manifest.sig.json').write_bytes(p[1])
    for entry, raw in zip(p[4]['chunks'], inputs(p)):
        (path / 'chunks' / (entry['sha256'] + '.json')).write_bytes(raw)
    return bt.FilePublisher(path, *p[:4])


@pytest.mark.parametrize('n', [100000, 1000000])
@pytest.mark.parametrize('exact32', [False, True])
def test_bounded_publisher_descriptor_and_receiver_roundtrip(tmp_path, signer, n, exact32, monkeypatch):
    p = package(signer, n, exact32)
    maxima = []
    original = os.pread
    def observed(fd, count, offset):
        maxima.append(count)
        return original(fd, count, offset)
    with source(tmp_path / 'source', p) as publisher:
        raw = bt.prepare_descriptor_from_publisher(publisher)
        canonical = {e['sha256']: data for e, data in zip(p[4]['chunks'], inputs(p))}
        assert raw == bt.prepare_descriptor(*p[:4], chunks=canonical)
        transport = bt.MB1Transport(raw, kp.sha256_hex(raw), *p[:4])
        value = json.loads(raw)
        assert value['schema'] == bt.SCHEMA and 'lantern' not in value['schema']
        assert value['whole_bytes'] == n and value['whole_sha256'] == hashlib.sha256(payload(n)).hexdigest()
        monkeypatch.setattr(os, 'pread', observed)
        with receive(tmp_path / 'receiver', p, Clock()) as receiver:
            for index, response in transport.iter_responses(publisher):
                assert len(response) <= 32768
                assert transport.decode_response(index, transport.request(index), response) == canonical[p[4]['chunks'][index]['sha256']]
                transport.accept_response(receiver, index, transport.request(index), response)
            done = receiver.finish_file()
    assert maxima and max(maxima) <= bt.MAX_MANIFEST + 1
    assert (tmp_path / 'receiver' / 'bundle.bin').read_bytes() == payload(n)
    assert done['complete'] and done['publisher_verified'] and done['file_backed']
    assert done['useful_decoded_bytes'] == n and not done['native_transport_qualified']
    assert resource.MAX_BLOCK == 16384  # No MR1 default upgrade.


@pytest.mark.parametrize('fault', ['truncated', 'extra', 'cross_part', 'wrong_request', 'wrong_header'])
def test_response_tampering_never_enters_quarantine(tmp_path, signer, fault):
    p = package(signer)
    with source(tmp_path / 'source', p) as publisher:
        raw = bt.prepare_descriptor_from_publisher(publisher)
        transport = bt.MB1Transport(raw, kp.sha256_hex(raw), *p[:4])
        request = transport.request(0); response = transport.response(0, publisher)
        if fault == 'truncated': response = response[:-1]
        elif fault == 'extra': response += b'x'
        elif fault == 'cross_part': response = transport.response(1, publisher)
        elif fault == 'wrong_request': request = transport.request(1)
        else: response = b'bad!' + response[4:]
        with receive(tmp_path / 'receiver', p, Clock()) as receiver:
            with pytest.raises(ValueError): transport.accept_response(receiver, 0, request, response)
        assert not list((tmp_path / 'receiver' / 'parts').iterdir())


@pytest.mark.parametrize('fault', ['bool_index', 'oversized_wire', 'bad_canonical_size', 'unknown_field',
                                 'duplicate_field', 'wrong_whole', 'wrong_signature', 'path_address'])
def test_descriptor_refuses_malformed_or_foreign_binding(signer, fault):
    p = package(signer)
    raw = bt.prepare_descriptor(*p[:4], chunks={e['sha256']: d for e, d in zip(p[4]['chunks'], inputs(p))})
    value = json.loads(raw)
    if fault == 'bool_index': value['parts'][0]['index'] = False
    elif fault == 'oversized_wire': value['parts'][0]['mb1_bytes'] = 1000000
    elif fault == 'bad_canonical_size': value['parts'][0]['canonical_bytes'] -= 1
    elif fault == 'unknown_field': value['authority'] = True
    elif fault == 'wrong_whole': value['whole_sha256'] = '0' * 64
    elif fault == 'wrong_signature': value['signature_sha256'] = '0' * 64
    elif fault == 'path_address': value['parts'][0]['chunk_content_address'] = '../secret'
    raw = kp.canonical_json(value)
    if fault == 'duplicate_field': raw = raw.replace(b'"whole_bytes":100000', b'"whole_bytes":100000,"whole_bytes":100000')
    with pytest.raises(ValueError): bt.MB1Transport(raw, kp.sha256_hex(raw), *p[:4])


@pytest.mark.parametrize('fault', ['root', 'chunks', 'piece_symlink', 'piece_hardlink', 'same_inode_content',
                                 'metadata_content', 'revoked_signer', 'unknown_piece'])
def test_publisher_checks_fresh_files_paths_and_trust(tmp_path, signer, fault):
    p = package(signer); path = tmp_path / 'source'
    with source(path, p) as publisher:
        target = path / 'chunks' / (p[4]['chunks'][0]['sha256'] + '.json')
        address = p[4]['chunks'][0]['sha256']
        if fault == 'root':
            path.rename(tmp_path / 'retained-source'); path.mkdir(mode=0o700)
        elif fault == 'chunks':
            (path / 'chunks').rename(path / 'old-chunks'); (path / 'chunks').mkdir(mode=0o700)
        elif fault == 'piece_symlink':
            original = tmp_path / 'other'; original.write_bytes(target.read_bytes())
            target.unlink(); target.symlink_to(original)
        elif fault == 'piece_hardlink': os.link(target, tmp_path / 'other-link')
        elif fault == 'same_inode_content':
            before = target.stat().st_ino; target.write_bytes(b'x' * target.stat().st_size)
            assert target.stat().st_ino == before
        elif fault == 'metadata_content': (path / 'manifest.json').write_bytes(b'{}')
        elif fault == 'revoked_signer': p[2]['keys'][0]['status'] = 'revoked'
        else: address = '../private-file'
        with pytest.raises((OSError, ValueError)): publisher.read_chunk(address)


def test_publisher_file_growth_is_bounded_and_refused(tmp_path, signer, monkeypatch):
    p = package(signer); path = tmp_path / 'source'
    with source(path, p) as publisher:
        target = path / 'chunks' / (p[4]['chunks'][0]['sha256'] + '.json')
        inode = target.stat().st_ino; original = os.pread; observed = []
        def grow(fd, count, offset):
            if os.fstat(fd).st_ino == inode:
                observed.append(count); target.write_bytes(b'x' * 2000000)
            return original(fd, count, offset)
        monkeypatch.setattr(os, 'pread', grow)
        with pytest.raises(ValueError): publisher.read_chunk(p[4]['chunks'][0]['sha256'])
        assert observed == [32741]


def test_publisher_rechecks_source_between_yields_and_resume_selection_is_exact(tmp_path, signer):
    p = package(signer); path = tmp_path / 'source'
    with source(path, p) as publisher:
        raw = bt.prepare_descriptor_from_publisher(publisher)
        transport = bt.MB1Transport(raw, kp.sha256_hex(raw), *p[:4])
        assert [i for i, _ in transport.iter_responses(publisher, indices=(1, 4))] == [1, 4]
        for wrong in ((), (1, 1), (4, 1), (False,), (5,)):
            with pytest.raises(ValueError): list(transport.iter_responses(publisher, indices=wrong))
        iterator = transport.iter_responses(publisher)
        assert next(iterator)[0] == 0
        (path / 'chunks' / (p[4]['chunks'][1]['sha256'] + '.json')).write_bytes(b'{}')
        with pytest.raises(ValueError): next(iterator)


def test_closed_publisher_and_rebound_transport_cannot_supply_bytes(tmp_path, signer):
    p = package(signer)
    with source(tmp_path / 'source', p) as publisher:
        raw = bt.prepare_descriptor_from_publisher(publisher)
        transport = bt.MB1Transport(raw, kp.sha256_hex(raw), *p[:4])
        transport.parts = transport.parts[:-1]
        with pytest.raises(ValueError): transport.request(0)
    with pytest.raises(ValueError): publisher.read_chunk(p[4]['chunks'][0]['sha256'])


def test_failed_publisher_construction_closes_only_its_opened_fds(tmp_path, signer, monkeypatch):
    p = package(signer); path = tmp_path / 'source'
    with source(path, p): pass
    (path / 'manifest.sig.json').write_bytes(b'bad signature')
    opened = []; original = os.open
    def recorded(*args, **kwargs):
        fd = original(*args, **kwargs); opened.append(fd); return fd
    monkeypatch.setattr(os, 'open', recorded)
    with pytest.raises(ValueError): bt.FilePublisher(path, *p[:4])
    assert opened
    for fd in opened:
        with pytest.raises(OSError): os.fstat(fd)
