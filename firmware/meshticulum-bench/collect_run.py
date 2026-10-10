"""Read bounded public bench evidence; never reads session keys or payloads."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys


def need(ok, code):
    if not ok: raise RuntimeError(code)


def read(path, limit, *, private=True):
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
             'unsafe_evidence_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
             and not info.st_mode & (0o077 if private else 0o022) and info.st_size <= limit,
             'unsafe_evidence_file')
        with os.fdopen(fd, 'rb', closefd=False) as stream: return stream.read(limit + 1)
    finally: os.close(fd)


def main():
    need(os.geteuid() == 0 and sys.flags.isolated and len(sys.argv) == 5,
         'requires_root_unit_run_runtime_manifest')
    unit, identifier, runtime_pin, manifest_pin = sys.argv[1:]
    need(unit in ('unit-a', 'unit-b', 'unit-c') and re.fullmatch('[0-9a-f]{32}', identifier),
         'invalid_run_target')
    for value in (runtime_pin, manifest_pin):
        need(re.fullmatch('[0-9a-f]{64}', value), 'invalid_runtime_pin')
    bundle = Path('/opt/meshticulum-bench') / ('runtime-' + runtime_pin[:16])
    raw = read(bundle / 'manifest.json', 20000)
    need(hashlib.sha256(raw).hexdigest() == manifest_pin, 'runtime_manifest_changed')
    manifest = json.loads(raw)
    for name, pin in manifest['files'].items():
        part = Path(name)
        need(not part.is_absolute() and '..' not in part.parts, 'invalid_runtime_member')
        need(hashlib.sha256(read(bundle / part, 500000)).hexdigest() == pin, 'runtime_source_changed')
    root = Path('/var/lib/meshticulum-bench/runs') / identifier
    result = {'schema': 'meshticulum.collected-run.v1', 'unit': unit, 'run_id': identifier,
        'runtime_archive_sha256': runtime_pin, 'runtime_manifest_sha256': manifest_pin,
        'collected_host_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'files': {}, 'result': None, 'provision': None, 'attempts': [],
        'attempt_owner_busy': None, 'attempt_snapshot_stable': False}
    for name, key, limit in [('result.json', 'result', 300000), ('provision.json', 'provision', 20000),
                              ('grant.json', 'grant', 16384)]:
        if (root / name).exists():
            raw = read(root / name, limit)
            value = json.loads(raw)
            need(value['unit'] == unit, 'wrong_receipt_unit')
            result[key] = value
            result['files'][name] = hashlib.sha256(raw).hexdigest()
    if (root / 'object_manifest.json').exists():
        raw = read(root / 'object_manifest.json', 20000)
        obj = json.loads(raw)
        result['object_manifest'] = obj
        result['files']['object_manifest.json'] = hashlib.sha256(raw).hexdigest()
        need(re.fullmatch('[0-9a-f]{64}', obj['object_id']) and type(obj['chunks']) is list
             and 1 <= len(obj['chunks']) <= 64, 'invalid_object_evidence')
        hasher = hashlib.sha256()
        verified, missing, size = [], [], 0
        storage = Path('/var/lib/meshticulum-bench/objects') / obj['object_id']
        for index, descriptor in enumerate(obj['chunks']):
            need(descriptor['chunk_index'] == index and descriptor['object_id'] == obj['object_id']
                 and type(descriptor['bytes']) is int and 1 <= descriptor['bytes'] <= 16384,
                 'invalid_object_chunk_order')
            meta = storage / (str(index) + '.json')
            if not meta.exists():
                missing.append(index)
                continue
            need(json.loads(read(meta, 4096)) == descriptor, 'stored_chunk_binding_changed')
            data = read(storage / (str(index) + '.bin'), 16384)
            need(len(data) == descriptor['bytes'] and hashlib.sha256(data).hexdigest() == descriptor['sha256'],
                 'stored_chunk_digest_changed')
            verified.append(index); size += len(data); hasher.update(data)
        complete = not missing and size == obj['bytes'] and hasher.hexdigest() == obj['object_id']
        need(missing or complete, 'whole_object_digest_changed')
        result['object_receipt'] = {'object_id': obj['object_id'], 'verified_chunks': verified,
            'missing_chunks': missing, 'verified_bytes': size, 'whole_object_verified': complete}
    owner = root / 'attempts.sqlite.owner'
    if owner.exists():
        read(owner, 1024)
        fd = os.open(owner, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result['attempt_owner_busy'] = False
            except BlockingIOError:
                result['attempt_owner_busy'] = True
            database = root / 'attempts.sqlite'
            # A finished owner plus the consumed grant makes this a stable
            # snapshot. A busy owner is reported explicitly, never as stopped.
            before = read(database, 2000000)
            connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)
            try:
                rows = connection.execute('SELECT request,hex(packet_sha),bytes,estimated_us,state,evidence '
                    'FROM attempts ORDER BY request LIMIT 257').fetchall()
                need(len(rows) <= 256, 'attempt_export_limit')
                for request, digest, size, estimated, outcome, evidence in rows:
                    result['attempts'].append({'request': request, 'packet_sha256': digest.lower(),
                        'bytes': size, 'reserved_estimated_us': estimated, 'outcome': outcome,
                        'driver_evidence': json.loads(evidence) if evidence and evidence.startswith('{') else evidence})
                result['owner'] = connection.execute('SELECT max_attempts,max_airtime,fenced FROM owner WHERE id=1').fetchone()
            finally: connection.close()
            after = read(database, 2000000)
            result['attempt_snapshot_stable'] = result['attempt_owner_busy'] is False and before == after
            if result['attempt_snapshot_stable']:
                result['files']['attempts.sqlite'] = hashlib.sha256(after).hexdigest()
        finally: os.close(fd)
    print(json.dumps(result))


if __name__ == '__main__': main()
