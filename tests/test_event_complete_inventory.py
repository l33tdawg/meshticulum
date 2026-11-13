import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest
from meshticulum.bench import open_watch as m
from tests.test_open_watch import SyntheticWatch

@pytest.mark.parametrize('kind,accepted', [('tmpfs',True),('devtmpfs',True),('devpts',False),('hugetlbfs',False),('ext4',False)])
def test_mount_type_and_device(kind,accepted):
 raw=f'1 0 0:7 / /dev rw - {kind} dev rw\n'.encode()
 assert m._event_complete_mount(raw,Path('/dev/private'),SimpleNamespace(st_dev=os.makedev(0,7))) is accepted
 assert not m._event_complete_mount(raw,Path('/dev/private'),SimpleNamespace(st_dev=os.makedev(0,8)))

def test_nested_unknown_ambiguous_and_escaped_mounts():
 base=b'1 0 0:7 / /dev rw - tmpfs dev rw\n'
 info=SimpleNamespace(st_dev=os.makedev(0,7))
 assert not m._event_complete_mount(base+base,Path('/dev'),info)
 assert not m._event_complete_mount(base+b'2 1 0:7 / /dev/pts rw - devpts pts rw\n',Path('/dev/pts/0'),info)
 assert m._event_complete_mount(b'1 0 0:7 / /dev/a\\040b rw - tmpfs dev rw\n',Path('/dev/a b/x'),info)
 assert not m._event_complete_mount(b'malformed',Path('/dev'),info)

class EventFreshnessTests(SyntheticWatch):
 def test_only_qualified_inventory_skips_and_mount_stays_fresh(self):
  p=Path('/synthetic');self.watch.directories={p:()};self.watch.event_directories=frozenset({p})
  self.watch.mountinfo=b'original'
  with mock.patch.object(m,'_mountinfo',return_value=b'original') as mounts, mock.patch.object(m,'_entries',side_effect=AssertionError('unexpected enumeration')):
   self.assertTrue(self.watch.current());self.assertTrue(self.watch.current());self.assertEqual(mounts.call_count,2)
  with mock.patch.object(m,'_mountinfo',return_value=b'changed'):
   self.assertFalse(self.watch.current())
 def test_unknown_directory_always_enumerates(self):
  p=Path('/synthetic');self.watch.directories={p:()};self.watch.event_directories=frozenset()
  with mock.patch.object(m,'_entries',return_value=()) as entries:
   self.assertTrue(self.watch.current());self.assertTrue(self.watch.current());self.assertEqual(entries.call_count,2)
 def test_event_coverage_never_excuses_mutation(self):
  self.watch.event_directories=frozenset({Path('/synthetic')})
  for mask in (m._IN_CREATE,m._IN_DELETE,m._IN_MOVED_FROM,m._IN_MOVED_TO,m._IN_ATTRIB):
   self.watch.failed=None;self.event(mask);self.assertFalse(self.watch.current())
 def test_reduced_mask_cannot_qualify(self):
  p=Path('/synthetic');self.watch.directories={p:()};self.watch.directory_wds={p:3}
  self.watch.watch_lines=(b'inotify wd:3 ino:1 sdev:7 mask:100 ignored_mask:0',)
  with mock.patch.object(m,'_event_complete_mount',return_value=True),mock.patch.object(Path,'stat',return_value=SimpleNamespace(st_dev=7)):
   self.watch._bind_event_directories();self.assertEqual(self.watch.event_directories,frozenset())

 def test_ignored_mask_cannot_qualify(self):
  p=Path('/synthetic');self.watch.directories={p:()};self.watch.directory_wds={p:3}
  self.watch.watch_lines=(f'inotify wd:3 ino:1 sdev:7 mask:{m._DIRECTORY_EVENTS:x} ignored_mask:100'.encode(),)
  with mock.patch.object(m,'_event_complete_mount',return_value=True),mock.patch.object(Path,'stat',return_value=SimpleNamespace(st_dev=7)):
   self.watch._bind_event_directories();self.assertEqual(self.watch.event_directories,frozenset())

def test_public_option_bound_without_hardware():
 from tests.test_radio_file_example import example
 args=SimpleNamespace(interface='hat',device=None,event_complete_inventory=False)
 with mock.patch.object(example,'host_boot',return_value='test-boot'):
  hardware=example.FileHardware(args)
 args.event_complete_inventory=True
 assert hardware.binding != hardware._binding(args)
 assert hardware.transport is None

@pytest.mark.parametrize('mask,qualified', [(0xfc4, True), (0xfc4 & ~m._IN_CREATE, False), (0xfc4 & ~m._IN_ATTRIB, False)])
def test_actual_linux_fdinfo_omits_implicit_events(mask, qualified):
 p=Path('/synthetic');watch=object.__new__(m.OpenCollisionWatch)
 watch.directories={p:()};watch.directory_wds={p:3};watch.mountinfo=b'table'
 watch.watch_lines=(f'inotify wd:3 ino:1 sdev:7 mask:{mask:x} ignored_mask:0'.encode(),)
 with mock.patch.object(m,'_event_complete_mount',return_value=True),mock.patch.object(Path,'stat',return_value=SimpleNamespace(st_dev=7)):
  watch._bind_event_directories()
 assert (p in watch.event_directories) is qualified
 assert m._DIRECTORY_REPORTED_EVENTS == 0xfc4
