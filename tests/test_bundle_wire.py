"""Opt-in wire reduction retains original signed chunks and receiver fences."""
from copy import deepcopy
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from meshticulum.resources import byte_bundle, bundle_wire, knowledge_pack as kp, lamplighter as ll


@pytest.fixture
def published():
    payload = bytes(range(256))*390+bytes(range(160))
    key = Ed25519PrivateKey.generate()
    manifest, chunks, signature = byte_bundle.build(payload, pack_id='wire-test',
        created_at='2026-10-13T00:00:00Z', private_key=key, exact32=True)
    raw = kp.manifest_bytes(manifest); signed = kp.canonical_json(signature)
    trust = {'schema': 'sage-foundation.knowledge-pack.trust.v1', 'keys': [{
        'key_id': signature['key_id'], 'public_key_hex': key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        'status': 'active', 'pack_classes': ['reference']}]}
    digest = kp.sha256_hex(raw)
    assert ll.manifest(raw, signed, trust, digest) == manifest
    return payload, raw, signed, trust, digest, manifest, chunks


def first(published):
    entry = published[5]['chunks'][0]
    raw = kp.chunk_bytes(published[6][entry['sha256']])
    return entry, raw, bundle_wire.encode_chunk(raw, entry)


def changed_metadata(wire, change):
    magic, length, payload_length, padding = bundle_wire.HEADER.unpack_from(wire)
    encoded = wire[bundle_wire.HEADER.size:bundle_wire.HEADER.size+length]
    updated = change(encoded)
    return bundle_wire.HEADER.pack(magic, len(updated), payload_length,
                                  padding-len(updated)+len(encoded))+updated+wire[bundle_wire.HEADER.size+length:]


def test_compact_and_legacy_decode_reproduce_exact_signed_canonical_chunks(published):
    _, manifest_raw, signature_raw, trust, digest, manifest, chunks = published
    total = 0
    for entry in manifest['chunks']:
        raw = kp.chunk_bytes(chunks[entry['sha256']])
        wire = bundle_wire.encode_chunk(raw, entry)
        assert bundle_wire.decode_chunk(wire, entry) == raw
        assert bundle_wire.decode_chunk(raw, entry) == raw
        assert ll.chunk(bundle_wire.decode_chunk(wire, entry), entry) == chunks[entry['sha256']]
        total += len(wire)
    assert total == 103205
    assert ll.manifest(manifest_raw, signature_raw, trust, digest) == manifest
    assert hashlib.sha256(manifest_raw).hexdigest() == digest


@pytest.mark.parametrize('mutation', ['raw_payload', 'padding', 'payload_length', 'metadata_length', 'truncated', 'trailing', 'version'])
def test_wire_tampering_cannot_become_a_verified_chunk(published, mutation):
    entry, raw, wire = first(published)
    magic, metadata, payload, padding = bundle_wire.HEADER.unpack_from(wire)
    if mutation == 'raw_payload': wire = wire[:-1]+bytes([wire[-1]^1])
    elif mutation == 'padding': wire = bundle_wire.HEADER.pack(magic, metadata, payload, padding+1)+wire[bundle_wire.HEADER.size:]
    elif mutation == 'payload_length': wire = bundle_wire.HEADER.pack(magic, metadata, payload-1, padding)+wire[bundle_wire.HEADER.size:]
    elif mutation == 'metadata_length': wire = bundle_wire.HEADER.pack(magic, metadata+1, payload, padding)+wire[bundle_wire.HEADER.size:]
    elif mutation == 'truncated': wire = wire[:-1]
    elif mutation == 'trailing': wire += b'\0'
    else: wire = b'MB2\0'+wire[4:]
    with pytest.raises(ValueError): bundle_wire.decode_chunk(wire, entry)


@pytest.mark.parametrize('mutation', ['duplicate', 'whitespace', 'unknown', 'reserved', 'float', 'bool'])
def test_metadata_has_no_duplicate_unknown_or_noncanonical_ambiguity(published, mutation):
    entry, raw, wire = first(published)
    def modify(encoded):
        value = json.loads(encoded)
        if mutation == 'duplicate': return encoded.replace(b'"raw_bytes":23900', b'"raw_bytes":23900,"raw_bytes":23900', 1)
        if mutation == 'whitespace': return encoded+b' '
        if mutation == 'unknown': value['unexpected'] = 0
        elif mutation == 'reserved': value['bundle_part']['payload_base64'] = ''
        elif mutation == 'float': value['bundle_part']['index'] = 0.0
        else: value['bundle_part']['index'] = False
        return kp.canonical_json(value)
    with pytest.raises(ValueError): bundle_wire.decode_chunk(changed_metadata(wire, modify), entry)


def test_cross_piece_and_wrong_publisher_entry_are_refused(published):
    entry, raw, wire = first(published)
    with pytest.raises(ValueError): bundle_wire.decode_chunk(wire, published[5]['chunks'][1])
    wrong = deepcopy(entry); wrong['sha256'] = '0'*64
    with pytest.raises(ValueError): bundle_wire.decode_chunk(wire, wrong)


def test_oversized_reconstruction_is_refused_before_base64_or_padding_expansion(published, monkeypatch):
    entry, raw, wire = first(published)
    magic, metadata, payload, padding = bundle_wire.HEADER.unpack_from(wire)
    wire = bundle_wire.HEADER.pack(magic, metadata, payload, 65535)+wire[bundle_wire.HEADER.size:]
    called = []
    monkeypatch.setattr(bundle_wire.base64, 'b64encode', lambda _: called.append(True))
    with pytest.raises(ValueError): bundle_wire.decode_chunk(wire, entry)
    assert called == []


def test_exact_receiver_signature_whole_sha_replay_and_original_expiry_remain(published, tmp_path):
    payload, raw, signature, trust, digest, manifest, chunks = published
    directory = tmp_path/'receiver'; directory.mkdir(mode=0o700)
    now = [1000000, 2000000]
    with byte_bundle.Receiver(directory, raw, signature, trust, digest,
        expires_epoch_us=10000000, clock=lambda: tuple(now), boot_reader=lambda: 'wire-test-boot') as receiver:
        for index, entry in enumerate(manifest['chunks']):
            wire = bundle_wire.encode_chunk(kp.chunk_bytes(chunks[entry['sha256']]), entry)
            receiver.accept(index, bundle_wire.decode_chunk(wire, entry))
        done = receiver.finish()
        assert done['useful_decoded_bytes'] == 100000
        assert done['whole_file_sha256'] == hashlib.sha256(payload).hexdigest()
        assert (directory/'bundle.bin').read_bytes() == payload
        entry = manifest['chunks'][0]; canonical = kp.chunk_bytes(chunks[entry['sha256']])
        with pytest.raises(ValueError): receiver.accept(0, canonical)
    now[:] = [10000000, 11000000]
    with pytest.raises(ValueError): byte_bundle.Receiver(directory, raw, signature, trust, digest,
        expires_epoch_us=10000000, clock=lambda: tuple(now), boot_reader=lambda: 'wire-test-boot')
