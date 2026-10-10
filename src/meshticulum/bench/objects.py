"""Durable verified public/synthetic chunks, independent of expired RF sessions.

A new session can reuse an already verified chunk, never its old nonce or
radio-attempt state. Only trusted management supplies object descriptors; a
received radio payload cannot choose a filesystem path or grant a service.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets

from .authority import _open, _private_info, canonical
from .wire import require


class VerifiedChunks:
    def __init__(self, directory):
        self.path = Path(directory).absolute()
        self.path.mkdir(mode=0o700, exist_ok=True)
        _private_info(self.path.lstat(), directory=True)

    def _paths(self, descriptor):
        require(type(descriptor) is dict and set(descriptor) == {'object_id', 'chunk_index', 'sha256', 'bytes'},
                'invalid_chunk_descriptor')
        for key in ('object_id', 'sha256'):
            value = descriptor[key]
            require(type(value) is str and len(value) == 64 and bytes.fromhex(value).hex() == value,
                    'invalid_chunk_digest')
        require(type(descriptor['chunk_index']) is int and 0 <= descriptor['chunk_index'] < 64
                and type(descriptor['bytes']) is int and 1 <= descriptor['bytes'] <= 16384,
                'invalid_chunk_bound')
        root = self.path / descriptor['object_id']
        root.mkdir(mode=0o700, exist_ok=True)
        _private_info(root.lstat(), directory=True)
        stem = root / str(descriptor['chunk_index'])
        return root, stem.with_suffix('.json'), stem.with_suffix('.bin')

    @staticmethod
    def _read(path, limit):
        fd = _open(path)
        try:
            data = os.read(fd, limit + 1)
            require(len(data) <= limit, 'chunk_file_too_large')
            return data
        finally:
            os.close(fd)

    def get(self, descriptor):
        _, meta, data = self._paths(descriptor)
        if not meta.exists():
            return None
        require(self._read(meta, 4096) == canonical(descriptor), 'chunk_descriptor_changed')
        value = self._read(data, 16384)
        require(len(value) == descriptor['bytes'] and hashlib.sha256(value).hexdigest() == descriptor['sha256'],
                'stored_chunk_corrupt')
        return value

    @staticmethod
    def _publish(path, data):
        # Same-directory link is an atomic no-replace publish. Metadata is
        # published last, so a crash cannot claim an incomplete chunk exists.
        pending = path.with_name('.' + secrets.token_hex(16) + '.pending')
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data); output.flush(); os.fsync(output.fileno())
            os.link(pending, path, follow_symlinks=False)
        finally:
            pending.unlink(missing_ok=True)

    def commit(self, descriptor, payload):
        require(type(payload) is bytes and len(payload) == descriptor['bytes']
                and hashlib.sha256(payload).hexdigest() == descriptor['sha256'], 'received_chunk_mismatch')
        root, meta, data = self._paths(descriptor)
        previous = self.get(descriptor)
        if previous is not None:
            require(previous == payload, 'chunk_content_conflict')
            return False
        if data.exists():
            require(self._read(data, 16384) == payload, 'incomplete_chunk_content_conflict')
        else:
            self._publish(data, payload)
        self._publish(meta, canonical(descriptor))
        for directory in (root, self.path):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        return True

    def assemble(self, descriptors):
        require(type(descriptors) is list and 1 <= len(descriptors) <= 64, 'invalid_object_manifest')
        object_id = descriptors[0]['object_id']
        require(all(d['object_id'] == object_id and d['chunk_index'] == i
                    for i, d in enumerate(descriptors)), 'invalid_object_order')
        chunks = [self.get(d) for d in descriptors]
        require(all(value is not None for value in chunks), 'object_incomplete')
        value = b''.join(chunks)
        require(hashlib.sha256(value).hexdigest() == object_id, 'whole_object_hash_mismatch')
        return value
