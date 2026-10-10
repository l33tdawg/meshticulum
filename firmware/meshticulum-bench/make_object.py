"""Create a retained synthetic benchmark object; no device or network access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets

WORK = Path(__file__).resolve().parents[2] / 'work/meshticulum-abc-deployment-20261009'


def manifest_for(chunks):
    if not chunks or len(chunks) > 64 or any(type(c) is not bytes or not 1 <= len(c) <= 16384 for c in chunks):
        raise ValueError('invalid_chunk_bounds')
    object_id = hashlib.sha256(b''.join(chunks)).hexdigest()
    return {'schema': 'meshticulum.synthetic-object.v1', 'object_id': object_id,
        'bytes': sum(map(len, chunks)), 'chunks': [{'object_id': object_id, 'chunk_index': i,
            'bytes': len(chunk), 'sha256': hashlib.sha256(chunk).hexdigest()}
            for i, chunk in enumerate(chunks)]}


def validate(manifest):
    if type(manifest) is not dict or set(manifest) != {'schema', 'object_id', 'bytes', 'chunks'}:
        raise ValueError('invalid_object_manifest')
    if manifest['schema'] != 'meshticulum.synthetic-object.v1' or type(manifest['chunks']) is not list:
        raise ValueError('invalid_object_schema')
    if not 1 <= len(manifest['chunks']) <= 64:
        raise ValueError('invalid_object_chunk_count')
    object_id = manifest['object_id']
    if type(object_id) is not str or len(object_id) != 64 or bytes.fromhex(object_id).hex() != object_id:
        raise ValueError('invalid_object_id')
    for i, descriptor in enumerate(manifest['chunks']):
        if (type(descriptor) is not dict or set(descriptor) != {'object_id', 'chunk_index', 'bytes', 'sha256'}
                or descriptor['object_id'] != object_id or type(descriptor['chunk_index']) is not int
                or descriptor['chunk_index'] != i or type(descriptor['bytes']) is not int
                or not 1 <= descriptor['bytes'] <= 16384):
            raise ValueError('invalid_chunk_descriptor')
        value = descriptor['sha256']
        if type(value) is not str or len(value) != 64 or bytes.fromhex(value).hex() != value:
            raise ValueError('invalid_chunk_hash')
    if type(manifest['bytes']) is not int or manifest['bytes'] != sum(d['bytes'] for d in manifest['chunks']):
        raise ValueError('invalid_object_length')
    return manifest


def load_chunk(path, index):
    path = Path(path)
    manifest = validate(json.loads(path.read_text()))
    if type(index) is not int or not 0 <= index < len(manifest['chunks']):
        raise ValueError('invalid_chunk_index')
    # Verify every retained random chunk so a resumed run keeps one exact
    # complete-object identity. No manifest field is used as a file path.
    content = []
    for descriptor in manifest['chunks']:
        data = (path.parent / ('%02d.bin' % descriptor['chunk_index'])).read_bytes()
        if len(data) != descriptor['bytes'] or hashlib.sha256(data).hexdigest() != descriptor['sha256']:
            raise ValueError('synthetic_chunk_changed')
        content.append(data)
    if hashlib.sha256(b''.join(content)).hexdigest() != manifest['object_id']:
        raise ValueError('synthetic_object_changed')
    return manifest, content[index]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bytes', type=int, default=8192)
    parser.add_argument('--chunk-bytes', type=int, default=2048)
    args = parser.parse_args()
    if not 1 <= args.chunk_bytes <= 16384 or not 1 <= args.bytes <= 64 * args.chunk_bytes:
        parser.error('Use 1..64 chunks, each at most16384bytes.')
    os.umask(0o077)
    value = secrets.token_bytes(args.bytes)
    chunks = [value[i:i + args.chunk_bytes] for i in range(0, len(value), args.chunk_bytes)]
    manifest = manifest_for(chunks)
    base = WORK / 'synthetic-objects'; base.mkdir(mode=0o700, exist_ok=True)
    root = base / manifest['object_id']; root.mkdir(mode=0o700)
    for i, chunk in enumerate(chunks): (root / ('%02d.bin' % i)).write_bytes(chunk)
    (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'manifest': str(root / 'manifest.json'), 'object_id': manifest['object_id'],
                      'bytes': manifest['bytes'], 'chunks': len(chunks), 'devices_contacted': False}))


if __name__ == '__main__': main()
