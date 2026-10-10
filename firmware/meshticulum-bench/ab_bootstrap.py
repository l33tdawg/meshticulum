"""Inline root bootstrap: verify the complete archive before executing its code.

The local operator helper passes this source via `sudo python3 -I -B -c`, with
the frozen archive SHA-256 in the same command. A user-writable installer is
never executed directly as root. No SSH or sudo policy is changed.
"""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def files_from_archive(data, expected):
    require(hashlib.sha256(data).hexdigest() == expected, 'archive_hash_mismatch')
    result = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            require(item.isfile() and not path.is_absolute() and '..' not in path.parts
                    and str(path) == item.name and path.parts and len(item.name) < 200,
                    'invalid_archive_member')
            require(item.name not in result and len(result) < 100
                    and 0 <= item.size <= 8 * 1024 * 1024, 'archive_member_limit')
            total += item.size
            require(total <= 20 * 1024 * 1024, 'archive_total_limit')
            value = archive.extractfile(item).read(item.size + 1)
            require(len(value) == item.size, 'truncated_archive_member')
            result[item.name] = value
    require({'manifest.json', 'install_ab.py', 'firmware.bin', 'rollback-r7.bin'} <= result.keys(),
            'incomplete_archive')
    return result


def read_regular(path, *, owner=None, maximum=20 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum
                and (owner is None or info.st_uid == owner), 'unsafe_archive_or_bundle_file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(maximum + 1)
    finally:
        os.close(fd)


def private_root(path):
    path.mkdir(mode=0o700, exist_ok=True)
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'unsafe_root_bundle_directory')
    require(stat.S_IMODE(path.stat().st_mode) == 0o700, 'bundle_directory_not_private')


def main():
    require(os.geteuid() == 0 and sys.flags.isolated, 'requires_isolated_root_python')
    require(len(sys.argv) == 5, 'expected_action_unit_archive_hash')
    action, unit, source, digest = sys.argv[1:]
    require(action in ('flash', 'rollback', 'status') and unit in ('unit-a', 'unit-b'), 'invalid_action')
    require(len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'invalid_archive_pin')
    os.umask(0o077)
    data = read_regular(source, owner=int(os.environ['SUDO_UID']) if 'SUDO_UID' in os.environ else None)
    members = files_from_archive(data, digest)
    base = Path('/opt/meshticulum-bench')
    private_root(base)
    destination = base / ('ab-flash-' + digest[:16])
    if not destination.exists():
        staging = Path(tempfile.mkdtemp(prefix='.ab-stage-', dir=base))
        for name, value in members.items():
            path = staging / name
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
        os.rename(staging, destination)
        directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    private_root(destination)
    for name, expected in members.items():
        path = destination / name
        private_root(path.parent)
        require(read_regular(path, owner=0) == expected, 'existing_root_bundle_changed')
    executable = '/root/esp-venv-r3/bin/python'
    require(Path(executable).is_file(), 'missing_retained_esptool_python')
    os.chdir(destination)
    os.execve(executable, [executable, '-I', '-B', str(destination / 'install_ab.py'),
                           action, '--unit', unit],
              {'HOME': '/root', 'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})


if __name__ == '__main__':
    main()
