"""Application-only flash transaction with a durable, exact-range rollback.

The ROM adapter owns a pinned, exclusive serial connection. This module never
reads NVS or SPIFFS into the host: unchanged regions are compared using the ROM
MD5 command. Only the partition table, OTA selector and overwritten app
sectors are read. MD5 here detects transport/write mistakes, not authenticity;
bundle SHA-256 pins and an operator-authorized root installation supply trust.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import uuid

from .linux_hat import HardwareError, require

TABLE_OFFSET, TABLE_SIZE, APP_OFFSET, SECTOR = 0x8000, 0x1000, 0x10000, 0x1000
ENTRY = struct.Struct('<HBBII16sI')
MD5_MARKER = b'\xeb\xeb' + b'\xff' * 14
MAX_APP_BYTES = 4 * 1024 * 1024
# Retained Arduino boot_app0.bin, also read independently from A's actual OTA
# partition. First record: sequence 1, undefined state, valid sequence CRC;
# second record: invalid CRC. ESP-IDF 4.4.6 therefore selects ota_0. Accept this
# exact 8 KiB default image or an erased selector; never rewrite either one.
APP0_OTA_SHA256 = 'f94c5d786a7a8fab06ac5d10e33bf37711a6697636dc037559ea19cc410a17f0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def md5(data):
    return hashlib.md5(data).hexdigest()


def partitions(data, flash_size):
    require(type(data) is bytes and len(data) == TABLE_SIZE, 'invalid_partition_table_size')
    entries, found_checksum = [], False
    for offset in range(0, TABLE_SIZE, ENTRY.size):
        raw = data[offset:offset + ENTRY.size]
        if raw[:16] == MD5_MARKER:
            require(entries and raw[16:] == hashlib.md5(data[:offset]).digest(),
                    'partition_table_checksum')
            require(data[offset + ENTRY.size:] == b'\xff' * (TABLE_SIZE - offset - ENTRY.size),
                    'partition_table_trailing_data')
            found_checksum = True
            break
        require(raw[:2] == b'\xaa\x50', 'invalid_partition_magic_or_missing_checksum')
        _, kind, subtype, start, size, label, flags = ENTRY.unpack(raw)
        require(start >= TABLE_OFFSET + TABLE_SIZE and start % SECTOR == 0
                and size > 0 and size % SECTOR == 0 and start + size <= flash_size,
                'partition_out_of_bounds')
        require(flags == 0, 'encrypted_or_unknown_partition_flags')
        try:
            label = label.split(b'\0', 1)[0].decode('ascii')
        except UnicodeError:
            raise HardwareError('invalid_partition_label') from None
        require(label and label not in [item['label'] for item in entries], 'duplicate_partition_label')
        require(all(start + size <= e['offset'] or start >= e['offset'] + e['size']
                    for e in entries), 'overlapping_partitions')
        entries.append(dict(type=kind, subtype=subtype, offset=start, size=size, label=label))
    require(found_checksum, 'missing_partition_checksum')
    apps = [e for e in entries if e['type'] == 0 and e['subtype'] == 0x10]
    ota = [e for e in entries if e['type'] == 1 and e['subtype'] == 0]
    require(len(apps) == 1 and apps[0]['offset'] == APP_OFFSET, 'unexpected_app_partition')
    require(len(ota) == 1 and ota[0]['size'] == 2 * SECTOR, 'unexpected_ota_selector')
    require(not any(e['type'] == 0 and e['subtype'] == 0 for e in entries),
            'factory_app_would_take_precedence')
    return apps[0], ota[0]


def outside_hashes(rom, span):
    end = APP_OFFSET + span
    return {'before_app': rom.md5(0, APP_OFFSET),
            'after_written_sectors': rom.md5(end, rom.flash_size - end)}


def plan(rom, image, previous_image):
    require(type(image) is bytes and type(previous_image) is bytes
            and 32 <= len(image) <= MAX_APP_BYTES and 32 <= len(previous_image) <= MAX_APP_BYTES,
            'invalid_app_size')
    # ESP32-S3 extended image header has chip ID 9 at offset 12.
    require(image[0] == previous_image[0] == 0xE9
            and image[12:14] == previous_image[12:14] == b'\x09\0', 'wrong_app_chip')
    table = rom.read(TABLE_OFFSET, TABLE_SIZE)
    app, ota = partitions(table, rom.flash_size)
    span = (len(image) + SECTOR - 1) // SECTOR * SECTOR
    require(max(span, len(previous_image)) <= app['size'], 'app_does_not_fit')
    # No OTA selector is modified. Other valid OTA selections require review.
    selector = rom.read(ota['offset'], ota['size'])
    require(selector == b'\xff' * ota['size'] or sha(selector) == APP0_OTA_SHA256,
            'unreviewed_ota_selector')
    require(rom.md5(APP_OFFSET, len(previous_image)) == md5(previous_image),
            'installed_app_does_not_match_pinned_rollback')
    return {'schema': 'meshticulum.app-flash-plan.v1', 'flash_size': rom.flash_size,
            'partition_sha256': sha(table), 'ota_selector_sha256': sha(selector),
            'app': app, 'span': span,
            'new_sha256': sha(image), 'new_bytes': len(image),
            'prior_sha256': sha(previous_image), 'prior_bytes': len(previous_image),
            'prior_md5': md5(previous_image), 'range_md5': rom.md5(APP_OFFSET, span),
            'outside_md5': outside_hashes(rom, span)}


class Receipt:
    def __init__(self, directory, *, create=False):
        self.directory = Path(directory)
        if create:
            self.directory.mkdir(mode=0o700)
            self._sync_parent()
        info = self.directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_flash_receipt_directory')

    def _sync_parent(self):
        fd = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def read(self, name):
        require(name in ('receipt.json', 'app-before.bin'), 'invalid_receipt_file')
        fd = os.open(self.directory / name, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_size <= MAX_APP_BYTES, 'unsafe_flash_receipt_file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                return stream.read(MAX_APP_BYTES + 1)
        finally:
            os.close(fd)

    def write(self, name, data, *, replace=False):
        require(name in ('receipt.json', 'app-before.bin') and type(data) is bytes,
                'invalid_receipt_write')
        target = self.directory / name
        temporary = target.with_name(name + '.' + uuid.uuid4().hex + '.pending') if replace else target
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        if replace:
            if target.exists():
                self.read(name)  # validate destination before replacing it
            os.replace(temporary, target)
        directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def save(self, state):
        self.write('receipt.json', (json.dumps(state, sort_keys=True, indent=2) + '\n').encode(),
                   replace=True)

    def state(self):
        return json.loads(self.read('receipt.json'))


def checked_read(rom, offset, size):
    data = bytearray()
    for start in range(offset, offset + size, 65536):
        length = min(65536, offset + size - start)
        one, two = rom.read(start, length), rom.read(start, length)
        require(type(one) is bytes and len(one) == length and one == two,
                'rollback_readback_mismatch')
        data.extend(one)
    return bytes(data)


def apply(rom, image, previous_image, receipt, binding):
    selected = plan(rom, image, previous_image)
    original = checked_read(rom, APP_OFFSET, selected['span'])
    require(md5(original) == selected['range_md5'], 'rollback_hash_mismatch')
    payload = image + original[len(image):]
    state = {'schema': 'meshticulum.app-flash-receipt.v1', 'phase': 'backed_up',
             'binding': binding, 'plan': selected, 'backup_sha256': sha(original),
             'installed_range_md5': md5(payload), 'bench_arm_commands': 0, 'bench_tx_commands': 0}
    receipt.write('app-before.bin', original)
    receipt.save(state)
    require(plan(rom, image, previous_image) == selected, 'flash_changed_after_backup')
    state['phase'] = 'writing'
    receipt.save(state)  # commit before any erase/write command
    try:
        rom.write(APP_OFFSET, payload)
        require(rom.md5(APP_OFFSET, len(payload)) == md5(payload), 'flashed_app_verification_failed')
        require(outside_hashes(rom, len(payload)) == selected['outside_md5'],
                'outside_app_changed')
        state['phase'] = 'flash_verified'
        receipt.save(state)
        return state
    except BaseException:
        try:
            rollback(rom, receipt, binding)
            if outside_hashes(rom, len(payload)) != selected['outside_md5']:
                recovered = receipt.state()
                recovered['phase'] = 'recovery_required'
                recovered['outside_app_changed_before_boot'] = True
                receipt.save(recovered)
        except BaseException:
            state['phase'] = 'recovery_required'
            receipt.save(state)
        raise


def rollback(rom, receipt, binding):
    state = receipt.state()
    require(state['binding'] == binding, 'rollback_wrong_device_or_bundle')
    require(not state.get('outside_app_changed_before_boot'),
            'outside_app_change_requires_operator_review')
    require(state['phase'] in ('writing', 'flash_verified', 'active_disarmed',
                              'recovery_required', 'rolling_back'), 'rollback_wrong_phase')
    selected = state['plan']
    require(rom.flash_size == selected['flash_size'], 'rollback_flash_size_changed')
    table = rom.read(TABLE_OFFSET, TABLE_SIZE)
    app, _ = partitions(table, rom.flash_size)
    require(sha(table) == selected['partition_sha256'] and app == selected['app'],
            'rollback_partition_changed')
    original = receipt.read('app-before.bin')
    require(sha(original) == state['backup_sha256'] and md5(original) == selected['range_md5']
            and len(original) == selected['span'] and len(original) <= app['size'],
            'rollback_backup_changed')
    if state['phase'] in ('flash_verified', 'active_disarmed'):
        require(rom.md5(APP_OFFSET, len(original)) == state['installed_range_md5'],
                'rollback_would_replace_an_unrelated_app')
    # A normal boot updates the bench NVS boot counter. Preserve current NVS,
    # OTA and private stores; compare their current hashes around rollback.
    before = outside_hashes(rom, len(original))
    state['phase'] = 'rolling_back'
    receipt.save(state)
    rom.write(APP_OFFSET, original)
    require(rom.md5(APP_OFFSET, len(original)) == selected['range_md5']
            and rom.md5(APP_OFFSET, selected['prior_bytes']) == selected['prior_md5'],
            'rollback_verification_failed')
    require(outside_hashes(rom, len(original)) == before, 'rollback_changed_outside_app')
    state['phase'] = 'rolled_back'
    receipt.save(state)
    return state
