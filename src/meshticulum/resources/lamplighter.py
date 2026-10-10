#!/usr/bin/env python3
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Public-pack seed and resumable multi-peer client for the A/B lab.

This is a bounded HTTP chunk protocol, not BitTorrent wire compatibility.
Only explicitly loaded, signed, unembedded knowledge-pack-v2 content is served.
Receiving a pack never imports it into SAGE or grants its carrier authority.
"""
import argparse
import base64
from functools import wraps
import hashlib
import http.client
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse

from . import knowledge_pack as kp
from . import swarm as swarm

MAX_MANIFEST = 8 * 1024 * 1024
MAX_CHUNK = 65536
MAX_PACK = 64 * 1024 * 1024
BATCH_SIZE = 32
HEX = re.compile(r"[0-9a-f]{64}\Z")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def peer_format(function):
    """Wrong JSON field types are invalid peer data, not fatal client bugs."""
    @wraps(function)
    def checked(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (TypeError, KeyError, RecursionError) as error:
            raise ValueError('invalid peer object structure') from error
    return checked


@peer_format
def manifest(raw, signature, root, expected):
    require(HEX.fullmatch(expected) and sha(raw) == expected, "manifest pin mismatch")
    value = json.loads(raw)
    kp.validate_manifest(value)
    require(kp.manifest_bytes(value) == raw, "noncanonical manifest")
    kp.verify_manifest_signature(raw, json.loads(signature), trust_root=root)
    require(value['embedding']['embedded_chunks'] == 0, "embedded packs need separately authenticated vector bytes")
    require(0 < value['totals']['chunk_bytes'] <= MAX_PACK, "pack quota exceeded")
    require(len(raw) <= MAX_MANIFEST and len(value['chunks']) <= 20000, "manifest quota exceeded")
    require(all(0 < e['bytes'] <= MAX_CHUNK for e in value['chunks']), "chunk quota exceeded")
    return value


@peer_format
def chunk(raw, entry):
    require(len(raw) == entry['bytes'] <= MAX_CHUNK, "chunk size mismatch")
    value = json.loads(raw)
    kp.validate_chunk(value)
    require('embedding' not in value, "unexpected unauthenticated vector")
    require(kp.chunk_bytes(value) == raw and kp.content_address(value) == entry['sha256'], "chunk digest mismatch")
    require(all(value[k] == entry[k] for k in ('id', 'title', 'tags', 'sha256')), "chunk metadata mismatch")
    return value


def objects(directory, root):
    directory = Path(directory)
    require(not directory.is_symlink(), "symlink pack root")
    raw = (directory / 'manifest.json').read_bytes()
    signature = (directory / 'manifest.sig.json').read_bytes()
    digest = sha(raw)
    value = manifest(raw, signature, root, digest)
    base = '/packs/' + digest + '/'
    result = {base + 'manifest.json': raw, base + 'manifest.sig.json': signature}
    records = []
    require(not (directory / 'chunks').is_symlink(), "symlink chunk store")
    for entry in value['chunks']:
        path = directory / 'chunks' / (entry['sha256'] + '.json')
        require(not path.is_symlink() and path.stat().st_size <= MAX_CHUNK, "unsafe chunk file")
        data = path.read_bytes()
        records.append(chunk(data, entry))
        result[base + 'chunks/' + entry['sha256'] + '.json'] = data
    kp.validate_pack_rules(value, records)
    # Immutable in-memory allowlist: no request path ever opens the filesystem.
    return digest, result


def catalogue(digest):
    return kp.canonical_json({'schema': 'sage.lamplighter.catalogue.lab.v1',
        'manifests': [digest], 'authority': 'untrusted_carrier'}) + b'\n'


def server(bind, port, directory, root):
    digest, allowed = objects(directory, root)
    value = json.loads(allowed['/packs/' + digest + '/manifest.json'])
    allowed['/packs/' + digest + '/availability.json'] = availability_bytes(
        digest, value['chunks'], {e['sha256'] for e in value['chunks']})
    allowed['/catalogue.json'] = catalogue(digest)

    def read(path):
        data = allowed.get(path)
        prefix = '/packs/' + digest + '/chunks/batch/'
        if data is None and path.startswith(prefix):
            names = path[len(prefix):].split(',')
            if 1 <= len(names) <= 32 and len(set(names)) == len(names) and all(HEX.fullmatch(n) for n in names):
                paths = {n: '/packs/' + digest + '/chunks/' + n + '.json' for n in names}
                if all(p in allowed for p in paths.values()):
                    data = kp.canonical_json({n: base64.b64encode(allowed[p]).decode('ascii') for n, p in paths.items()})
        return data
    return resource_server(bind, port, read)


def resource_server(bind, port, read):
    """A finite public-object allowlist supplied by the seed or verified cache."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass

        def do_GET(self):
            try:
                data = read(self.path)
            except (OSError, ValueError):
                data = None
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('ETag', '"' + sha(data) + '"')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return http.server.HTTPServer((bind, port), Handler)


class VerifiedCache:
    """Seed only locally verified pieces, including an incomplete download.

    The manifest and independent trust root are checked at construction. Each
    HAVE/read rechecks the current piece bytes, so atomic additions become
    seedable and later corruption disappears from the next offer. This cache
    never fetches a miss, selects an upstream, or serves an arbitrary file.
    """
    def __init__(self, directory, root, digest):
        self.directory = Path(directory)
        require(not self.directory.is_symlink(), 'symlink cache root')
        for name, limit in (('manifest.json', MAX_MANIFEST), ('manifest.sig.json', 4096)):
            path = self.directory / name
            require(not path.is_symlink() and path.stat().st_size <= limit, 'unsafe cache manifest')
        self.raw = (self.directory / 'manifest.json').read_bytes()
        self.signature = (self.directory / 'manifest.sig.json').read_bytes()
        self.value = manifest(self.raw, self.signature, root, digest)
        self.digest = digest
        self.base = '/packs/' + digest + '/'
        self.entries = {e['sha256']: e for e in self.value['chunks']}

    def piece(self, name):
        require(name in self.entries, 'unknown cache piece')
        store = self.directory / 'chunks'
        path = store / (name + '.json')
        require(not store.is_symlink() and not path.is_symlink(), 'symlink cached chunk')
        require(path.stat().st_size == self.entries[name]['bytes'], 'cached chunk size mismatch')
        data = path.read_bytes()
        record = chunk(data, self.entries[name])
        kp.validate_pack_rules(self.value, [record])
        return data

    def read(self, path):
        if path == '/catalogue.json':
            return catalogue(self.digest)
        if path == self.base + 'manifest.json':
            return self.raw
        if path == self.base + 'manifest.sig.json':
            return self.signature
        if path == self.base + 'availability.json':
            have = set()
            for name in self.entries:
                try:
                    self.piece(name)
                    have.add(name)
                except (OSError, ValueError):
                    pass
            return availability_bytes(self.digest, self.value['chunks'], have)
        if path.startswith(self.base + 'chunks/batch/'):
            names = path[len(self.base + 'chunks/batch/'):].split(',')
            require(1 <= len(names) <= 32 and len(set(names)) == len(names), 'invalid batch')
            return kp.canonical_json({n: base64.b64encode(self.piece(n)).decode('ascii') for n in names})
        if path.startswith(self.base + 'chunks/') and path.endswith('.json'):
            return self.piece(path[len(self.base + 'chunks/'):-5])
        return None


def cache_server(bind, port, directory, root, digest):
    cache = VerifiedCache(directory, root, digest)
    return resource_server(bind, port, cache.read)


def peer_url(value):
    p = urllib.parse.urlsplit(value)
    require(p.scheme == 'http' and p.hostname and p.port and not p.username and not p.password
            and p.path in ('', '/') and not p.query and not p.fragment, 'invalid peer URL')
    address = ipaddress.ip_address(p.hostname)
    require(address.version == 4 and (address.is_private or address.is_loopback)
            and not address.is_unspecified and not address.is_multicast, 'peer must be explicit local IPv4')
    return value.rstrip('/')


def get(peer, path, limit, *, deadline=None):
    """Explicit local HTTP, with an absolute deadline even for trickle traffic."""
    peer = peer_url(peer)
    require(type(limit) is int and 0 < limit <= MAX_MANIFEST, 'invalid HTTP byte limit')
    require(path.startswith('/') and not path.startswith('//') and '\r' not in path
            and '\n' not in path, 'invalid resource path')
    deadline = time.monotonic() + 5 if deadline is None else deadline
    if not swarm.positive(deadline) or time.monotonic() >= deadline:
        raise TimeoutError('resource deadline expired')
    parsed = urllib.parse.urlsplit(peer)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port,
                                            timeout=max(.001, deadline - time.monotonic()))
    timer = response = None
    try:
        connection.connect()
        sock = connection.sock
        # Header parsing also obeys the total deadline. Socket timeouts alone
        # reset for each successful read, allowing an endless trickle of bytes.
        def expire():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(max(0, deadline - time.monotonic()), expire)
        timer.daemon = True
        timer.start()
        connection.request('GET', path)
        response = connection.getresponse()
        if response.status != 200:
            raise urllib.error.HTTPError(peer + path, response.status, 'unexpected HTTP status',
                                         response.headers, None)
        parts, size = [], 0
        while not response.isclosed():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('resource deadline expired')
            sock.settimeout(remaining)
            part = response.read1(min(65536, limit + 1 - size))
            if not part:
                break
            parts.append(part)
            size += len(part)
            require(size <= limit, 'oversized response')
        if time.monotonic() >= deadline:
            raise TimeoutError('resource deadline expired')
        return b''.join(parts)
    except http.client.HTTPException as error:
        raise OSError('HTTP resource transport failed') from error
    finally:
        if timer is not None:
            timer.cancel()
        if response is not None:
            response.close()
        connection.close()


def http_source(peer, *, route=None):
    peer = peer_url(peer)
    route = route or swarm.Route(peer, 1, (swarm.Hop('receiver', peer, 'http/ip',
        'http:' + peer, 65536),))
    return swarm.Source(route, lambda path, limit, deadline: get(peer, path, limit, deadline=deadline),
                        max_batch=BATCH_SIZE)


def availability_bytes(digest, entries, have):
    bits = bytearray((len(entries) + 7) // 8)
    for i, entry in enumerate(entries):
        if entry['sha256'] in have:
            bits[i // 8] |= 1 << (7 - i % 8)
    return kp.canonical_json({'schema': 'meshticulum.have.v1', 'manifest_sha256': digest,
        'chunks': len(entries), 'have_base64': base64.b64encode(bits).decode('ascii')})


@peer_format
def read_availability(source, digest, entries, deadline):
    try:
        raw = source.read('/packs/' + digest + '/availability.json', 4096, deadline)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        # The older complete-pack HTTP server has no availability endpoint.
        # This is an untrusted hint, never evidence of verified possession.
        return {e['sha256'] for e in entries}
    value = json.loads(raw)
    require(type(value) is dict and set(value) ==
            {'schema', 'manifest_sha256', 'chunks', 'have_base64'}, 'invalid availability')
    require(value['schema'] == 'meshticulum.have.v1' and value['manifest_sha256'] == digest
            and type(value['chunks']) is int and value['chunks'] == len(entries), 'availability binding mismatch')
    require(type(value['have_base64']) is str, 'invalid availability encoding')
    bits = base64.b64decode(value['have_base64'], validate=True)
    require(len(bits) == (len(entries) + 7) // 8, 'availability length mismatch')
    require(not len(entries) % 8 or not bits[-1] & ((1 << (8 - len(entries) % 8)) - 1),
            'availability padding mismatch')
    return {e['sha256'] for i, e in enumerate(entries) if bits[i // 8] & (1 << (7 - i % 8))}


def atomic(path, raw, *, mode=0o600):
    require(not path.is_symlink(), 'symlink destination')
    fd, temporary = tempfile.mkstemp(prefix='.incoming-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def fetch(peers, digest, destination, root, max_chunks=None, *, progress=None, cancelled=None,
          timeout=300, max_workers=4):
    sources = [http_source(p) for p in dict.fromkeys(peer_url(p) for p in peers)]
    return fetch_sources(sources, digest, destination, root, max_chunks,
                         progress=progress, cancelled=cancelled, timeout=timeout, max_workers=max_workers)


def fetch_sources(sources, digest, destination, root, max_chunks=None, *, progress=None,
                  cancelled=None, timeout=300, max_workers=4):
    """Same verified object identity over any explicitly supplied resource adapter.

    This API accepts admitted adapters, not peer-announced URLs/routes. A LoRa
    reader must use its existing exclusive, authenticated, budgeted owner.
    Switching sources starts a new resource request; no wire nonce, session or
    expired authorization is carried from one adapter to another.
    """
    require(HEX.fullmatch(digest), 'invalid manifest digest')
    sources = list(sources)
    require(1 <= len(sources) <= 8 and all(type(s) is swarm.Source for s in sources)
            and len({s.name for s in sources}) == len(sources)
            and (max_chunks is None or type(max_chunks) is int and max_chunks > 0)
            and swarm.positive(timeout), 'invalid transfer bounds')
    base = '/packs/' + digest + '/'
    started = time.monotonic()
    deadline = started + timeout
    def report(**event):
        if progress is not None:
            progress(event)
        if cancelled is not None and cancelled():
            raise InterruptedError('transfer paused; verified chunks retained')
        if time.monotonic() >= deadline:
            raise TimeoutError('transfer deadline expired; verified chunks retained')
    report(phase='connecting', verified=0, total=0, downloaded=0, reused=0, download_bytes=0)
    failures = 0
    rejected = set()
    destination = Path(destination)
    require(not destination.is_symlink(), 'symlink destination')
    previous = destination / 'manifest.json'
    if previous.exists():
        require(not previous.is_symlink() and not (destination / 'manifest.sig.json').is_symlink(),
                'symlink cached manifest')
        require(previous.stat().st_size <= MAX_MANIFEST
                and (destination / 'manifest.sig.json').stat().st_size <= 4096, 'cached manifest quota exceeded')
        raw, signature = previous.read_bytes(), (destination / 'manifest.sig.json').read_bytes()
        value = manifest(raw, signature, root, digest)
    else:
        for source in sorted(sources, key=lambda s: (s.seconds(4096, started), s.name)):
            try:
                bound = min(deadline, time.monotonic() + source.request_timeout)
                raw = source.read(base + 'manifest.json', MAX_MANIFEST, bound)
                signature = source.read(base + 'manifest.sig.json', 4096, bound)
                value = manifest(raw, signature, root, digest)
                break
            except (OSError, ValueError):
                failures += 1
                rejected.add(source.name)
        else:
            raise ValueError('no peer supplied an authorized manifest')
    destination.mkdir(parents=True, exist_ok=True)
    store = destination / 'chunks'
    require(not store.is_symlink(), 'symlink chunk store')
    store.mkdir(exist_ok=True)
    atomic(previous, raw)
    atomic(destination / 'manifest.sig.json', signature)
    counts = {s.name: 0 for s in sources}
    reused = downloaded = transferred = 0
    records = []
    missing = []
    for entry in value['chunks']:
        path = store / (entry['sha256'] + '.json')
        require(not path.is_symlink(), 'symlink cached chunk')
        if path.exists():
            try:
                require(path.stat().st_size <= MAX_CHUNK, 'oversized cached chunk')
                record = chunk(path.read_bytes(), entry)
                kp.validate_pack_rules(value, [record])
                records.append(record)
                reused += 1
                continue
            except (OSError, ValueError, kp.PackFormatError):
                pass  # Re-fetch damaged cache bytes; never accept them.
        missing.append(entry)
    report(phase='transferring', verified=reused, total=len(value['chunks']),
           downloaded=0, reused=reused, download_bytes=0)
    wanted = missing if max_chunks is None else missing[:max_chunks]
    available = {s.name: set() for s in sources}
    if wanted:
        for source in sources:
            if source.name in rejected:
                continue
            try:
                available[source.name] = read_availability(source, digest, value['chunks'],
                    min(deadline, time.monotonic() + source.request_timeout))
            except (OSError, ValueError):
                failures += 1
                rejected.add(source.name)

    @peer_format
    def load(source, entries, bound):
        names = [e['sha256'] for e in entries]
        response = json.loads(source.read(base + 'chunks/batch/' + ','.join(names),
            sum(e['bytes'] * 2 + 128 for e in entries), bound))
        require(type(response) is dict and set(response) == set(names), 'batch members mismatch')
        batch = []
        for entry in entries:
            require(isinstance(response[entry['sha256']], str), 'invalid chunk encoding')
            data = base64.b64decode(response[entry['sha256']], validate=True)
            record = chunk(data, entry)
            kp.validate_pack_rules(value, [record])
            batch.append((entry, data, record))
        return batch

    def commit(source, batch):
        nonlocal downloaded, transferred
        for entry, data, record in batch:
            atomic(store / (entry['sha256'] + '.json'), data)
            records.append(record)
            downloaded += 1
            transferred += len(data)
            counts[source.name] += 1
        report(phase='transferring', verified=reused + downloaded, total=len(value['chunks']),
               downloaded=downloaded, reused=reused, download_bytes=transferred,
               failed_peer_attempts=failures, chunks_by_peer=dict(counts),
               seconds=time.monotonic() - started)

    def failed(_source):
        nonlocal failures
        failures += 1

    if wanted:
        swarm.download(sources, wanted, available, load, commit, deadline=deadline,
                       cancelled=cancelled, max_workers=max_workers, failed=rejected, on_failure=failed)
    complete = len(records) == len(value['chunks'])
    if complete:
        report(phase='verifying', verified=len(records), total=len(value['chunks']),
               downloaded=downloaded, reused=reused, download_bytes=transferred)
        kp.validate_pack_rules(value, records)
        kp.load_pack(destination, trust_root=root)
    receipt = {'schema': 'sage.lamplighter.transfer.lab.v1', 'manifest_sha256': digest,
        'complete': complete, 'chunks': len(records), 'total_chunks': len(value['chunks']),
        'reused': reused, 'downloaded': downloaded, 'download_bytes': transferred,
        'chunks_by_peer': counts, 'failed_peer_attempts': failures,
        'seconds': time.monotonic() - started, 'sage_imported': False,
        'paths': [s.snapshot(time.monotonic()) for s in sources],
        'scheduler': 'verified-chunk earliest-finish; bounded independent paths',
        'evidence': 'local verification report, not a signed recipient acknowledgement'}
    atomic(destination / 'transfer-receipt.json', kp.canonical_json(
        {k: v for k, v in receipt.items() if k not in ('seconds', 'paths')}) + b'\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trust-root', required=True, type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    seed = commands.add_parser('serve')
    seed.add_argument('--pack', required=True, type=Path)
    seed.add_argument('--bind', default='127.0.0.1')
    seed.add_argument('--port', type=int, default=18090)
    cache = commands.add_parser('serve-cache', help='seed verified chunks from a complete or partial public cache')
    cache.add_argument('--pack', required=True, type=Path)
    cache.add_argument('--manifest', required=True)
    cache.add_argument('--bind', default='127.0.0.1')
    cache.add_argument('--port', type=int, default=18090)
    pull = commands.add_parser('fetch')
    pull.add_argument('--peer', action='append', required=True)
    pull.add_argument('--manifest', required=True)
    pull.add_argument('--destination', required=True, type=Path)
    pull.add_argument('--max-chunks', type=int)
    pull.add_argument('--timeout', type=float, default=300)
    pull.add_argument('--max-workers', type=int, default=4)
    args = parser.parse_args()
    root = kp.load_trust_root(args.trust_root)
    if args.command == 'serve':
        with server(args.bind, args.port, args.pack, root) as service:
            service.serve_forever()
    elif args.command == 'serve-cache':
        with cache_server(args.bind, args.port, args.pack, root, args.manifest) as service:
            service.serve_forever()
    else:
        print(json.dumps(fetch(args.peer, args.manifest, args.destination, root, args.max_chunks,
                               timeout=args.timeout, max_workers=args.max_workers), sort_keys=True))


if __name__ == '__main__':
    main()
