"""Linux SPI/GPIO transport for the confirmed Orange Pi 5 Plus SX1262 HAT.

Uses the kernel SPI controller (not GPIO bit banging) and GPIO chardev v2.
The exact ioctl layouts are checked against C's installed Linux headers by
firmware/meshticulum-bench/check_linux_abi.c before deploying this module.
Only the bench root owner may open it; GPIO requests are kernel-exclusive.
Root remains trusted: Linux spidev cannot exclude a hostile privileged opener.
"""
import ctypes
import fcntl
import os
from pathlib import Path
import platform
import stat
import struct
import time


class HardwareError(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise HardwareError(reason)


def ioc(direction, group, number, size):
    return (direction << 30) | (size << 16) | (ord(group) << 8) | number


class Attribute(ctypes.Structure):
    _fields_ = [('id', ctypes.c_uint32), ('padding', ctypes.c_uint32),
                ('value', ctypes.c_uint64), ('mask', ctypes.c_uint64)]


class LineConfig(ctypes.Structure):
    _fields_ = [('flags', ctypes.c_uint64), ('num_attrs', ctypes.c_uint32),
                ('padding', ctypes.c_uint32 * 5), ('attrs', Attribute * 10)]


class LineRequest(ctypes.Structure):
    _fields_ = [('offsets', ctypes.c_uint32 * 64), ('consumer', ctypes.c_char * 32),
                ('config', LineConfig), ('num_lines', ctypes.c_uint32),
                ('event_buffer_size', ctypes.c_uint32), ('padding', ctypes.c_uint32 * 5),
                ('fd', ctypes.c_int32)]


class SPITransfer(ctypes.Structure):
    _fields_ = [('tx_buf', ctypes.c_uint64), ('rx_buf', ctypes.c_uint64),
                ('len', ctypes.c_uint32), ('speed_hz', ctypes.c_uint32),
                ('delay_usecs', ctypes.c_uint16), ('bits_per_word', ctypes.c_uint8),
                ('cs_change', ctypes.c_uint8), ('tx_nbits', ctypes.c_uint8),
                ('rx_nbits', ctypes.c_uint8), ('word_delay_usecs', ctypes.c_uint8),
                ('pad', ctypes.c_uint8)]


GPIO_GET_CHIP = ioc(2, '\xb4', 1, 68)
GPIO_GET_LINES = ioc(3, '\xb4', 7, ctypes.sizeof(LineRequest))
GPIO_GET_VALUES = ioc(3, '\xb4', 14, 16)
GPIO_SET_VALUES = ioc(3, '\xb4', 15, 16)
SPI_MESSAGE = ioc(1, 'k', 0, ctypes.sizeof(SPITransfer))
SPI_WRITE_MODE = ioc(1, 'k', 1, 1)
SPI_WRITE_BITS = ioc(1, 'k', 3, 1)
SPI_WRITE_HZ = ioc(1, 'k', 4, 4)


def abi_manifest():
    return {'gpio_request_size': ctypes.sizeof(LineRequest),
            'gpio_config_size': ctypes.sizeof(LineConfig),
            'gpio_request_config_offset': LineRequest.config.offset,
            'gpio_request_fd_offset': LineRequest.fd.offset,
            'spi_transfer_size': ctypes.sizeof(SPITransfer),
            'gpio_get_chip': GPIO_GET_CHIP, 'gpio_get_lines': GPIO_GET_LINES,
            'gpio_get_values': GPIO_GET_VALUES, 'gpio_set_values': GPIO_SET_VALUES,
            'spi_message': SPI_MESSAGE, 'spi_write_mode': SPI_WRITE_MODE,
            'spi_write_bits': SPI_WRITE_BITS, 'spi_write_hz': SPI_WRITE_HZ}


def gpio_request():
    request = LineRequest()
    request.offsets[:4] = [1, 0, 4, 5]  # reset, external RF switch, busy, DIO1
    request.consumer = b'meshticulum-bench'
    request.num_lines = 4
    request.fd = -1
    request.config.flags = 4  # GPIO_V2_LINE_FLAG_INPUT
    request.config.num_attrs = 2
    request.config.attrs[0] = Attribute(1, 0, 8, 3)  # first two lines OUTPUT
    request.config.attrs[1] = Attribute(2, 0, 3, 3)  # reset HIGH, RF switch RX
    return request


def _existing_openers(device_stat):
    """Root-only best-effort collision check in addition to ownership locks."""
    owners = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            for fd in (proc / 'fd').iterdir():
                try:
                    info = fd.stat()
                    if stat.S_ISCHR(info.st_mode) and info.st_rdev == device_stat.st_rdev:
                        owners.append(int(proc.name))
                        break
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        except PermissionError:
            raise HardwareError('cannot_inspect_radio_ownership') from None
    return owners


class LinuxHAT:
    """Fixed, independently identified C binding; no configurable arbitrary pins."""
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)

    def __init__(self):
        self.spi_fd = self.chip_fd = self.lines_fd = self.lock_fd = None
        require(platform.system() == 'Linux' and platform.machine() == 'aarch64',
                'requires_verified_aarch64_linux')
        require(os.geteuid() == 0, 'requires_root_radio_owner')
        compatible = Path('/proc/device-tree/compatible').read_bytes().split(b'\0')
        require(b'rockchip,rk3588-orangepi-5-plus' in compatible, 'wrong_host_board')
        spi_node = Path('/sys/bus/spi/devices/spi0.0/of_node').resolve(strict=True)
        require(str(spi_node).endswith('/spi@feb00000/spidev@0'), 'wrong_spi_controller')
        cs = (spi_node.parent / 'cs-gpios').read_bytes()
        gpio3 = Path('/proc/device-tree/pinctrl/gpio@fec40000/phandle').read_bytes()
        require(cs == gpio3 + struct.pack('>II', 3, 1), 'wrong_kernel_chip_select')
        require((spi_node / 'spi-max-frequency').read_bytes() == struct.pack('>I', 1000000),
                'unexpected_spi_speed_limit')
        self.boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        try:
            lock_dir = Path('/run/meshticulum-bench')
            lock_dir.mkdir(mode=0o700, exist_ok=True)
            info = lock_dir.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                    and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_owner_directory')
            self.lock_fd = os.open(lock_dir / 'spi0.0.lock',
                                   os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            info = os.fstat(self.lock_fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                    and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe_owner_lock')
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = Path('/dev/spidev0.0').lstat()
            require(stat.S_ISCHR(info.st_mode) and info.st_uid == 0
                    and not stat.S_IMODE(info.st_mode) & 0o077, 'spi_not_private_to_root')
            require(not _existing_openers(info), 'spi_already_open')
            self.spi_fd = os.open('/dev/spidev0.0', os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            require(os.fstat(self.spi_fd).st_rdev == info.st_rdev, 'spi_changed_during_open')
            fcntl.flock(self.spi_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(not _existing_openers(info), 'spi_open_collision')
            self.chip_fd = os.open('/dev/gpiochip3', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            chip = bytearray(68)
            fcntl.ioctl(self.chip_fd, GPIO_GET_CHIP, chip)
            name, label, count = struct.unpack('=32s32sI', chip)
            require(label.rstrip(b'\0') == b'gpio3' and count == 32, 'wrong_gpio_bank')
            request = gpio_request()
            fcntl.ioctl(self.chip_fd, GPIO_GET_LINES, request)
            require(request.fd >= 0, 'gpio_request_failed')
            self.lines_fd = request.fd
            os.set_inheritable(self.lines_fd, False)
            fcntl.ioctl(self.spi_fd, SPI_WRITE_MODE, bytes([0]))
            fcntl.ioctl(self.spi_fd, SPI_WRITE_BITS, bytes([8]))
            fcntl.ioctl(self.spi_fd, SPI_WRITE_HZ, struct.pack('=I', 1000000))
        except BaseException:
            self.close()
            raise

    def transfer(self, data):
        require(type(data) is bytes and 1 <= len(data) <= 260, 'invalid_spi_transfer')
        require(self.spi_fd is not None, 'closed_spi')
        tx = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
        rx = (ctypes.c_uint8 * len(data))()
        message = SPITransfer(ctypes.addressof(tx), ctypes.addressof(rx), len(data),
                              1000000, 0, 8, 0, 0, 0, 0, 0)
        sent = fcntl.ioctl(self.spi_fd, SPI_MESSAGE, message)
        require(sent == len(data), 'partial_spi_transfer')
        return bytes(rx)

    def _set(self, mask, values):
        require(self.lines_fd is not None, 'closed_gpio')
        fcntl.ioctl(self.lines_fd, GPIO_SET_VALUES, struct.pack('=QQ', values, mask))

    def reset(self, high):
        require(type(high) is bool, 'invalid_reset_value')
        self._set(1, int(high))

    def receive_switch(self, receive):
        require(type(receive) is bool, 'invalid_rf_switch_value')
        self._set(2, int(receive) << 1)

    def busy(self):
        values = bytearray(struct.pack('=QQ', 0, 4))
        fcntl.ioctl(self.lines_fd, GPIO_GET_VALUES, values)
        return bool(struct.unpack('=QQ', values)[0] & 4)

    def close(self):
        # No SPI commands from teardown; higher driver owns bounded standby.
        for field in ('lines_fd', 'chip_fd', 'spi_fd', 'lock_fd'):
            fd = getattr(self, field, None)
            setattr(self, field, None)
            if fd is not None:
                os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()
