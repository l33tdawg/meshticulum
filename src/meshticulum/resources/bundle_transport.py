"""Explicit MB1 descriptors and bounded, signed public-file publication.

These APIs verify bytes. They create no session, grant, device access or native
authority. Callers must independently bind the descriptor and original expiry
to their authenticated transport. MR1 defaults and canonical cache formats are
unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
from typing import NamedTuple

from . import bundle_wire, byte_bundle, knowledge_pack as kp, lamplighter as ll, resource

SCHEMA = 'meshticulum.mb1-byte-bundle-transport.v1'
KIND = 'mb1-byte-bundle-transport-v1'
MAX_DESCRIPTOR = MAX_MANIFEST = 65536
MAX_SIGNATURE = 4096
_FD_TOKEN_BASE = 2 * kp.BUNDLE_BYTES + MAX_MANIFEST + MAX_SIGNATURE
FIELDS = frozenset(('schema', 'transport_kind', 'manifest_sha256', 'signature_sha256',
                    'whole_bytes', 'whole_sha256', 'parts'))


class _Part(NamedTuple):
    index: int
    chunk_content_address: str
    canonical_bytes: int
    canonical_bytes_sha256: str
    mb1_bytes: int
    mb1_sha256: str
    request_bytes: int
    request_sha256: str
    response_bytes: int
    response_sha256: str


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pin(value):
    return type(value) is str and kp.HEX64.fullmatch(value)


def _manifest(raw, signature, trust, digest):
    kp.require(type(raw) is bytes and 0 < len(raw) <= MAX_MANIFEST
               and type(signature) is bytes and 0 < len(signature) <= MAX_SIGNATURE,
               'bounded immutable bundle metadata required')
    value = ll.manifest(raw, signature, trust, digest)
    byte_bundle.descriptor(value)
    return value


def _request(digest, part):
    return resource.encode_request('/packs/' + digest + '/chunks/' + part.chunk_content_address + '.json',
                                   0, part.mb1_bytes, public32=True)


def _response(request, wire):
    return resource.RESPONSE.pack(resource.MAGIC, hashlib.sha256(request).digest()[:16], 0, len(wire), 0) + wire


def _prepare(raw, signature, trust, digest, read):
    manifest = _manifest(raw, signature, trust, digest)
    whole = byte_bundle.descriptor(manifest)
    parts = []
    for index, entry in enumerate(manifest['chunks']):
        canonical = read(entry['sha256'])
        wire = bundle_wire.encode_chunk(canonical, entry)
        part = _Part(index, entry['sha256'], len(canonical), _sha(canonical), len(wire), _sha(wire), 0, '', 0, '')
        request = _request(digest, part)
        response = _response(request, wire)
        parts.append(part._replace(request_bytes=len(request), request_sha256=_sha(request),
                                  response_bytes=len(response), response_sha256=_sha(response))._asdict())
    _manifest(raw, signature, trust, digest)
    encoded = kp.canonical_json(dict(schema=SCHEMA, transport_kind=KIND, manifest_sha256=digest,
                                    signature_sha256=_sha(signature), whole_bytes=whole['bytes'],
                                    whole_sha256=whole['sha256'], parts=parts))
    kp.require(len(encoded) <= MAX_DESCRIPTOR, 'bundle transport descriptor too large')
    return encoded


def prepare_descriptor(manifest_raw, signature_raw, trust, manifest_sha256, *, chunks):
    """Prepare data pins from caller-owned canonical bytes; this grants no authority."""
    manifest = _manifest(manifest_raw, signature_raw, trust, manifest_sha256)
    kp.require(type(chunks) is dict and set(chunks) == {e['sha256'] for e in manifest['chunks']},
               'bundle chunk roster changed')
    return _prepare(manifest_raw, signature_raw, trust, manifest_sha256, chunks.__getitem__)


class FilePublisher:
    """Read one verified canonical part at a time through retained directory FDs.

    Every read checks current named paths, identities, bounded bytes, publisher
    signature/trust and the signed part. No arbitrary request path is opened.
    The caller still owns transport authorization and must consume the iterator
    without accumulating its responses.
    """
    __slots__ = ('__cleanup', '__dict__')

    def __setattr__(self, name, value):
        if name == '_FilePublisher__cleanup' and hasattr(self, name):
            raise ValueError('bundle publisher cleanup changed')
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if name == '_FilePublisher__cleanup':
            raise ValueError('bundle publisher cleanup changed')
        object.__delattr__(self, name)

    def __init__(self, directory, manifest_raw, signature_raw, trust, manifest_sha256):
        self.path = Path(directory).absolute()
        self.raw, self.signature, self.trust, self.digest = manifest_raw, signature_raw, trust, manifest_sha256
        self.pid, self.thread = os.getpid(), threading.get_ident()
        self.closed = False
        registry, tokens, chain, metadata = {}, [], [], []
        owner = (self.pid, self.thread)
        self.owned = registry
        cleanup = ((), owner, registry, ())
        self.__cleanup = lambda: cleanup
        def freeze_cleanup():
            nonlocal cleanup
            cleanup = (tuple(tokens), owner, registry, tuple(registry.items()))
        def retain(fd, info):
            identity = self._identity(info)[:2]
            registry[fd] = identity; tokens.append((fd, identity, None))
            # pread leaves regular-file offsets untouched. Directory offsets
            # remain untouched because arbitrary directory seeks are not portable.
            if stat.S_ISREG(info.st_mode):
                token = _FD_TOKEN_BASE + fd
                kp.require(os.lseek(fd, token, os.SEEK_SET) == token, 'publisher descriptor token refused')
                tokens[-1] = (fd, identity, token)
        freeze_cleanup()
        self.chunks_fd = None
        try:
            _manifest(self.raw, self.signature, self.trust, self.digest)
            kp.require(self.path.resolve() == self.path and len(self.path.parts) <= 64,
                       'unsafe bundle publisher ancestry')
            parent = None
            for name in self.path.parts:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                info = os.fstat(fd); identity = self._identity(info)
                retain(fd, info)
                kp.require(info.st_uid in (0, os.geteuid()), 'foreign bundle publisher ancestor')
                chain.append((fd, parent, name, identity)); parent = fd
            self.directory_fd = parent
            self.chunks_fd = os.open('chunks', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                     dir_fd=parent)
            self.chunks_identity = self._identity(os.fstat(self.chunks_fd))
            retain(self.chunks_fd, os.fstat(self.chunks_fd))
            kp.require(os.fstat(self.chunks_fd).st_uid in (0, os.geteuid()), 'foreign bundle publisher directory')
            for name, expected, maximum in (('manifest.json', self.raw, MAX_MANIFEST),
                                            ('manifest.sig.json', self.signature, MAX_SIGNATURE)):
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                identity = self._file_identity(os.fstat(fd)); retain(fd, os.fstat(fd))
                self._regular(os.fstat(fd))
                kp.require(os.fstat(fd).st_size <= maximum and os.pread(fd, maximum + 1, 0) == expected
                           and identity == self._file_identity(os.fstat(fd))
                           == self._file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                           'bundle publisher metadata changed')
                metadata.append((fd, name, identity, expected, maximum))
            self.chain, self.metadata = tuple(chain), tuple(metadata)
            freeze_cleanup()
            refs = (self.path, self.raw, self.signature, self.trust, self.digest, self.directory_fd,
                    self.chunks_fd, self.chunks_identity, self.chain, self.metadata)
            self.refs = refs; self._captured_refs = lambda: refs
            self._check()
        except BaseException:
            freeze_cleanup()
            self.close()
            raise

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino, info.st_mode, info.st_uid

    @classmethod
    def _file_identity(cls, info):
        return cls._identity(info) + (info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    @staticmethod
    def _regular(info):
        kp.require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid in (0, os.geteuid()),
                   'unsafe bundle publisher file')

    def _check(self):
        refs = self._captured_refs()
        tokens, owner, registry, original = self.__cleanup()
        kp.require(not self.closed and (self.pid, self.thread) == (os.getpid(), threading.get_ident())
                   == owner and self.owned is registry and tuple(registry.items()) == original
                   and self.refs is refs and all(a is b for a, b in zip(
                       (self.path, self.raw, self.signature, self.trust, self.digest, self.directory_fd,
                        self.chunks_fd, self.chunks_identity, self.chain, self.metadata), refs)),
                   'bundle publisher rebound')
        for fd, identity, token in tokens:
            kp.require(self._identity(os.fstat(fd))[:2] == identity
                       and (token is None or os.lseek(fd, 0, os.SEEK_CUR) == token),
                       'bundle publisher descriptor changed')
        for fd, parent, name, identity in self.chain:
            kp.require(self._identity(os.fstat(fd)) == identity
                       == self._identity(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                       'bundle publisher ancestry rebound')
        kp.require(self._identity(os.fstat(self.chunks_fd)) == self.chunks_identity
                   == self._identity(os.stat('chunks', dir_fd=self.directory_fd, follow_symlinks=False)),
                   'bundle publisher chunks rebound')
        for fd, name, identity, expected, maximum in self.metadata:
            self._regular(os.fstat(fd))
            kp.require(self._file_identity(os.fstat(fd)) == identity
                       == self._file_identity(os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False))
                       and os.pread(fd, maximum + 1, 0) == expected
                       and self._file_identity(os.fstat(fd)) == identity
                       == self._file_identity(os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)),
                       'bundle publisher metadata changed')
        return _manifest(self.raw, self.signature, self.trust, self.digest)

    def read_chunk(self, content_address):
        manifest = self._check()
        kp.require(_pin(content_address), 'invalid bundle content address')
        entry = next((e for e in manifest['chunks'] if e['sha256'] == content_address), None)
        kp.require(entry is not None, 'unknown bundle publisher part')
        name = content_address + '.json'
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.chunks_fd)
        identity = None
        try:
            info = os.fstat(fd); identity = self._file_identity(info); self._regular(info)
            kp.require(info.st_size == entry['bytes'] <= kp.BUNDLE_CHUNK_BYTES, 'bundle publisher part size changed')
            raw = os.pread(fd, kp.BUNDLE_CHUNK_BYTES + 1, 0)
            kp.require(identity == self._file_identity(os.fstat(fd))
                       == self._file_identity(os.stat(name, dir_fd=self.chunks_fd, follow_symlinks=False)),
                       'bundle publisher part changed')
            record = ll.chunk(raw, entry); kp.validate_pack_rules(manifest, [record])
            self._check()
            kp.require(identity == self._file_identity(os.fstat(fd))
                       == self._file_identity(os.stat(name, dir_fd=self.chunks_fd, follow_symlinks=False)),
                       'bundle publisher part changed')
            return raw
        finally:
            try:
                if identity is None or self._identity(os.fstat(fd))[:2] == identity[:2]:
                    os.close(fd)
            except OSError:
                pass

    def close(self):
        tokens, owner, registry, original = self.__cleanup()
        if self.closed or owner != (os.getpid(), threading.get_ident()):
            return
        valid = self.owned is registry and tuple(registry.items()) == original
        self.closed = True
        for fd, identity, token in tokens:
            try:
                if self._identity(os.fstat(fd))[:2] == identity and (token is None or os.lseek(fd, 0, os.SEEK_CUR) == token):
                    os.close(fd)
            except OSError:
                pass
        if valid: registry.clear()
        kp.require(valid, 'bundle publisher ownership registry changed')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def prepare_descriptor_from_publisher(publisher):
    """Build bounded descriptor metadata from file-backed, verified parts."""
    kp.require(type(publisher) is FilePublisher, 'bounded file publisher required')
    publisher._check()
    return _prepare(publisher.raw, publisher.signature, publisher.trust, publisher.digest, publisher.read_chunk)


class MB1Transport:
    """Pinned MB1 representation with independent publisher verification."""
    def __init__(self, raw, pin, manifest_raw, signature_raw, trust, manifest_sha256):
        kp.require(type(raw) is bytes and 0 < len(raw) <= MAX_DESCRIPTOR and _pin(pin) and _sha(raw) == pin,
                   'bundle transport descriptor pin changed')
        value = json.loads(raw)
        kp.require(type(value) is dict and set(value) == FIELDS and kp.canonical_json(value) == raw
                   and value['schema'] == SCHEMA and value['transport_kind'] == KIND,
                   'bundle transport descriptor changed')
        manifest = _manifest(manifest_raw, signature_raw, trust, manifest_sha256)
        whole = byte_bundle.descriptor(manifest)
        kp.require(value['manifest_sha256'] == manifest_sha256 and value['signature_sha256'] == _sha(signature_raw)
                   and type(value['whole_bytes']) is int and value['whole_bytes'] == whole['bytes']
                   and value['whole_sha256'] == whole['sha256'] and type(value['parts']) is list
                   and len(value['parts']) == len(manifest['chunks']), 'bundle publisher binding changed')
        parts = []
        for index, (row, entry) in enumerate(zip(value['parts'], manifest['chunks'])):
            kp.require(type(row) is dict and set(row) == set(_Part._fields)
                       and all(type(row[k]) is int for k in ('index', 'canonical_bytes', 'mb1_bytes', 'request_bytes', 'response_bytes'))
                       and row['index'] == index and row['chunk_content_address'] == entry['sha256']
                       and row['canonical_bytes'] == entry['bytes']
                       and bundle_wire.HEADER.size < row['mb1_bytes'] <= kp.BUNDLE_CHUNK_BYTES
                       and row['response_bytes'] == row['mb1_bytes'] + resource.RESPONSE.size
                       and all(_pin(row[k]) for k in ('canonical_bytes_sha256', 'mb1_sha256', 'request_sha256', 'response_sha256')),
                       'bundle part descriptor changed')
            part = _Part(**row); request = _request(manifest_sha256, part)
            kp.require(part.request_bytes == len(request) and part.request_sha256 == _sha(request),
                       'bundle request binding changed')
            parts.append(part)
        self.raw, self.pin, self.manifest_raw, self.signature_raw, self.trust, self.digest = (
            raw, pin, manifest_raw, signature_raw, trust, manifest_sha256)
        self.parts = tuple(parts)
        refs = (raw, pin, manifest_raw, signature_raw, trust, manifest_sha256, self.parts)
        self.refs = refs; self._captured_refs = lambda: refs

    def _check(self):
        refs = self._captured_refs()
        kp.require(self.refs is refs and all(a is b for a, b in zip(
            (self.raw, self.pin, self.manifest_raw, self.signature_raw, self.trust, self.digest, self.parts), refs)),
            'bundle transport rebound')
        return _manifest(self.manifest_raw, self.signature_raw, self.trust, self.digest)

    def part(self, index):
        self._check()
        kp.require(type(index) is int and 0 <= index < len(self.parts), 'unplanned bundle part')
        return self.parts[index]._asdict()

    def request(self, index):
        self.part(index)
        return _request(self.digest, self.parts[index])

    def response(self, index, publisher):
        manifest = self._check(); self.part(index)
        kp.require(type(publisher) is FilePublisher and publisher.raw == self.manifest_raw
                   and publisher.signature == self.signature_raw and publisher.digest == self.digest,
                   'bundle publisher source rebound')
        canonical = publisher.read_chunk(self.parts[index].chunk_content_address)
        wire = bundle_wire.encode_chunk(canonical, manifest['chunks'][index])
        response = _response(self.request(index), wire)
        self.decode_response(index, self.request(index), response)
        return response

    def iter_responses(self, publisher, *, indices=None):
        """Yield (index, response) without accumulating a whole-file object."""
        self._check()
        indices = tuple(range(len(self.parts))) if indices is None else tuple(indices)
        kp.require(0 < len(indices) <= len(self.parts) and all(type(i) is int and 0 <= i < len(self.parts) for i in indices)
                   and tuple(sorted(set(indices))) == indices, 'invalid bundle part sequence')
        for index in indices:
            yield index, self.response(index, publisher)

    def decode_response(self, index, request, response):
        manifest = self._check(); self.part(index); part = self.parts[index]
        kp.require(type(request) is bytes and request == self.request(index)
                   and type(response) is bytes and len(response) == part.response_bytes
                   and _sha(response) == part.response_sha256, 'bundle response hash or length changed')
        magic, binding, status, total, offset = resource.RESPONSE.unpack_from(response)
        kp.require((magic, binding, status, total, offset) == (
            resource.MAGIC, hashlib.sha256(request).digest()[:16], 0, part.mb1_bytes, 0),
            'bundle response request binding changed')
        wire = response[resource.RESPONSE.size:]
        kp.require(len(wire) == part.mb1_bytes and _sha(wire) == part.mb1_sha256
                   and wire.startswith(bundle_wire.MAGIC), 'bundle wire representation changed')
        canonical = bundle_wire.decode_chunk(wire, manifest['chunks'][index])
        kp.require(len(canonical) == part.canonical_bytes and _sha(canonical) == part.canonical_bytes_sha256,
                   'bundle canonical bytes changed')
        kp.validate_pack_rules(manifest, [ll.chunk(canonical, manifest['chunks'][index])])
        self._check()
        return canonical

    def accept_response(self, receiver, index, request, response):
        kp.require(type(receiver) is byte_bundle.Receiver, 'original bundle receiver required')
        receiver._live(); self._check()
        kp.require(receiver.raw == self.manifest_raw and receiver.signature == self.signature_raw
                   and receiver.digest == self.digest, 'bundle receiver manifest changed')
        receiver.accept(index, self.decode_response(index, request, response))
        self._check()
