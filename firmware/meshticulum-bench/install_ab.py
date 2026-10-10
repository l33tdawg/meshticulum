#!/usr/bin/env python3
"""Root operator entry point for the frozen A/B app-only bench bundle.

Invoked from a hash-verified root-owned bundle with an isolated Python that has
esptool 4.x and pyserial installed. Does not install services, change sudo/SSH
access, open private SAGE stores, arm the radio or transmit bench packets.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import sys
import termios
import time

BUNDLE = Path(__file__).resolve().parent
sys.path.insert(0, str(BUNDLE))

from meshticulum_bench import flash_app
from meshticulum_bench.linux_hat import HardwareError, _existing_openers, require
from meshticulum_bench.serial_port import usb_identity
from meshticulum_bench.serial_protocol import Decoder, FirmwareIdentity, Frame, Message

UNITS = {'unit-a': 'F8:5B:1B:A5:F6:08', 'unit-b': '44:1B:F6:62:56:54'}


def trusted_file(path, *, limit=8 * 1024 * 1024):
    path = Path(path)
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and not info.st_mode & 0o022, 'untrusted_root_file_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= limit, 'untrusted_root_file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(limit + 1)
    finally:
        os.close(fd)


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_private_directory')


def verify_bundle(unit):
    raw = trusted_file(BUNDLE / 'manifest.json')
    manifest = json.loads(raw)
    require(manifest['schema'] == 'meshticulum.ab-flash-bundle.v1'
            and manifest['units'] == UNITS, 'wrong_bundle')
    for name, expected in manifest['files'].items():
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts, 'invalid_bundle_member')
        require(hashlib.sha256(trusted_file(BUNDLE / relative)).hexdigest() == expected,
                'bundle_source_or_image_changed')
    operator = json.loads(trusted_file('/etc/sage-lamplighter-lab/operator.json', limit=16384))
    require(operator['unit'] == unit and Path('/etc/sage-lamplighter-lab/enabled').is_file(),
            'wrong_unit_or_public_carrier_role')
    import subprocess
    for service in ('sage-lantern-mesh-runtime.service', 'sage-lantern-personal-sage.service',
                    'sage-lantern-voice-loop.service', 'sage-lantern-llm.service'):
        result = subprocess.run(['/usr/bin/systemctl', 'show', service, '-p', 'ActiveState',
                                 '-p', 'MainPID'], capture_output=True, text=True, timeout=5)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        require(result.returncode == 0 and state.get('ActiveState') in ('inactive', 'failed')
                and state.get('MainPID') == '0', 'legacy_private_or_radio_service_active')
    return manifest, {'unit': unit, 'usb_serial': UNITS[unit],
                      'bundle_manifest_sha256': hashlib.sha256(raw).hexdigest()}


class PinnedROM:
    """Retains the same owner lock and serial FD throughout each ROM operation."""
    def __init__(self, usb_serial):
        import esptool
        import serial
        from esptool.targets.esp32s3 import ESP32S3ROM
        require(esptool.__version__.startswith('4.'), 'requires_reviewed_esptool_4_api')
        self.esptool_version = esptool.__version__
        self.serial_class, self.rom_class = serial.Serial, ESP32S3ROM
        self.serial_number, self.port, self.rom, self.lock = usb_serial, None, None, None
        self.original_mode = None
        self.path = Path('/dev/serial/by-id') / (
            'usb-Espressif_USB_JTAG_serial_debug_unit_' + usb_serial + '-if00')
        try:
            directory = Path('/run/meshticulum-bench')
            private_directory(directory)
            name = hashlib.sha256(usb_serial.encode('ascii')).hexdigest() + '.lock'
            self.lock = os.open(directory / name,
                                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            info = os.fstat(self.lock)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_usb_owner_lock')
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.open_port()
        except BaseException:
            self.close()
            raise

    def node(self):
        resolved = self.path.resolve(strict=True)
        require(resolved.parent == Path('/dev') and resolved.name.startswith('ttyACM'), 'wrong_tty')
        require(usb_identity(resolved.name) == ('303a', '1001', self.serial_number), 'wrong_usb_identity')
        info = resolved.lstat()
        require(stat.S_ISCHR(info.st_mode) and info.st_uid == 0, 'unsafe_tty_owner')
        return resolved, info

    def open_port(self):
        if self.port is not None:
            self.port.close()
            self.port = None
        resolved, info = self.node()
        require(not _existing_openers(info), 'radio_already_owned')
        if self.original_mode is None:
            self.original_mode = stat.S_IMODE(info.st_mode)
        os.chmod(resolved, 0o600)  # close ordinary-user open races during the flash window
        self.port = self.serial_class(str(resolved), 115200, timeout=0.1,
                                      write_timeout=3, exclusive=True)
        fd = self.port.fileno()
        fcntl.ioctl(fd, termios.TIOCEXCL)
        require(os.fstat(fd).st_rdev == info.st_rdev and not _existing_openers(info),
                'radio_identity_or_owner_changed')
        self.node()

    def connect(self):
        self.node()
        self.rom = self.rom_class(self.port, baud=115200)
        self.rom.connect(mode='usb_reset', attempts=3)
        require(self.rom.get_chip_id() == 9 and not self.rom.IS_STUB
                and not self.rom.secure_download_mode, 'wrong_chip_or_loader')
        require(':'.join(f'{b:02X}' for b in self.rom.read_mac()) == self.serial_number,
                'efuse_mac_does_not_match_usb_pin')
        require(not self.rom.get_secure_boot_enabled() and not self.rom.get_flash_encryption_enabled(),
                'secured_flash_requires_separate_review')
        self.rom.flash_spi_attach(0)
        capacity_code = self.rom.flash_id() >> 16
        require(20 <= capacity_code <= 25, 'unknown_flash_capacity')
        self.flash_size = 1 << capacity_code
        self.rom.flash_set_parameters(self.flash_size)
        self.node()

    def read(self, offset, size):
        require(0 <= offset and 0 < size <= 65536 and offset + size <= self.flash_size,
                'invalid_flash_read_bounds')
        data = self.rom.read_flash(offset, size)
        require(type(data) is bytes and len(data) == size, 'short_flash_read')
        return data

    def md5(self, offset, size):
        require(0 <= offset and size > 0 and offset + size <= self.flash_size,
                'invalid_flash_hash_bounds')
        value = self.rom.flash_md5sum(offset, size)
        require(type(value) is str and len(value) == 32
                and all(c in '0123456789abcdef' for c in value), 'invalid_rom_digest')
        return value

    def write(self, offset, data):
        require(offset == flash_app.APP_OFFSET and type(data) is bytes
                and 0 < len(data) <= flash_app.MAX_APP_BYTES and len(data) % flash_app.SECTOR == 0,
                'write_outside_app')
        block_size = self.rom.FLASH_WRITE_SIZE
        require(flash_app.SECTOR % block_size == 0, 'unexpected_rom_write_block')
        blocks = self.rom.flash_begin(len(data), offset)
        require(blocks == len(data) // block_size, 'unexpected_rom_write_length')
        for number in range(blocks):
            self.rom.flash_block(data[number * block_size:(number + 1) * block_size], number)
        # Stay in ROM for hash verification. No flash_finish/app launch yet.

    def reset_app(self):
        self.rom.hard_reset()
        self.rom = None
        time.sleep(1.0)

    def probe_app(self, version):
        # Reopen if USB re-enumerated, retaining the private owner lock.
        self.open_port()
        decoder = Decoder()
        request = time.time_ns() // 1000
        identity = None
        self.port.reset_input_buffer()
        self.port.write(Frame(Message.HELLO, bytes(16), request).encode())
        end = time.monotonic() + 8
        while time.monotonic() < end:
            for frame in decoder.feed(bytes(self.port.read(512))):
                if frame.type is Message.HELLO_REPLY and frame.request == request:
                    identity = FirmwareIdentity.from_reply(frame, request=request, expected_board_id=1,
                                                           expected_version=version)
                    expected_mac = bytes(2) + bytes.fromhex(self.serial_number.replace(':', ''))[::-1]
                    require(identity.boot[:8] == expected_mac, 'wrong_mcu_boot_mac')
                    self.port.write(Frame(Message.STATUS, identity.boot, request + 1).encode())
                elif identity is not None and frame.type is Message.STATUS_REPLY and frame.request == request + 1:
                    require(frame.boot == identity.boot and len(frame.body) == 49
                            and frame.body[30:42] == bytes(12) and frame.body[46:] == bytes(3),
                            'bench_boot_not_fresh_and_disarmed')
                    return {'firmware': identity.version, 'mcu_boot': identity.boot.hex(),
                            'board_id': identity.board_id, 'disarmed': True,
                            'bench_arm_commands': 0, 'bench_tx_commands': 0}
        raise HardwareError('bench_hello_or_status_timeout')

    def close(self):
        if self.port is not None:
            self.port.close()
            self.port = None
        if self.original_mode is not None:
            try:
                resolved, _ = self.node()
                os.chmod(resolved, self.original_mode)
            except (OSError, HardwareError):
                pass  # absent device is recorded by the failed operation; never chmod a different radio
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('flash', 'rollback', 'status'))
    parser.add_argument('--unit', required=True, choices=UNITS)
    args = parser.parse_args()
    require(platform.system() == 'Linux' and os.geteuid() == 0 and sys.flags.isolated,
            'requires_isolated_linux_root_operator')
    os.umask(0o077)
    manifest, binding = verify_bundle(args.unit)
    root = Path('/var/lib/meshticulum-bench')
    private_directory(root)
    private_directory(root / 'flash')
    path = root / 'flash' / (args.unit + '-' + binding['bundle_manifest_sha256'][:16])
    if args.action == 'status':
        state = flash_app.Receipt(path).state()
        print(json.dumps({'phase': state['phase'], 'binding': state['binding'],
                          'receipt': str(path / 'receipt.json'), 'application': state.get('application')}))
        return
    receipt = None
    if args.action == 'flash':
        require(not path.exists(), 'flash_receipt_already_exists_use_status_or_rollback')
    else:
        receipt = flash_app.Receipt(path)
        require(receipt.state()['binding'] == binding, 'rollback_wrong_device_or_bundle')
    with PinnedROM(binding['usb_serial']) as rom:
        try:
            rom.connect()
            if args.action == 'rollback':
                state = flash_app.rollback(rom, receipt, binding)
                rom.reset_app()
            else:
                image = trusted_file(BUNDLE / 'firmware.bin')
                previous = trusted_file(BUNDLE / 'rollback-r7.bin')
                flash_app.plan(rom, image, previous)  # preflight before creating a transaction
                receipt = flash_app.Receipt(path, create=True)
                state = flash_app.apply(rom, image, previous, receipt, binding)
                rom.reset_app()
                state['application'] = rom.probe_app(manifest['firmware_identity'])
                state['phase'] = 'active_disarmed'
                state['esptool_version'] = rom.esptool_version
                state['host_boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                receipt.save(state)
            print(json.dumps({'phase': state['phase'], 'unit': args.unit,
                'receipt': str(path / 'receipt.json'), 'application': state.get('application'),
                'bench_arm_commands': 0, 'bench_tx_commands': 0}))
        except BaseException:
            state = receipt.state() if receipt is not None and (path / 'receipt.json').exists() else None
            if state and state['phase'] == 'flash_verified':
                # A verified flash whose boot check failed is restored, preserving
                # any NVS boot-counter update from that attempted boot.
                try:
                    if rom.rom is None:
                        rom.open_port()
                        rom.connect()
                    flash_app.rollback(rom, receipt, binding)
                    state = receipt.state()
                except BaseException:
                    state['phase'] = 'recovery_required'
                    receipt.save(state)
            if rom.rom is not None and (state is None or state['phase'] in ('backed_up', 'rolled_back')):
                rom.reset_app()
            raise


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__, 'error': str(error)}), file=sys.stderr)
        raise SystemExit(1)
