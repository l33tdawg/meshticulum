"""One Linux owner for a pinned USB radio. Opening does not arm or transmit."""
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import select
import stat
import termios
import time

from .linux_hat import HardwareError, _existing_openers, require


def usb_identity(tty):
    path = (Path('/sys/class/tty') / tty / 'device').resolve(strict=True)
    for parent in [path, *path.parents]:
        if (parent / 'idVendor').exists():
            return tuple((parent / name).read_text().strip()
                         for name in ('idVendor', 'idProduct', 'serial'))
    raise HardwareError('missing_usb_identity')


class ExclusiveSerial:
    monotonic = staticmethod(time.monotonic)

    def __init__(self, by_id, *, usb_serial):
        self.fd = self.lock_fd = None
        require(platform.system() == 'Linux' and os.geteuid() == 0, 'requires_linux_root_owner')
        path = Path(by_id)
        require(path.parent == Path('/dev/serial/by-id'), 'requires_stable_by_id_path')
        resolved = path.resolve(strict=True)
        require(resolved.parent == Path('/dev') and resolved.name.startswith('ttyACM'), 'wrong_serial_node')
        expected = ('303a', '1001', usb_serial)
        require(usb_identity(resolved.name) == expected, 'wrong_usb_radio')
        try:
            lock_dir = Path('/run/meshticulum-bench')
            lock_dir.mkdir(mode=0o700, exist_ok=True)
            info = lock_dir.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                    and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_owner_directory')
            name = hashlib.sha256(usb_serial.encode('ascii')).hexdigest() + '.lock'
            self.lock_fd = os.open(lock_dir / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            info = os.fstat(self.lock_fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_owner_lock')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = resolved.lstat()
            require(stat.S_ISCHR(info.st_mode) and info.st_uid == 0, 'unsafe_serial_node')
            require(not _existing_openers(info), 'radio_already_open')
            self.fd = os.open(resolved, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
            require(os.fstat(self.fd).st_rdev == info.st_rdev, 'serial_changed_during_open')
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(not _existing_openers(info) and usb_identity(resolved.name) == expected,
                    'radio_owner_or_identity_changed')
            attrs = termios.tcgetattr(self.fd)
            attrs[:6] = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0,
                         termios.B115200, termios.B115200]
            attrs[6][termios.VMIN], attrs[6][termios.VTIME] = 0, 0
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
        except BaseException:
            self.close()
            raise

    def write(self, data, deadline):
        require(type(data) is bytes and 0 < len(data) <= 710, 'invalid_serial_write')
        view = memoryview(data)
        while view:
            remaining = deadline - self.monotonic()
            require(remaining > 0, 'serial_write_deadline')
            require(select.select([], [self.fd], [], remaining)[1], 'serial_write_deadline')
            try:
                # ESP32-S3 USB CDC ignores the nominal UART baud rate. The
                # retained MCU core has a 256-byte RX queue and drops overflow
                # in its USB ISR. Pace whole encoded frames, including SLIP
                # expansion, so a full 255-byte radio packet reaches the MCU.
                size = os.write(self.fd, view[:64])
            except BlockingIOError:
                continue
            require(size > 0, 'serial_write_disconnected')
            view = view[size:]
            if view:
                remaining = deadline - self.monotonic()
                # Both deployed MCU radios passed eighty worst-case escaped
                # STATUS frames at this gap without dropped bytes. Keep USB
                # packet boundaries; a whole-frame burst still overflows.
                require(remaining > .002, 'serial_write_deadline')
                time.sleep(.002)

    def read(self, timeout):
        require(0 <= timeout <= 30, 'invalid_serial_timeout')
        if not select.select([self.fd], [], [], timeout)[0]:
            return b''
        try:
            data = os.read(self.fd, 4096)
        except BlockingIOError:
            return b''
        require(bool(data), 'serial_read_disconnected')
        return data

    def close(self):
        for name in ('fd', 'lock_fd'):
            fd = getattr(self, name, None)
            setattr(self, name, None)
            if fd is not None:
                os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()
