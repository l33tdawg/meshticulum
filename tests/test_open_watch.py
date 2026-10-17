"""Linux collision-watch controls using temporary ordinary files, never RF.

Also runnable without pytest: PYTHONPATH=src python tests/test_open_watch.py
"""
import ctypes
import errno
import os
from pathlib import Path
import platform
import select
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from meshticulum.bench import open_watch as module
from meshticulum.bench.linux_hat import HardwareError


class SyntheticWatch(unittest.TestCase):
    def setUp(self):
        self.read, self.write = os.pipe()
        os.set_blocking(self.read, False)
        os.set_inheritable(self.read, False)
        self.watch = object.__new__(module.OpenCollisionWatch)
        self.watch.fd, self.watch.watch_stat = self.read, os.fstat(self.read)
        self.watch.closed, self.watch.failed, self.watch.pid = False, None, os.getpid()
        self.watch.paths, self.watch.directories, self.watch.watches = {}, {}, {3}
        self.watch.device_stat = os.fstat(self.read)
        self.watch.scan = lambda info: []
        self.watch.watch_lines = (b'synthetic descriptor-source fixture',)
        self.watch._fdinfo_watch_lines = lambda **kwargs: self.watch.watch_lines

    def tearDown(self):
        self.watch.close()
        os.close(self.write)

    def event(self, mask, *, wd=3, cookie=0, name=b''):
        os.write(self.write, struct.pack('=iIII', wd, mask, cookie, len(name)) + name)

    def test_empty_queue_is_fresh_and_does_not_call_procfs(self):
        self.watch.scan = lambda info: self.fail('event mode performed a procfs scan')
        self.assertTrue(self.watch.current())
        self.assertTrue(self.watch.current())

    def test_foreign_open_latches_even_after_the_event_is_consumed(self):
        self.event(module._IN_OPEN)
        self.assertFalse(self.watch.current())
        self.assertEqual(self.watch.failed, 'device_watch_event')
        self.assertFalse(self.watch.current())

    def test_invalidation_overflow_and_unknown_watch_refuse(self):
        for mask, wd in ((module._IN_DELETE_SELF, 3), (module._IN_MOVE_SELF, 3),
                         (module._IN_UNMOUNT, 3), (module._IN_IGNORED, 3),
                         (module._IN_Q_OVERFLOW, -1), (module._IN_OPEN, 44)):
            with self.subTest(mask=mask, wd=wd):
                self.watch.failed = None
                self.event(mask, wd=wd)
                self.assertFalse(self.watch.current())
                self.assertFalse(self.watch.current())

    def test_short_or_malformed_event_refuses(self):
        for raw in (b'bad', struct.pack('=iIII', 3, module._IN_OPEN, 0, 20),
                    struct.pack('=iIII', 3, module._IN_OPEN, 0, 4) + b'name'):
            with self.subTest(raw=raw):
                self.watch.failed = None
                os.write(self.write, raw)
                self.assertFalse(self.watch.current())
                self.assertEqual(self.watch.failed, 'device_watch_malformed')

    def test_read_error_is_latched(self):
        with mock.patch.object(module.os, 'read', side_effect=OSError(errno.EIO, 'read control')):
            self.assertFalse(self.watch.current())
        self.assertFalse(self.watch.current())

    def test_closed_descriptor_is_refused(self):
        os.close(self.read)
        self.watch.fd = self.read
        self.assertFalse(self.watch.current())
        # The fixture already closed this descriptor, so teardown must not
        # close an unrelated future fd with this recycled integer.
        self.watch.fd = None

    def test_reused_descriptor_is_refused(self):
        with tempfile.TemporaryFile() as replacement:
            os.dup2(replacement.fileno(), self.read)
            self.assertFalse(self.watch.current())
            self.assertEqual(self.watch.failed, 'device_watch_fd_changed')

    def test_fallback_scans_every_call_and_latches_a_new_opener(self):
        os.close(self.read)
        self.watch.fd = None
        calls = []
        self.watch.scan = lambda info: calls.append(info) or ([] if len(calls) < 3 else [123])
        self.assertTrue(self.watch.current())
        self.assertTrue(self.watch.current())
        self.assertFalse(self.watch.current())
        self.assertEqual(len(calls), 3)
        self.assertFalse(self.watch.current())
        self.assertEqual(len(calls), 3)


@unittest.skipUnless(platform.system() == 'Linux', 'requires actual Linux inotify')
class LinuxWatch(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='meshticulum-open-watch-')
        self.root = Path(self.folder.name)
        self.node = self.root / 'owned-fixture'
        self.node.write_bytes(b'ordinary file, never a device')
        self.info = self.node.stat()
        self.watches = []
        # Start the sibling before acquiring ownership. Commands later make it
        # open and close files while the watched owner never forks.
        self.child = subprocess.Popen([sys.executable, '-u', '-c',
            "import os,sys; "
            "exec(\"for line in sys.stdin:\\n path=line.rstrip('\\\\n'); fd=os.open(path,os.O_RDONLY); os.close(fd); print('opened',flush=True)\")"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def tearDown(self):
        for watch in self.watches:
            watch.close()
        self.child.communicate(timeout=5)
        self.assertEqual(self.child.returncode, 0)
        self.folder.cleanup()

    def watch(self, *, scan=None):
        watch = module.OpenCollisionWatch(self.info, device_root=self.root,
                                         opener_scan=scan or (lambda info: []))
        self.assertIsNotNone(watch.fd, 'Linux fixture must exercise actual inotify, not fallback')
        self.watches.append(watch)
        return watch

    def child_open_close(self, node=None):
        self.child.stdin.write(os.fsencode(node or self.node) + b'\n')
        self.child.stdin.flush()
        self.assertTrue(select.select([self.child.stdout], [], [], 5)[0], 'sibling open timeout')
        self.assertEqual(self.child.stdout.readline(), b'opened\n')

    def test_initial_scan_is_after_watch_and_refuses_preexisting_opener(self):
        seen = []

        def scan(info):
            seen.append(info)
            # An opener after watch installation must already be queued when
            # the initial scan returns. It may close before the next probe.
            self.child_open_close()
            return []

        with self.assertRaisesRegex(HardwareError, 'device_watch_event'):
            self.watch(scan=scan)
        self.assertEqual(seen, [self.info])
        with self.assertRaisesRegex(HardwareError, 'radio_already_open'):
            self.watch(scan=lambda info: [12345])

    def test_transient_sibling_open_and_close_is_latched(self):
        watch = self.watch()
        self.assertTrue(watch.current())
        self.child_open_close()
        self.assertFalse(watch.current())
        self.assertFalse(watch.current())

    def test_existing_different_inode_alias_is_watched(self):
        alias = self.root / 'same-device-id-different-inode'
        alias.write_bytes(b'other fixture inode')
        self.assertNotEqual(alias.stat().st_ino, self.info.st_ino)
        # Ordinary-file fixtures have rdev=0: this exercises enumeration of
        # distinct inodes with the same type/rdev without creating device nodes.
        watch = self.watch()
        self.child_open_close(alias)
        self.assertFalse(watch.current())

    def test_existing_nested_alias_is_watched(self):
        directory = self.root / 'nested'
        directory.mkdir()
        alias = directory / 'alias'
        alias.write_bytes(b'nested fixture')
        watch = self.watch()
        self.child_open_close(alias)
        self.assertFalse(watch.current())

    def test_create_then_remove_alias_between_probes_is_latched(self):
        watch = self.watch()
        alias = self.root / 'new-alias'
        alias.write_bytes(b'created after registration')
        alias.unlink()
        self.assertFalse(watch.current())

    def test_path_replacement_and_directory_replacement_are_refused(self):
        watch = self.watch()
        moved = self.root / 'old-node'
        self.node.rename(moved)
        self.node.write_bytes(b'replacement')
        self.assertFalse(watch.current())

    def test_symlink_alias_target_replacement_is_refused(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.node.name)
        watch = self.watch()
        alias.unlink()
        alias.symlink_to('missing-target')
        self.assertFalse(watch.current())

    def test_source_inventory_refuses_directory_change_without_an_event(self):
        watch = self.watch()
        # Masking the read models an unmonitorable filesystem such as devpts.
        # Fresh directory inventory must still refuse a retained-tree change.
        with mock.patch.object(watch, '_events_current', return_value=True):
            (self.root / 'new-node').write_bytes(b'changed')
            self.assertFalse(watch.current())
            self.assertEqual(watch.failed, 'device_watch_directory_changed')

    def test_source_binding_refuses_mount_style_inode_change_without_an_event(self):
        watch = self.watch()
        with mock.patch.object(watch, '_events_current', return_value=True):
            moved = self.root / 'original'
            self.node.rename(moved)
            self.node.write_bytes(b'replacement')
            self.assertFalse(watch.current())
            self.assertEqual(watch.failed, 'device_watch_path_changed')

    def test_owner_fork_invalidates_parent_and_child(self):
        watch = self.watch()
        pid = os.fork()
        if pid == 0:
            os._exit(0 if not watch.current() else 3)
        finished, status = os.waitpid(pid, 0)
        self.assertEqual(finished, pid)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        self.assertFalse(watch.current())
        self.assertEqual(watch.failed, 'owner_forked')

    def test_new_empty_inotify_at_same_fd_is_refused_and_not_closed(self):
        watch = self.watch()
        fd = watch.fd
        library = ctypes.CDLL(None, use_errno=True)
        initialize = library.inotify_init1
        initialize.argtypes, initialize.restype = [ctypes.c_int], ctypes.c_int
        replacement = initialize(os.O_NONBLOCK | os.O_CLOEXEC)
        self.assertGreaterEqual(replacement, 0)
        try:
            os.dup2(replacement, fd, inheritable=False)
            # This is the regression: anon-inode fstat and flags alone can be
            # identical for the old and this unrelated empty inotify instance.
            self.assertEqual(module._identity(os.fstat(fd)), module._identity(watch.watch_stat))
            self.assertFalse(watch.current())
            watch.close()
            os.fstat(fd)  # A known recycled descriptor belongs to its new owner.
        finally:
            os.close(replacement)
            os.close(fd)

    def test_external_watch_fd_close_has_safe_idempotent_cleanup(self):
        watch = self.watch()
        os.close(watch.fd)
        self.assertFalse(watch.current())
        watch.close()
        watch.close()

    def test_real_node_deletion_still_releases_original_watch(self):
        watch = self.watch()
        fd = watch.fd
        self.node.unlink()
        self.assertFalse(watch.current())
        watch.close()
        with self.assertRaises(OSError):
            os.fstat(fd)

    def test_close_is_idempotent_and_releases_watch_fd(self):
        watch = self.watch()
        fd = watch.fd
        watch.close()
        watch.close()
        self.assertFalse(watch.current())
        with self.assertRaises(OSError):
            os.fstat(fd)


if __name__ == '__main__':
    unittest.main()
