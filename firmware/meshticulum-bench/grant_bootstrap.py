# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Install a hash-pinned, short-lived bench grant. Does not open a radio."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time


def need(ok, message):
    if not ok: raise RuntimeError(message)


def read(path, maximum, *, root=False):
    if root:
        for parent in (path.parent, *path.parent.parents):
            info = parent.lstat()
            need(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                 'unsafe_grant_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum
             and (not root or info.st_uid == 0 and not info.st_mode & 0o022), 'unsafe_grant_file')
        with os.fdopen(fd, 'rb', closefd=False) as inp: return inp.read(maximum + 1)
    finally: os.close(fd)


def main():
    need(os.geteuid() == 0 and sys.flags.isolated and len(sys.argv) == 6,
         'expected_root_unit_package_hash_runtime_manifest')
    unit, source, digest, runtime_digest, manifest_digest = sys.argv[1:]
    for value in (digest, runtime_digest, manifest_digest):
        need(len(value) == 64 and all(c in '0123456789abcdef' for c in value), 'invalid_pin')
    need(unit in ('unit-a', 'unit-b', 'unit-c'), 'invalid_unit')
    os.umask(0o077)
    raw = read(Path(source), 40000)
    need(hashlib.sha256(raw).hexdigest() == digest, 'grant_package_hash_mismatch')
    package = json.loads(raw)
    need(type(package) is dict and set(package) == {'grant', 'secret', 'payload', 'reuse_verified_chunk', 'object_manifest'},
         'invalid_grant_package')
    bundle = Path('/opt/meshticulum-bench') / ('runtime-' + runtime_digest[:16])
    manifest_raw = read(bundle / 'manifest.json', 20000, root=True)
    need(hashlib.sha256(manifest_raw).hexdigest() == manifest_digest, 'runtime_manifest_changed')
    manifest = json.loads(manifest_raw)
    for name, expected in manifest['files'].items():
        part = Path(name)
        need(not part.is_absolute() and '..' not in part.parts, 'invalid_runtime_path')
        need(hashlib.sha256(read(bundle / part, 500000, root=True)).hexdigest() == expected,
             'runtime_source_changed')
    sys.path.insert(0, str(bundle))
    from meshticulum.bench.node import roles
    from meshticulum.bench.authority import canonical, _private_info
    from meshticulum.bench.objects import VerifiedChunks
    services = roles(unit)
    grant = package['grant']
    need(grant.get('schema') in ('meshticulum.bench-run.v1', 'meshticulum.service-run.v1'),
         'unsupported_grant_schema')
    service = grant['schema'] == 'meshticulum.service-run.v1'
    if service:
        need(manifest.get('public_service_formats') == ['MR1.request', 'MR1.response']
             and manifest.get('private_services_enabled') is False
             and type(grant.get('service')) is dict
             and set(grant['service']) == {'kind', 'format', 'application_expires_epoch_us'}
             and grant['service']['kind'] == 'knowledge'
             and grant['service']['format'] in manifest['public_service_formats']
             and package['reuse_verified_chunk'] is False and package['object_manifest'] is None,
             'service_requires_public_only_package')
        deadline = grant['service']['application_expires_epoch_us']
        need(type(deadline) is int and grant['issued_epoch_us'] < deadline <= grant['expires_epoch_us'],
             'invalid_service_application_deadline')
    need(grant['unit'] == unit and grant['host_boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
         'grant_wrong_host')
    now = time.time_ns() // 1000
    need(grant['issued_epoch_us'] <= now < grant['expires_epoch_us']
         and grant['expires_epoch_us'] - grant['issued_epoch_us'] <= 240000000, 'grant_not_current')
    if service:
        need(now < grant['service']['application_expires_epoch_us'], 'service_application_expired')
    need(grant['country'] == 'MY' and grant['antenna_confirmed'] is True, 'rf_confirmation_missing')
    identifier = grant['session_id']
    need(type(identifier) is str and len(identifier) == 32
         and bytes.fromhex(identifier).hex() == identifier and any(bytes.fromhex(identifier)), 'invalid_run_id')
    secret = base64.b64decode(package['secret'], validate=True)
    need(len(secret) == 32, 'invalid_bench_key')
    reuse = package['reuse_verified_chunk']
    need(type(reuse) is bool, 'invalid_reuse_mode')
    payload = base64.b64decode(package['payload'], validate=True) if package['payload'] is not None else None
    state = Path('/var/lib/meshticulum-bench')
    source_basis = 'none_receiver'
    if grant['sender_unit'] == unit:
        if reuse:
            need(payload is None, 'cannot_send_ip_copy_as_relay')
            payload = VerifiedChunks(state / 'objects').get(grant['object'])
            source_basis = 'previous_verified_local_radio_chunk'
        else:
            source_basis = 'operator_public_resource_block' if service else 'operator_synthetic_random_bytes'
        need(payload is not None and len(payload) == grant['object']['bytes']
             and hashlib.sha256(payload).hexdigest() == grant['object']['sha256'], 'wrong_sender_payload')
        if service:
            from meshticulum.bench.services import _shape
            _shape(payload, grant['service']['format'])
    else:
        need(payload is None and reuse is False, 'receiver_must_not_have_ip_payload')
    files = {'grant.json': canonical(grant), 'secret.bin': secret, 'active': b'active\n'}
    object_manifest = package['object_manifest']
    if object_manifest is not None:
        need(type(object_manifest) is dict and set(object_manifest) == {'schema', 'object_id', 'bytes', 'chunks'}
             and object_manifest['schema'] == 'meshticulum.synthetic-object.v1'
             and object_manifest['object_id'] == grant['object']['object_id']
             and type(object_manifest['chunks']) is list and 1 <= len(object_manifest['chunks']) <= 64,
             'invalid_object_manifest')
        for i, descriptor in enumerate(object_manifest['chunks']):
            need(type(descriptor) is dict and set(descriptor) == {'object_id', 'chunk_index', 'sha256', 'bytes'}
                 and descriptor['object_id'] == object_manifest['object_id']
                 and type(descriptor['chunk_index']) is int and descriptor['chunk_index'] == i
                 and type(descriptor['bytes']) is int and 1 <= descriptor['bytes'] <= 16384,
                 'invalid_object_chunk')
            need(type(descriptor['sha256']) is str and len(descriptor['sha256']) == 64
                 and bytes.fromhex(descriptor['sha256']).hex() == descriptor['sha256'], 'invalid_chunk_digest')
        index = grant['object']['chunk_index']
        need(type(index) is int and 0 <= index < len(object_manifest['chunks'])
             and object_manifest['chunks'][index] == grant['object']
             and type(object_manifest['bytes']) is int
             and sum(d['bytes'] for d in object_manifest['chunks']) == object_manifest['bytes'],
             'object_manifest_binding_mismatch')
        files['object_manifest.json'] = canonical(object_manifest)
    if payload is not None: files['sender.bin'] = payload
    runs = state / 'runs'; _private_info(runs.lstat(), directory=True)
    destination = runs / identifier
    if not destination.exists():
        staging = Path(tempfile.mkdtemp(prefix='.grant-', dir=runs))
        for name, data in files.items():
            fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as out: out.write(data); out.flush(); os.fsync(out.fileno())
        os.rename(staging, destination)
        for parent in (destination, runs):
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
    _private_info(destination.lstat(), directory=True)
    need(not (destination / 'started').exists(), 'grant_already_consumed')
    for name, data in files.items():
        path = destination / name; _private_info(path.lstat())
        need(read(path, 20000, root=True) == data, 'existing_grant_differs')
    receipt = {'schema': 'meshticulum.provisioned-grant.v1', 'unit': unit, 'run_id': identifier,
        'package_sha256': digest, 'runtime_bundle': str(bundle), 'object': grant['object'],
        'source_basis': source_basis, 'receiver_payload_installed': False,
        'expires_epoch_us': grant['expires_epoch_us'], 'radio_opened': False, 'services': services}
    receipt_path = destination / 'provision.json'
    if not receipt_path.exists():
        fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as out: out.write(canonical(receipt)); out.flush(); os.fsync(out.fileno())
    print(json.dumps(receipt))


if __name__ == '__main__': main()
