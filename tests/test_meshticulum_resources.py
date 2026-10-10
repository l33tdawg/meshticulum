# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Signed resources across HTTP and compact block adapters; no physical RF."""
import base64
import http.server
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meshticulum.resources import knowledge_pack as kp
from meshticulum.resources import lamplighter as ll
from meshticulum.resources import resource as mr
from meshticulum.resources import swarm as ms


@pytest.fixture
def pack(tmp_path):
    key = Ed25519PrivateKey.generate()
    public = kp.public_key_hex(key)
    trust = {'schema': 'sage.knowledge-pack.trust-root.v1', 'note': 'test only',
        'keys': [{'key_id': kp.key_id_for(public), 'algorithm': 'ed25519', 'public_key_hex': public,
                  'status': 'active', 'created_at': '2026-10-09T00:00:00Z', 'note': 'synthetic'}]}
    chunks = [kp.build_chunk(str(i), 'Synthetic resource ' + str(i), licence='CC0-1.0',
        source='synthetic', date='2026-10-09') for i in range(8)]
    value = kp.build_manifest(pack_id='mixed-path-test', version=1, pack_class='reference',
        title='Synthetic', licence='CC0-1.0', provenance={'source': 'synthetic'},
        created_at='2026-10-09T00:00:00Z', chunks=chunks, embedding_model='test', embedding_dimension=1)
    raw = kp.manifest_bytes(value)
    directory = tmp_path / 'seed'
    kp.write_pack(directory, value, {c['sha256']: c for c in chunks}, kp.sign_manifest(raw, key))
    return directory, trust, ll.sha(raw), value


@pytest.fixture
def start_server():
    servers = []
    def start(service):
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        servers.append((service, thread))
        return 'http://127.0.0.1:' + str(service.server_port)
    yield start
    for service, thread in servers:
        service.shutdown()
        service.server_close()
        thread.join(timeout=2)


def block_source(pack, name='block-model', *, read=None):
    directory, trust, digest, value = pack
    cache = ll.VerifiedCache(directory, trust, digest)
    server = mr.BlockServer(read or cache.read)
    exchanges = []
    def exchange(request, limit, deadline):
        result = server.handle(request)
        assert len(result) <= limit
        exchanges.append((request, result, deadline))
        return result
    # Deliberately labelled model, never treated as radio performance evidence.
    route = ms.Route(name, 1, (ms.Hop('receiver', name, 'block-model-no-rf', name, 1200),))
    return ms.Source(route, mr.BlockReader(exchange, max_block=96), max_batch=1), exchanges


def test_raw_block_resource_roundtrip_has_bounded_frames_and_original_deadline(pack):
    source, frames = block_source(pack)
    path = '/packs/' + pack[2] + '/chunks/' + pack[3]['chunks'][0]['sha256'] + '.json'
    deadline = time.monotonic() + 5
    data = source.read(path, 65536, deadline)
    ll.chunk(data, pack[3]['chunks'][0])
    assert len(frames) > 1
    assert all(len(reply) <= mr.RESPONSE.size + 96 and bound == deadline for _, reply, bound in frames)
    assert b''.join(reply[mr.RESPONSE.size:] for _, reply, _ in frames) == data


@pytest.mark.parametrize('fault', ['binding', 'offset', 'size', 'short', 'status', 'growth'])
def test_invalid_block_cannot_enter_resource_cache(pack, fault):
    source, _ = block_source(pack)
    reader = source.reader
    original = reader.exchange
    calls = 0
    def broken(request, limit, deadline):
        nonlocal calls
        calls += 1
        response = original(request, limit, deadline)
        magic, binding, status, total, offset = mr.RESPONSE.unpack(response[:mr.RESPONSE.size])
        if fault == 'binding': binding = bytes(16)
        if fault == 'offset': offset += 1
        if fault == 'size': total = 1000000
        if fault == 'status': status = 99
        if fault == 'growth' and calls > 1: total += 1
        response = mr.RESPONSE.pack(magic, binding, status, total, offset) + response[mr.RESPONSE.size:]
        return response[:-1] if fault == 'short' else response
    reader.exchange = broken
    with pytest.raises(ValueError):
        source.read('/packs/' + pack[2] + '/manifest.json', 65536, time.monotonic() + 5)


def test_complete_verified_pack_over_compact_blocks_then_http_reseed(pack, tmp_path, start_server):
    source, frames = block_source(pack)
    relay = tmp_path / 'relay'
    first = ll.fetch_sources([source], pack[2], relay, pack[1])
    assert first['complete'] and first['downloaded'] == 8 and frames
    endpoint = start_server(ll.cache_server('127.0.0.1', 0, relay, pack[1], pack[2]))
    second = ll.fetch([endpoint], pack[2], tmp_path / 'recipient', pack[1])
    assert second['complete'] and second['manifest_sha256'] == first['manifest_sha256']
    assert not first['sage_imported'] and not second['sage_imported']
    # The first-hop store can vanish; the independently verified relay seeds.
    assert all((relay / 'chunks' / (entry['sha256'] + '.json')).exists() for entry in pack[3]['chunks'])


def test_complementary_partial_sources_use_both_adapters(pack, tmp_path, start_server):
    source, _ = block_source(pack)
    relay = tmp_path / 'partial'
    partial = ll.fetch_sources([source], pack[2], relay, pack[1], max_chunks=3)
    assert not partial['complete'] and partial['downloaded'] == 3
    endpoint = start_server(ll.cache_server('127.0.0.1', 0, relay, pack[1], pack[2]))
    cache = ll.VerifiedCache(pack[0], pack[1], pack[2])
    have = {e['sha256'] for e in pack[3]['chunks'][3:]}
    def read(path):
        if path.endswith('/availability.json'):
            return ll.availability_bytes(pack[2], pack[3]['chunks'], have)
        return cache.read(path)
    other, _ = block_source(pack, read=read)
    result = ll.fetch_sources([ll.http_source(endpoint), other], pack[2], tmp_path / 'recipient', pack[1])
    assert result['complete']
    assert result['chunks_by_peer'] == {endpoint: 3, other.name: 5}


def test_haves_change_only_for_verified_chunks_and_recheck_corruption(pack, tmp_path, start_server):
    source, _ = block_source(pack)
    dest = tmp_path / 'partial'
    ll.fetch_sources([source], pack[2], dest, pack[1], max_chunks=1)
    endpoint = start_server(ll.cache_server('127.0.0.1', 0, dest, pack[1], pack[2]))
    peer = ll.http_source(endpoint)
    offer = lambda: ll.read_availability(peer, pack[2], pack[3]['chunks'], time.monotonic() + 5)
    assert len(offer()) == 1
    ll.fetch_sources([source], pack[2], dest, pack[1], max_chunks=1)
    assert len(offer()) == 2  # No server restart or premature complete-pack gate.
    bad = next((dest / 'chunks').glob('*.json'))
    bad.write_bytes(b'corrupt')
    assert len(offer()) == 1
    with pytest.raises(OSError):
        peer.read('/packs/' + pack[2] + '/chunks/' + bad.name, 65536, time.monotonic() + 5)
    with pytest.raises(OSError):
        peer.read('/etc/passwd', 65536, time.monotonic() + 5)


def test_resume_changes_adapter_without_redownloading_verified_pieces(pack, tmp_path, start_server):
    source, _ = block_source(pack)
    dest = tmp_path / 'recipient'
    ll.fetch_sources([source], pack[2], dest, pack[1], max_chunks=3)
    endpoint = start_server(ll.server('127.0.0.1', 0, pack[0], pack[1]))
    result = ll.fetch([endpoint], pack[2], dest, pack[1])
    assert result['complete'] and (result['reused'], result['downloaded']) == (3, 5)
    with patch.object(ll, 'get', side_effect=AssertionError('unexpected network access')):
        offline = ll.fetch([endpoint], pack[2], dest, pack[1])
    assert offline['complete'] and offline['reused'] == 8 and offline['downloaded'] == 0


def test_misbound_or_noncanonical_availability_rejected(pack):
    source, _ = block_source(pack)
    offer = json.loads(ll.availability_bytes(pack[2], pack[3]['chunks'], set()))
    for key, bad in [('manifest_sha256', 'f' * 64), ('chunks', True), ('have_base64', 'AA==AA==')]:
        value = dict(offer, **{key: bad})
        source.reader = lambda *args, value=value: json.dumps(value).encode()
        with pytest.raises(ValueError):
            ll.read_availability(source, pack[2], pack[3]['chunks'], time.monotonic() + 5)


def test_corrupt_batch_never_commits_good_prefix_or_teaches_fast_path(pack, tmp_path):
    source, _ = block_source(pack)
    source.max_batch = 2
    source.observe_verified(1000, .1, time.monotonic())
    original = source.reader
    def corrupt(path, limit, deadline):
        raw = original(path, limit, deadline)
        if '/chunks/batch/' in path:
            value = json.loads(raw)
            value[list(value)[-1]] = base64.b64encode(b'bad').decode()
            return json.dumps(value).encode()
        return raw
    source.reader = corrupt
    dest = tmp_path / 'recipient'
    with pytest.raises(ValueError, match='no peer supplied valid chunk batch'):
        ll.fetch_sources([source], pack[2], dest, pack[1])
    assert list((dest / 'chunks').iterdir()) == [] and source._samples == 1


def test_partial_cache_still_checks_publisher_revocation(pack):
    pack[1]['keys'][0]['status'] = 'revoked'
    with pytest.raises(ValueError):
        ll.VerifiedCache(pack[0], pack[1], pack[2])


def test_wrong_json_field_type_quarantines_peer_and_falls_back(pack, tmp_path):
    bad, _ = block_source(pack, name='bad-shape')
    good, _ = block_source(pack, name='good')
    bad.max_batch = 2
    bad.observe_verified(1000, .000001, time.monotonic())
    original = bad.reader
    def wrong_shape(path, limit, deadline):
        raw = original(path, limit, deadline)
        if '/chunks/batch/' in path:
            batch = json.loads(raw)
            name = next(iter(batch))
            data = base64.b64decode(batch[name])
            chunk = json.loads(data)
            chunk['id'] = [0]
            invalid = json.dumps(chunk, sort_keys=True, separators=(',', ':')).encode()
            invalid += b' ' * (len(data) - len(invalid))
            assert len(invalid) == len(data)
            batch[name] = base64.b64encode(invalid).decode()
            return json.dumps(batch).encode()
        return raw
    bad.reader = wrong_shape
    result = ll.fetch_sources([bad, good], pack[2], tmp_path / 'recipient', pack[1])
    assert result['complete'] and result['chunks_by_peer'][bad.name] == 0
    assert result['failed_peer_attempts'] == 1 and bad._samples == 1


@pytest.mark.parametrize('where', ['headers', 'body'])
def test_http_absolute_deadline_bounds_trickle_traffic(start_server, where):
    class Trickle(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            try:
                if where == 'body':
                    self.send_response(200)
                    self.send_header('Content-Length', '50')
                    self.end_headers()
                else:
                    self.wfile.write(b'HTTP/1.0 200 OK\r\nX-Slow: ')
                for _ in range(50):
                    self.wfile.write(b'x')
                    self.wfile.flush()
                    time.sleep(.02)
            except (BrokenPipeError, ConnectionResetError):
                pass
    endpoint = start_server(http.server.HTTPServer(('127.0.0.1', 0), Trickle))
    started = time.monotonic()
    with pytest.raises((OSError, ValueError)):
        ll.get(endpoint, '/test', 200, deadline=started + .08)
    assert time.monotonic() - started < .6
