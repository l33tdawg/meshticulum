#!/usr/bin/env python3
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Hash-verified root launcher for explicitly provisioned, finite bench runs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


def trusted(path, limit=2000000):
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not (stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022):
            raise RuntimeError('untrusted_bundle_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= limit):
            raise RuntimeError('untrusted_bundle_file')
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'run', 'service-run', 'ip-service-run', 'pilot-verify', 'revoke', 'ip-revoke'))
    parser.add_argument('unit', choices=('unit-a', 'unit-b', 'unit-c'))
    parser.add_argument('--run-id')
    parser.add_argument('--campaign-id')
    parser.add_argument('--request-run-id')
    args = parser.parse_args()
    if os.geteuid() != 0: raise RuntimeError('requires_root')
    bundle = Path(__file__).resolve().parent
    manifest_bytes = trusted(bundle / 'manifest.json')
    manifest = json.loads(manifest_bytes)
    if manifest['schema'] != 'meshticulum.bench-runtime-bundle.v1':
        raise RuntimeError('wrong_runtime_bundle')
    for name, digest in manifest['files'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise RuntimeError('unsafe_runtime_member')
        if hashlib.sha256(trusted(bundle / path)).hexdigest() != digest:
            raise RuntimeError('runtime_source_drift')
    sys.path.insert(0, str(bundle))
    from meshticulum.bench import node, authority
    if args.action == 'inspect':
        result = node.inspect(args.unit)
    else:
        if args.run_id is None or not re.fullmatch(r'[0-9a-f]{32}', args.run_id):
            raise RuntimeError('invalid_run_id')
        root = Path('/var/lib/meshticulum-bench')
        directory = root / 'runs' / args.run_id
        if args.action in ('revoke', 'ip-revoke'):
            node.roles(args.unit)
            if args.action == 'ip-revoke':
                from meshticulum.ip.authority import revoke
                revoke(directory)
            else:
                authority.revoke(directory)
            result = {'revoked': True, 'run_id': args.run_id}
        else:
            if args.action == 'pilot-verify':
                from meshticulum.bench.pilot_resource_node import verify
                result = verify(args.unit, args.campaign_id, args.request_run_id, args.run_id)
            elif args.action == 'ip-service-run':
                if manifest.get('public_ip_service_formats') != ['MR1.request', 'MR1.response']:
                    raise RuntimeError('public_ip_runner_not_in_bundle')
                from meshticulum.ip.node import run_public_service
                result = run_public_service(args.unit, directory)
            elif args.action == 'service-run':
                if manifest.get('public_service_formats') != ['MR1.request', 'MR1.response']:
                    raise RuntimeError('public_service_runner_not_in_bundle')
                result = node.run_public_service(args.unit, directory)
            else:
                result = node.run(args.unit, directory, root / 'objects')
    result['runtime_bundle'] = str(bundle)
    result['bundle_manifest_sha256'] = hashlib.sha256(manifest_bytes).hexdigest()
    print(json.dumps(result))
    if args.action in ('run', 'service-run', 'ip-service-run') and not result['complete']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
