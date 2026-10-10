# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Fault tests of the real app-only transaction; no hardware or flash claims."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tarfile
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from meshticulum.bench import flash_app as flash
from meshticulum.bench.linux_hat import HardwareError

spec = importlib.util.spec_from_file_location('ab_bootstrap', ROOT / 'firmware/meshticulum-bench/ab_bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)

BINDING = {'unit': 'unit-a', 'usb_serial': 'F8:5B:1B:A5:F6:08', 'bundle_manifest_sha256': '3' * 64}


def image(size, fill):
    result = bytearray([fill] * size)
    result[0], result[12:14] = 0xE9, b'\x09\0'
    return bytes(result)


OLD, NEW = image(16000, 61), image(5001, 83)


def table(entries=None):
    entries = entries or [(1, 2, 0x9000, 0x5000, b'nvs'),
                          (1, 0, 0xE000, 0x2000, b'otadata'),
                          (0, 0x10, 0x10000, 0x80000, b'app0'),
                          (0, 0x11, 0x90000, 0x80000, b'app1'),
                          (1, 0x82, 0x110000, 0xF0000, b'spiffs')]
    raw = b''.join(flash.ENTRY.pack(0x50AA, *e, 0) for e in entries)
    return (raw + flash.MD5_MARKER + hashlib.md5(raw).digest()).ljust(4096, b'\xff')


class ROM:
    flash_size = 2 * 1024 * 1024

    def __init__(self):
        self.data = bytearray(b'\xff' * self.flash_size)
        self.data[0x8000:0x9000] = table()
        self.data[0x10000:0x10000 + len(OLD)] = OLD
        self.data[0x9000:0x9007] = b'PRIVATE'
        self.data[0x120000:0x120007] = b'PRIVATE'
        self.reads, self.writes = [], []
        self.failure, self.audit = None, None

    def read(self, offset, size):
        self.reads.append((offset, size))
        return bytes(self.data[offset:offset + size])

    def md5(self, offset, size):
        return hashlib.md5(self.data[offset:offset + size]).hexdigest()

    def write(self, offset, data):
        if self.audit:
            self.audit(offset, data)
        self.writes.append((offset, data))
        assert offset == 0x10000 and len(data) % 4096 == 0
        if self.failure in ('partial_once', 'always'):
            self.data[offset:offset + 311] = data[:311]
            if self.failure == 'partial_once':
                self.failure = None
            raise OSError('simulated_transport_failure')
        self.data[offset:offset + len(data)] = data
        if self.failure == 'bad_readback_once':
            self.data[offset + 100] ^= 1
            self.failure = None


def receipt(tmp_path):
    return flash.Receipt(tmp_path / 'receipt', create=True)


def test_apply_preserves_every_other_byte_and_commit_precedes_erase(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    before = bytes(rom.data)

    def check_before_erase(offset, data):
        state = record.state()
        assert state['phase'] == 'writing'
        assert state['backup_sha256'] == flash.sha(record.read('app-before.bin'))
        assert (offset, len(data)) == (0x10000, 8192)

    rom.audit = check_before_erase
    state = flash.apply(rom, NEW, OLD, record, BINDING)
    assert state['phase'] == 'flash_verified'
    assert rom.data[:0x10000] == before[:0x10000]
    assert rom.data[0x10000:0x10000 + len(NEW)] == NEW
    assert rom.data[0x10000 + len(NEW):] == before[0x10000 + len(NEW):]
    assert len(rom.writes) == 1 and state['bench_tx_commands'] == 0
    assert record.read('app-before.bin') == before[0x10000:0x12000]
    # No raw reads of NVS, secondary app or SPIFFS, even though their hashes
    # are compared by the ROM to catch an accidental out-of-range write.
    assert all((offset == 0x8000 and size == 4096) or (offset == 0xE000 and size == 8192)
               or (offset >= 0x10000 and offset + size <= 0x12000) for offset, size in rom.reads)


def test_rollback_keeps_new_nvs_and_restores_exact_old_app(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    flash.apply(rom, NEW, OLD, record, BINDING)
    rom.data[0x9000:0x9008] = b'NEW-BOOT'
    state = flash.rollback(rom, record, BINDING)
    assert state['phase'] == 'rolled_back'
    assert rom.data[0x10000:0x10000 + len(OLD)] == OLD
    assert rom.data[0x9000:0x9008] == b'NEW-BOOT'


def test_exact_observed_arduino_app0_selector_is_preserved(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    selector = bytearray(b'\xff' * 8192)
    selector[:4] = struct.pack('<I', 1)
    selector[28:32] = struct.pack('<I', zlib.crc32(selector[:4], 0xFFFFFFFF))
    selector[4096:4100] = bytes(4)
    assert flash.sha(selector) == flash.APP0_OTA_SHA256
    rom.data[0xE000:0x10000] = selector
    state = flash.apply(rom, NEW, OLD, record, BINDING)
    assert state['plan']['ota_selector_sha256'] == flash.APP0_OTA_SHA256
    assert rom.data[0xE000:0x10000] == selector
    flash.rollback(rom, record, BINDING)
    assert rom.data[0xE000:0x10000] == selector


@pytest.mark.parametrize('failure', ['partial_once', 'bad_readback_once'])
def test_write_failure_rolls_back_before_returning_error(tmp_path, failure):
    rom, record = ROM(), receipt(tmp_path)
    before = bytes(rom.data)
    rom.failure = failure
    with pytest.raises((HardwareError, OSError)):
        flash.apply(rom, NEW, OLD, record, BINDING)
    assert record.state()['phase'] == 'rolled_back' and bytes(rom.data) == before


def test_failed_rollback_stays_recoverable_after_connection_recovery(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    rom.failure = 'always'
    with pytest.raises(OSError):
        flash.apply(rom, NEW, OLD, record, BINDING)
    assert record.state()['phase'] == 'recovery_required'
    rom.failure = None
    assert flash.rollback(rom, record, BINDING)['phase'] == 'rolled_back'
    assert rom.data[0x10000:0x10000 + len(OLD)] == OLD


@pytest.mark.parametrize('change', ['prior', 'ota', 'chip', 'table'])
def test_refuse_unverified_current_installation_without_writes(tmp_path, change):
    rom, record = ROM(), receipt(tmp_path)
    new = NEW
    if change == 'prior':
        rom.data[0x10000 + 57] ^= 1
    elif change == 'ota':
        rom.data[0xE000] = 1
    elif change == 'chip':
        new = NEW[:12] + b'\x00\x00' + NEW[14:]
    elif change == 'table':
        rom.data[0x8000 + 99] ^= 1
    with pytest.raises(HardwareError):
        flash.apply(rom, new, OLD, record, BINDING)
    assert not rom.writes and not (record.directory / 'app-before.bin').exists()


@pytest.mark.parametrize('bad', [
    [(1, 0, 0xE000, 0x2000, b'otadata'), (0, 0x10, 0x10000, 0x80000, b'app0'),
     (1, 2, 0x11000, 0x1000, b'overlap')],
    [(1, 0, 0xE000, 0x2000, b'otadata'), (0, 0x10, 0x20000, 0x80000, b'app0')],
    [(1, 0, 0xE000, 0x2000, b'otadata'), (0, 0x10, 0x10000, 0x80000, b'app0'),
     (0, 0, 0x90000, 0x80000, b'factory')],
    [(1, 0, 0xE000, 0x2000, b'otadata'), (0, 0x10, 0x10000, 0x300000, b'app0')],
])
def test_valid_checksum_does_not_admit_bad_partition_geometry(bad):
    with pytest.raises(HardwareError):
        flash.partitions(table(bad), ROM.flash_size)


def test_unstable_backup_refuses_flash(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    read = rom.read
    calls = 0

    def unstable(offset, size):
        nonlocal calls
        data = read(offset, size)
        if offset == 0x10000:
            calls += 1
            if calls == 2:
                return data[:-1] + bytes([data[-1] ^ 1])
        return data

    rom.read = unstable
    with pytest.raises(HardwareError, match='rollback_readback_mismatch'):
        flash.apply(rom, NEW, OLD, record, BINDING)
    assert not rom.writes


def test_compare_and_swap_refuses_change_after_backup(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    save = record.save

    def changed(state):
        save(state)
        if state['phase'] == 'backed_up':
            rom.data[0x120000] ^= 1

    record.save = changed
    with pytest.raises(HardwareError, match='flash_changed_after_backup'):
        flash.apply(rom, NEW, OLD, record, BINDING)
    assert not rom.writes and record.state()['phase'] == 'backed_up'


@pytest.mark.parametrize('change', ['device', 'backup', 'new_app', 'layout'])
def test_rollback_refuses_unrelated_or_tampered_state(tmp_path, change):
    rom, record = ROM(), receipt(tmp_path)
    flash.apply(rom, NEW, OLD, record, BINDING)
    binding = dict(BINDING)
    if change == 'device':
        binding['unit'] = 'unit-b'
    elif change == 'backup':
        (record.directory / 'app-before.bin').write_bytes(b'wrong')
    elif change == 'new_app':
        rom.data[0x10002] ^= 1
    elif change == 'layout':
        rom.data[0x8000 + 55] ^= 1
    with pytest.raises(HardwareError):
        flash.rollback(rom, record, binding)
    assert len(rom.writes) == 1


def test_post_program_receipt_failure_restores_original(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    save = record.save

    def full_disk(state):
        if state['phase'] == 'flash_verified':
            raise OSError('disk_full')
        save(state)

    record.save = full_disk
    with pytest.raises(OSError, match='disk_full'):
        flash.apply(rom, NEW, OLD, record, BINDING)
    assert record.state()['phase'] == 'rolled_back'
    assert rom.data[0x10000:0x10000 + len(OLD)] == OLD


def test_outside_region_change_is_not_reported_as_a_complete_recovery(tmp_path):
    rom, record = ROM(), receipt(tmp_path)
    write = rom.write

    def bad_address_driver(offset, data):
        write(offset, data)
        if len(rom.writes) == 1:
            rom.data[0x120000] ^= 1

    rom.write = bad_address_driver
    with pytest.raises(HardwareError, match='outside_app_changed'):
        flash.apply(rom, NEW, OLD, record, BINDING)
    state = record.state()
    assert state['phase'] == 'recovery_required' and state['outside_app_changed_before_boot']
    assert rom.data[0x10000:0x10000 + len(OLD)] == OLD
    with pytest.raises(HardwareError, match='outside_app_change_requires_operator_review'):
        flash.rollback(rom, record, BINDING)


def test_stale_uncommitted_receipt_does_not_block_recovery(tmp_path):
    record = receipt(tmp_path)
    (record.directory / 'receipt.json.dead.pending').write_bytes(b'crash-before-rename')
    record.save({'phase': 'writing'})
    assert record.state()['phase'] == 'writing'
    assert stat_mode(record.directory / 'receipt.json') == 0o600
    with pytest.raises(FileExistsError):
        flash.Receipt(record.directory, create=True)


def stat_mode(path):
    return path.stat().st_mode & 0o777


def archive(extra_name, *, symlink=False):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w:gz') as stream:
        for name in ['manifest.json', 'install_ab.py', 'firmware.bin', 'rollback-r7.bin', extra_name]:
            item = tarfile.TarInfo(name)
            item.size = 1
            if name == extra_name and symlink:
                item.type, item.linkname, item.size = tarfile.SYMTYPE, '/etc/passwd', 0
            stream.addfile(item, io.BytesIO(b'x'))
    return result.getvalue()


@pytest.mark.parametrize('name,symlink', [('../escape', False), ('/absolute', False),
                                        ('install_ab.py', False), ('symlink', True)])
def test_privileged_bootstrap_rejects_unsafe_archive_before_extraction(name, symlink):
    data = archive(name, symlink=symlink)
    with pytest.raises(RuntimeError):
        bootstrap.files_from_archive(data, flash.sha(data))


def test_privileged_bootstrap_rejects_wrong_complete_archive_pin():
    data = archive('extra.py')
    with pytest.raises(RuntimeError, match='archive_hash_mismatch'):
        bootstrap.files_from_archive(data, '0' * 64)
