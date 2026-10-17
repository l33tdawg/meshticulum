"""Fresh procfs ownership scans; physical radios are never opened here."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import platform
import pty
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from meshticulum.bench import linux_hat


def fake_proc(monkeypatch, tmp_path):
    """Use real scandir and character-device stat through synthetic fd links."""
    root = tmp_path / 'proc'
    root.mkdir()
    real_scan = os.scandir

    def scan(path):
        return real_scan(root if path == '/proc' else path)

    monkeypatch.setattr(linux_hat.os, 'scandir', scan)
    return root


def descriptor(root, pid, fd, node):
    folder = root / str(pid) / 'fd'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / str(fd)
    path.symlink_to(node)
    return path


def test_scan_follows_device_aliases_excludes_self_and_returns_each_process_once(tmp_path, monkeypatch):
    root = fake_proc(monkeypatch, tmp_path)
    own = os.getpid()
    pid = own + 100000
    descriptor(root, own, 3, os.devnull)
    descriptor(root, pid, 3, os.devnull)
    descriptor(root, pid, 4, os.devnull)
    descriptor(root, pid + 1, 5, os.devnull)
    descriptor(root, 'not-a-pid', 3, os.devnull)
    # Procfs fd links are followed to their character-device rdev, not compared
    # by path, fd number, inode, or DirEntry.is_file (which would be false).
    assert sorted(linux_hat._existing_openers(os.stat(os.devnull))) == [pid, pid + 1]


def test_every_call_observes_new_openers_and_closed_descriptors(tmp_path, monkeypatch):
    root = fake_proc(monkeypatch, tmp_path)
    info = os.stat(os.devnull)
    assert linux_hat._existing_openers(info) == []
    fd = descriptor(root, os.getpid() + 100000, 7, os.devnull)
    assert linux_hat._existing_openers(info) == [os.getpid() + 100000]
    fd.unlink()
    assert linux_hat._existing_openers(info) == []


def test_regular_files_with_same_zero_rdev_are_not_device_collisions(tmp_path, monkeypatch):
    root = fake_proc(monkeypatch, tmp_path)
    node = tmp_path / 'regular'
    node.write_bytes(b'no radio')
    descriptor(root, os.getpid() + 100000, 9, node)
    assert linux_hat._existing_openers(SimpleNamespace(st_rdev=0)) == []


def test_vanished_process_and_descriptor_are_safe_races(tmp_path, monkeypatch):
    root = fake_proc(monkeypatch, tmp_path)
    # A process with no fd directory can vanish between the outer and inner
    # scandir. A dangling fd link models a descriptor closed before stat.
    (root / str(os.getpid() + 100000)).mkdir()
    descriptor(root, os.getpid() + 100001, 9, tmp_path / 'already-closed')
    assert linux_hat._existing_openers(os.stat(os.devnull)) == []


@pytest.mark.parametrize('at_stat', [False, True])
def test_unreadable_process_or_descriptor_fails_closed(tmp_path, monkeypatch, at_stat):
    root = fake_proc(monkeypatch, tmp_path)
    pid = os.getpid() + 100000
    folder = root / str(pid) / 'fd'
    folder.mkdir(parents=True)
    real_scan = os.scandir

    class UnreadableDescriptor:
        def stat(self, *, follow_symlinks):
            assert follow_symlinks
            raise PermissionError(errno.EACCES, 'source permission control')

    @contextmanager
    def scan(path):
        if str(path) == str(folder):
            if not at_stat:
                raise PermissionError(errno.EACCES, 'source permission control')
            yield iter([UnreadableDescriptor()])
        else:
            with real_scan(path) as entries:
                yield entries

    monkeypatch.setattr(linux_hat.os, 'scandir', scan)
    with pytest.raises(linux_hat.HardwareError, match='cannot_inspect_radio_ownership'):
        linux_hat._existing_openers(os.stat(os.devnull))


@contextmanager
def sibling_opener(path):
    child = subprocess.Popen([sys.executable, '-c',
        "import os,sys; fd=os.open(sys.argv[1], os.O_RDWR|os.O_NOCTTY); "
        "print('open', flush=True); sys.stdin.buffer.read(1); os.close(fd)", str(path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert child.stdout.readline() == b'open\n'
        yield child.pid
    finally:
        child.communicate(b'x', timeout=5)
        assert child.returncode == 0


@pytest.mark.skipif(platform.system() != 'Linux' or os.geteuid() != 0,
                    reason='actual procfs full-owner check requires Linux root')
def test_linux_live_sibling_pty_open_and_close_is_seen_without_cached_approval():
    # A fresh private PTY is an OS character-device fixture, never RF hardware.
    master, slave = pty.openpty()
    try:
        info = os.fstat(slave)
        assert linux_hat._existing_openers(info) == []
        with sibling_opener(os.ttyname(slave)) as pid:
            assert pid in linux_hat._existing_openers(info)
        assert linux_hat._existing_openers(info) == []
    finally:
        os.close(slave)
        os.close(master)


@pytest.mark.skipif(platform.system() != 'Linux' or os.geteuid() != 0,
                    reason='actual procfs and character-node alias require Linux root')
def test_linux_live_sibling_alternate_device_inode_is_still_seen(tmp_path):
    alias = tmp_path / 'same-rdev-different-inode'
    try:
        info = os.stat(os.devnull)
        try:
            os.mknod(alias, stat.S_IFCHR | 0o600, info.st_rdev)
        except PermissionError:
            pytest.skip('Linux root fixture lacks CAP_MKNOD')
        alias_info = alias.stat()
        assert alias_info.st_rdev == info.st_rdev
        assert (alias_info.st_dev, alias_info.st_ino) != (info.st_dev, info.st_ino)
        with sibling_opener(alias) as pid:
            assert pid in linux_hat._existing_openers(info)
        assert pid not in linux_hat._existing_openers(info)
    finally:
        if alias.exists():
            alias.unlink()
