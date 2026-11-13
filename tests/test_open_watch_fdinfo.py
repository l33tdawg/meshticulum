"""Real bounded fdinfo reader/parser against synthetic kernel-format bytes."""
import os
import unittest
from unittest.mock import patch
from meshticulum.bench import open_watch as m
L1=b'inotify wd:1 ino:3be sdev:5 mask:fc4 ignored_mask:0 fhandle-bytes:8 fhandle-type:1 f_handle:be03000000000000'
L2=L1.replace(b'wd:1',b'wd:2').replace(b'ino:3be',b'ino:3bf')
RAW=b'pos:\t0\nflags:\t02004000\n'+L1+b'\n'+L2+b'\n'
class Fdinfo(unittest.TestCase):
    def setUp(self):
        self.w=object.__new__(m.OpenCollisionWatch)
        for k,v in dict(fd=7,watch_lines=None,watches={1,2},watch_inodes={1:958,2:959},anchor_wd=1,_initial_fdinfo_binding=None,closed=False).items(): setattr(self.w,k,v)
    def read(self,raw=RAW,**kw):
        chunks=[raw[i:i+65536] for i in range(0,len(raw),65536)]+[b'']
        with patch.object(m.os,'open',return_value=91) as op, patch.object(m.os,'read',side_effect=chunks) as rd, patch.object(m.os,'close') as cl:
            try: return self.w._fdinfo_watch_lines(**kw)
            finally:
                op.assert_called_once_with('/proc/self/fdinfo/7',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
                cl.assert_called_once_with(91); self.assertGreater(rd.call_count,0)
    def seed(self): self.w.watch_lines=self.read(capture_initial=True)
    def test_equal_fresh_bytes_skip_parse_only(self):
        self.seed()
        with patch.object(m,'_WATCH_LINE') as p:
            self.assertEqual(self.read(bytes(bytearray(RAW))),self.w.watch_lines); p.fullmatch.assert_not_called()
        self.assertIsInstance(self.w._initial_fdinfo_binding[3],frozenset)
    def test_changed_header_reordered_lines_never_refresh(self):
        self.seed(); original=self.w._initial_fdinfo_binding
        for raw in (RAW.replace(b'pos:\t0',b'pos:\t1'),L2+b'\n'+L1+b'\n'):
            with patch.object(m,'_WATCH_LINE',wraps=m._WATCH_LINE) as p:
                self.assertEqual(self.read(raw),self.w.watch_lines); self.assertEqual(p.fullmatch.call_count,2)
                self.assertIs(self.w._initial_fdinfo_binding,original)
    def test_malformed_duplicate_rebound_removed_empty(self):
        self.seed()
        for raw in (RAW.replace(b'ino:3be',b'ino:xxx'),RAW+L1+b'\n',RAW.replace(b'ino:3be',b'ino:3bd'),L1+b'\n',b'pos:0\n'):
            with self.subTest(raw=raw), self.assertRaises(m.HardwareError): self.read(raw)
    def test_mutable_state_forces_original_parser(self):
        for key,value in [('watch_inodes',{1:957,2:959}),('watches',{1}),('watch_lines',(L1,)),('anchor_wd',2)]:
            self.setUp(); self.seed(); setattr(self.w,key,value)
            with patch.object(m,'_WATCH_LINE',wraps=m._WATCH_LINE) as p:
                try: self.read()
                except m.HardwareError: pass
                self.assertGreater(p.fullmatch.call_count,0)
    def test_cached_and_uncached_semantics_match_adversarial_inputs(self):
        variants = [RAW, RAW.replace(b'pos:\t0', b'pos:\t2'),
                    L2+b'\n'+L1+b'\n', RAW+L1+b'\n',
                    RAW.replace(b'ino:3be', b'ino:3bd'),
                    RAW.replace(b'mask:fc4', b'mask:fc0'),
                    L1+b'\n', L2+b'\n', b'inotify malformed\n', b'']
        mutations = [None, ('watches', {1}), ('watch_inodes', {1:957,2:959}),
                     ('watch_lines', (L1,)), ('anchor_wd', 2)]
        for raw in variants:
            for mutation in mutations:
                for removed in (False, True):
                    outcomes = []
                    for cached in (False, True):
                        self.setUp(); self.seed()
                        if not cached: self.w._initial_fdinfo_binding = None
                        if mutation: setattr(self.w, *mutation)
                        try: outcomes.append(('return', self.read(raw, allow_removed=removed)))
                        except m.HardwareError as error: outcomes.append(('error', str(error)))
                    with self.subTest(raw=raw, mutation=mutation, removed=removed):
                        self.assertEqual(*outcomes)

    def test_teardown_always_parses_requires_anchor(self):
        self.seed()
        with patch.object(m,'_WATCH_LINE',wraps=m._WATCH_LINE) as p:
            self.assertEqual(self.read(allow_removed=True),self.w.watch_lines); self.assertEqual(p.fullmatch.call_count,2)
        self.assertEqual(self.read(L1+b'\n',allow_removed=True),(L1,))
        with self.assertRaises(m.HardwareError): self.read(L2+b'\n',allow_removed=True)
    def test_oversize_read_error_close(self):
        with self.assertRaisesRegex(m.HardwareError,'too_large'): self.read(b'x'*2097153)
        with patch.object(m.os,'open',return_value=91), patch.object(m.os,'read',side_effect=OSError('I/O')), patch.object(m.os,'close') as cl:
            with self.assertRaises(OSError): self.w._fdinfo_watch_lines()
            cl.assert_called_once_with(91)
    def test_initial_failure_and_reseed(self):
        with self.assertRaises(m.HardwareError): self.read(b'inotify bad\n',capture_initial=True)
        self.assertIsNone(self.w._initial_fdinfo_binding); self.seed(); original=self.w._initial_fdinfo_binding
        with self.assertRaises(m.HardwareError): self.read(capture_initial=True)
        self.assertIs(self.w._initial_fdinfo_binding,original)
    def test_descriptor_and_close_use_actual_reader(self):
        self.seed(); self.w.watch_stat=os.stat(__file__)
        with patch.object(m.os,'fstat',return_value=self.w.watch_stat), patch.object(m.fcntl,'fcntl',return_value=0), patch.object(m.os,'open',side_effect=AssertionError('must not read')):
            self.assertFalse(self.w._descriptor_current())
        for raw,close_mode,expected in [(RAW,False,True),(RAW.replace(b'mask:fc4',b'mask:fc0'),False,False),(L1+b'\n',True,[91,7]),(b'pos:0\n',True,[91])]:
            self.w.fd=7; self.w.closed=False
            with patch.object(m.os,'fstat',return_value=self.w.watch_stat), patch.object(m.fcntl,'fcntl',side_effect=[m.fcntl.FD_CLOEXEC,os.O_NONBLOCK]), patch.object(m.os,'open',return_value=91), patch.object(m.os,'read',side_effect=[raw,b'']), patch.object(m.os,'close') as cl:
                if close_mode:
                    self.w.close(); self.assertEqual([c.args[0] for c in cl.call_args_list],expected)
                else: self.assertEqual(self.w._descriptor_current(),expected)
if __name__=='__main__': unittest.main()
