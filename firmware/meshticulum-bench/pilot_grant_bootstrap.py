# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Prepare one hash-pinned finite public pilot child. Opens no socket or radio."""
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
         'expected_isolated_root_unit_package_hash_runtime_manifest')
    unit, source, digest, runtime_digest, manifest_digest = sys.argv[1:]
    for value in (digest, runtime_digest, manifest_digest):
        need(len(value) == 64 and all(c in '0123456789abcdef' for c in value), 'invalid_pin')
    need(unit in ('unit-a', 'unit-b', 'unit-c'), 'invalid_unit')
    os.umask(0o077)
    raw = read(Path(source), 100000)
    need(hashlib.sha256(raw).hexdigest() == digest, 'pilot_package_hash_mismatch')
    package = json.loads(raw)
    bundle = Path('/opt/meshticulum-bench') / ('runtime-' + runtime_digest[:16])
    manifest_raw = read(bundle / 'manifest.json', 20000, root=True)
    need(hashlib.sha256(manifest_raw).hexdigest() == manifest_digest, 'runtime_manifest_changed')
    manifest = json.loads(manifest_raw)
    need(manifest.get('schema') == 'meshticulum.bench-runtime-bundle.v1'
         and manifest.get('private_services_enabled') is False
         and manifest.get('public_ip_service_formats') == ['MR1.request', 'MR1.response'],
         'public_pilot_not_in_bundle')
    for name, expected in manifest['files'].items():
        part = Path(name)
        need(not part.is_absolute() and '..' not in part.parts, 'invalid_runtime_path')
        need(hashlib.sha256(read(bundle / part, 500000, root=True)).hexdigest() == expected,
             'runtime_source_changed')
    sys.path.insert(0, str(bundle))
    from meshticulum.ip.node import _facts, clock
    facts = _facts(unit)
    if package.get('schema') == 'meshticulum.pilot-public-seed.v1':
        from meshticulum.bench.pilot_seed import install
        result = install(package, state='/var/lib/meshticulum-bench', facts=facts)
    else:
        from meshticulum.bench.pilot_provision import provision
        result = provision(package, state='/var/lib/meshticulum-bench', facts=facts, clock=clock)
    need(_facts(unit) == facts, 'native_pilot_lifecycle_changed')
    result['package_sha256'] = digest
    result['bundle_manifest_sha256'] = manifest_digest
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__': main()
