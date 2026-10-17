"""Latched Linux open detection for a cooperative owner of a fixed device.

This replaces repeated best-effort procfs scans, not kernel device exclusivity.
The owner holds its original driver locks and descriptors and must not fork or
pass those descriptors. Host root remains trusted: a privileged process can
bypass locks, transfer a descriptor, or make a new device alias outside /dev.
All existing /dev aliases are watched. Changes within /dev or to its retained
directories invalidate the watch instead of authorizing a replacement node.
"""
import ctypes
import errno
import fcntl
import os
from pathlib import Path
import platform
import re
import struct
import weakref

from .linux_hat import HardwareError, _existing_openers


# inotify events are native-endian Linux ABI, independent of the radio ABI.
_IN_OPEN = 0x00000020
_IN_ATTRIB = 0x00000004
_IN_MOVED_FROM = 0x00000040
_IN_MOVED_TO = 0x00000080
_IN_CREATE = 0x00000100
_IN_DELETE = 0x00000200
_IN_DELETE_SELF = 0x00000400
_IN_MOVE_SELF = 0x00000800
_IN_UNMOUNT = 0x00002000
_IN_Q_OVERFLOW = 0x00004000
_IN_IGNORED = 0x00008000
_IN_ONLYDIR = 0x01000000
_IN_DONT_FOLLOW = 0x02000000
_IN_MASK_ADD = 0x20000000
_IN_ISDIR = 0x40000000
_INVALIDATION = (_IN_ATTRIB | _IN_DELETE_SELF | _IN_MOVE_SELF | _IN_UNMOUNT | _IN_IGNORED)
_DIRECTORY_EVENTS = _INVALIDATION | _IN_MOVED_FROM | _IN_MOVED_TO | _IN_CREATE | _IN_DELETE
_DEVICE_EVENTS = _INVALIDATION | _IN_OPEN
_UNSUPPORTED = {errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}
_ACTIVE = weakref.WeakSet()
_WATCH_LINE = re.compile(rb'inotify wd:([0-9a-f]+) ino:([0-9a-f]+) sdev:([0-9a-f]+) mask:([0-9a-f]+) ignored_mask:([0-9a-f]+)(?: .*)?')


def _forked():
    # Both parent and child invalidate: ownership forbids inheriting an open
    # radio even if the child promises to close its descriptor later.
    for watch in tuple(_ACTIVE):
        watch._fail('owner_forked')


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(before=_forked)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_rdev, info.st_mode, info.st_uid


def _entries(path):
    # A directory inventory also catches changes on filesystems that provide
    # incomplete inotify events (notably devpts), without scanning /proc fds.
    # A mount over a directory is caught separately by fresh stat of that path.
    with os.scandir(path) as entries:
        return tuple(sorted((entry.name, entry.inode(),
                             entry.is_dir(follow_symlinks=False), entry.is_symlink())
                            for entry in entries))


class OpenCollisionWatch:
    """Fresh event/inode checks; never a cached RF or grant authorization.

    A watcher is established before the initial rdev-wide opener scan, so an
    ordinary opener cannot hide by opening and closing between later probes.
    Unsupported platforms/kernels retain the original scan on every probe.
    Unexpected events, read errors, queue overflow and node changes latch a
    refusal until this object is closed; they never switch to a clean cache.
    """
    def __init__(self, device_stat, *, device_root='/dev', opener_scan=None):
        self.fd, self.watch_stat, self.closed, self.failed = None, None, False, None
        self.pid = os.getpid()
        self.device_stat = device_stat
        self.root = Path(device_root)
        self.scan = opener_scan or _existing_openers
        self.paths, self.directories, self.watches, self.aliases = {}, {}, set(), set()
        self.watch_lines, self.watch_inodes, self.anchor_wd = None, {}, None
        try:
            if platform.system() == 'Linux':
                self._start()
            # This initial full scan includes old aliases outside /dev too.
            if self.scan(device_stat):
                raise HardwareError('radio_already_open')
            if not self.current():
                raise HardwareError(self.failed or 'device_watch_not_current')
            _ACTIVE.add(self)
        except BaseException:
            self.close()
            raise

    def _start(self):
        library = ctypes.CDLL(None, use_errno=True)
        try:
            initialize, add = library.inotify_init1, library.inotify_add_watch
        except AttributeError:
            return  # libc/kernel without this API keeps fresh procfs scans.
        initialize.argtypes, initialize.restype = [ctypes.c_int], ctypes.c_int
        add.argtypes, add.restype = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32], ctypes.c_int
        fd = initialize(os.O_NONBLOCK | os.O_CLOEXEC)
        if fd < 0:
            code = ctypes.get_errno()
            if code in _UNSUPPORTED:
                return
            raise OSError(code, os.strerror(code))
        self.fd, self.watch_stat = fd, os.fstat(fd)
        try:
            self._inventory(self.root, add)
            if not self.aliases:
                raise HardwareError('device_watch_node_not_found')
            self.watch_lines = self._fdinfo_watch_lines()
        except OSError as error:
            if error.errno not in _UNSUPPORTED:
                raise
            os.close(self.fd)
            self.fd, self.watch_stat = None, None
            self.paths.clear()
            self.directories.clear()
            self.watches.clear()
            self.aliases.clear()
            self.watch_inodes.clear()
            self.watch_lines = None

    def _watch(self, path, info, mask, add):
        wd = add(self.fd, os.fsencode(path), mask | _IN_MASK_ADD)
        if wd < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(path))
        # add_watch follows a character-device symlink; verify its target did
        # not change between enumeration and registration.
        if _identity(path.stat()) != _identity(info):
            raise HardwareError('device_watch_path_changed')
        self.watches.add(wd)
        self.watch_inodes[wd] = info.st_ino
        if path == self.root:
            self.anchor_wd = wd
        self.paths[path] = _identity(info)

    def _inventory(self, path, add):
        info = path.stat()
        self._watch(path, info, _DIRECTORY_EVENTS | _IN_ONLYDIR | _IN_DONT_FOLLOW, add)
        snapshot = _entries(path)
        self.directories[path] = snapshot
        for name, unused_inode, directory, symlink in snapshot:
            child = path / name
            if directory:
                self._inventory(child, add)
            else:
                try:
                    info = child.stat()
                except FileNotFoundError:
                    # Existing dangling symlinks are common under /dev. A
                    # replacement/create is caught by the parent watch.
                    continue
                if info.st_rdev == self.device_stat.st_rdev and (
                        info.st_mode & 0o170000) == (self.device_stat.st_mode & 0o170000):
                    self._watch(child, info, _DEVICE_EVENTS, add)
                    self.aliases.add(child)

    def _fdinfo_watch_lines(self, *, allow_removed=False):
        # Anonymous inotify fds can share the same fstat inode and flags. Bind
        # the actual watch set too, so an empty/recycled inotify fd cannot pass.
        # This is one bounded procfs file, not a process/descriptor-table scan.
        fd = os.open(f'/proc/self/fdinfo/{self.fd}', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            chunks, size = [], 0
            while True:
                raw = os.read(fd, 65536)
                if not raw:
                    break
                size += len(raw)
                if size > 2097152:
                    raise HardwareError('device_watch_fdinfo_too_large')
                chunks.append(raw)
        finally:
            os.close(fd)
        lines, watches = [], {}
        for line in b''.join(chunks).splitlines():
            if not line.startswith(b'inotify'):
                continue
            match = _WATCH_LINE.fullmatch(line)
            if match is None:
                raise HardwareError('device_watch_fdinfo_malformed')
            wd, inode = int(match[1], 16), int(match[2], 16)
            if wd in watches or self.watch_inodes.get(wd) != inode:
                raise HardwareError('device_watch_fdinfo_rebound')
            watches[wd] = inode
            lines.append(line)
        if (not allow_removed and set(watches) != self.watches) or (
                allow_removed and self.anchor_wd not in watches):
            raise HardwareError('device_watch_fdinfo_rebound')
        return tuple(sorted(lines))

    def _descriptor_current(self, *, allow_removed=False):
        if (_identity(os.fstat(self.fd)) != _identity(self.watch_stat)
                or not fcntl.fcntl(self.fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
                or not fcntl.fcntl(self.fd, fcntl.F_GETFL) & os.O_NONBLOCK):
            return False
        lines = self._fdinfo_watch_lines(allow_removed=allow_removed)
        if allow_removed:
            # Teardown may follow a real node deletion. Its removed watch is
            # expected then, but the original directory anchor must remain and
            # every surviving inode/mask line must belong to the retained set.
            return bool(lines) and set(lines) <= set(self.watch_lines)
        return lines == self.watch_lines

    def _fail(self, reason):
        if self.failed is None:
            self.failed = reason
        return False

    def _events_current(self):
        try:
            raw = os.read(self.fd, 65536)
        except BlockingIOError:
            return True
        if not raw:
            return self._fail('device_watch_eof')
        offset = 0
        while offset < len(raw):
            if len(raw) - offset < 16:
                return self._fail('device_watch_malformed')
            wd, mask, cookie, size = struct.unpack_from('=iIII', raw, offset)
            offset += 16
            if size > len(raw) - offset or (size and (size % 4 or raw[offset + size - 1] != 0)):
                return self._fail('device_watch_malformed')
            offset += size
            if mask & _IN_Q_OVERFLOW:
                return self._fail('device_watch_overflow')
            if wd not in self.watches or not mask or mask & ~(
                    _DIRECTORY_EVENTS | _DEVICE_EVENTS | _IN_ISDIR):
                return self._fail('device_watch_unbound_event')
            # Every requested event invalidates ownership; no actor-PID guess
            # or coalesced-event count is used to excuse a foreign open.
            return self._fail('device_watch_event')
        return self._fail('device_watch_malformed')

    def current(self):
        if self.closed or self.failed is not None:
            return False
        try:
            if os.getpid() != self.pid:
                return self._fail('device_watch_process_changed')
            if self.fd is None:
                return not self.scan(self.device_stat) or self._fail('radio_open_collision')
            if not self._descriptor_current():
                return self._fail('device_watch_fd_changed')
            if not self._events_current():
                return False
            for path, original in self.paths.items():
                if _identity(path.stat()) != original:
                    return self._fail('device_watch_path_changed')
            for path, original in self.directories.items():
                if _entries(path) != original:
                    return self._fail('device_watch_directory_changed')
            return self.failed is None and self._events_current() and self.failed is None
        except (OSError, ValueError, HardwareError):
            return self._fail('device_watch_probe_failed')

    def close(self):
        self.closed = True
        _ACTIVE.discard(self)
        fd = self.fd
        if fd is None:
            return
        try:
            # A caller may already have closed or recycled this fd integer.
            # Do not close a known replacement; containment is a latched
            # refusal and relinquishing this stale reference.
            owned = (self.watch_lines is None or self._descriptor_current(allow_removed=True))
        except (OSError, ValueError, HardwareError):
            owned = False
        self.fd = None
        if owned:
            try:
                os.close(fd)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
