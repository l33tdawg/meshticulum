"""Inline root bootstrap for a source-only runtime archive. Installs disarmed."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile


def require(condition, reason):
    if not condition: raise RuntimeError(reason)


def members(data, digest):
    require(hashlib.sha256(data).hexdigest() == digest, 'runtime_archive_hash_mismatch')
    result = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            require(entry.isfile() and not path.is_absolute() and '..' not in path.parts
                    and str(path) == entry.name and path.parts and len(entry.name) < 200,
                    'invalid_runtime_archive_member')
            require(entry.name not in result and len(result) < 80 and 0 <= entry.size <= 500000,
                    'runtime_archive_member_limit')
            total += entry.size
            require(total <= 2000000, 'runtime_archive_total_limit')
            result[entry.name] = archive.extractfile(entry).read(entry.size + 1)
            require(len(result[entry.name]) == entry.size, 'truncated_runtime_member')
    require({'manifest.json', 'bench_node.py'} <= result.keys(), 'incomplete_runtime_archive')
    manifest = json.loads(result['manifest.json'])
    require(manifest['schema'] == 'meshticulum.bench-runtime-bundle.v1'
            and set(manifest['files']) == set(result) - {'manifest.json'}, 'wrong_runtime_manifest')
    for name, expected in manifest['files'].items():
        require(hashlib.sha256(result[name]).hexdigest() == expected, 'runtime_member_hash_mismatch')
    return result


def private_root(path, *, private=True):
    path.mkdir(mode=0o700, exist_ok=True)
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'unsafe_runtime_parent')
    if private:
        require(stat.S_IMODE(path.stat().st_mode) == 0o700, 'runtime_directory_not_private')


def read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
                'unsafe_runtime_source')
        with os.fdopen(fd, 'rb', closefd=False) as source: return source.read(limit + 1)
    finally: os.close(fd)


def main():
    require(os.geteuid() == 0 and sys.flags.isolated and len(sys.argv) == 4,
            'requires_isolated_root_unit_archive_hash')
    unit, archive, digest = sys.argv[1:]
    require(unit in ('unit-a', 'unit-b', 'unit-c') and len(digest) == 64
            and all(c in '0123456789abcdef' for c in digest), 'invalid_runtime_target')
    os.umask(0o077)
    files = members(read_regular(archive, 2000000), digest)
    root = Path('/opt/meshticulum-bench'); private_root(root, private=False)
    destination = root / ('runtime-' + digest[:16])
    if not destination.exists():
        staging = Path(tempfile.mkdtemp(prefix='.runtime-', dir=root))
        for name, data in files.items():
            path = staging / name
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as out:
                out.write(data); out.flush(); os.fsync(out.fileno())
        os.rename(staging, destination)
    private_root(destination)
    for name, data in files.items():
        path = destination / name
        private_root(path.parent)
        require(path.lstat().st_uid == 0 and not path.lstat().st_mode & 0o022
                and read_regular(path, 500000) == data, 'runtime_installed_bytes_changed')
    state = Path('/var/lib/meshticulum-bench'); private_root(state, private=False)
    for name in ('runs', 'objects'): private_root(state / name)
    executable = '/usr/bin/python3' if unit == 'unit-c' else '/root/esp-venv-r3/bin/python'
    os.chdir(destination)
    os.execve(executable, [executable, '-I', '-B', str(destination / 'bench_node.py'), 'inspect', unit],
              {'HOME': '/root', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})


if __name__ == '__main__': main()
