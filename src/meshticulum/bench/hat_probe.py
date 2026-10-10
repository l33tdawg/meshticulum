"""Bounded SX126x register/mode probe. Contains no RF transmit command.

Model identification comes from the owner's HAT photo, not from a unique chip
ID (SX1261 and SX1262 share this command interface). This proves host/chip SPI
communication and crystal startup; it does not prove an antenna or RF path.
"""
import json

from .linux_hat import LinuxHAT, HardwareError, require


class Probe:
    def __init__(self, transport):
        self.io = transport

    def wait_ready(self):
        deadline = self.io.monotonic() + 0.150
        while self.io.busy():
            require(self.io.monotonic() < deadline, 'radio_busy_timeout')
            self.io.sleep(0.0002)

    def transfer(self, command):
        require(command[0] in (0xC0, 0x80, 0x1D, 0x0D), 'probe_opcode_refused')
        self.wait_ready()
        received = self.io.transfer(command)
        require(type(received) is bytes and len(received) == len(command), 'short_spi_reply')
        self.io.sleep(0.0002)
        self.wait_ready()
        return received

    def status(self, expected_mode):
        value = self.transfer(b'\xc0\0')[1]
        require(value & 0x81 == 0 and (value >> 4) & 7 == expected_mode,
                'unexpected_chip_mode')
        require((value >> 1) & 7 not in (3, 4, 5), 'chip_command_error')
        return value

    def standby(self, xosc=False):
        self.transfer(bytes([0x80, int(xosc)]))
        return self.status(3 if xosc else 2)

    def sync_word(self):
        return self.transfer(b'\x1d\x07\x40\0\0\0')[4:]

    def write_sync_word(self, value):
        self.transfer(b'\x0d\x07\x40' + value)

    def run(self):
        self.io.receive_switch(True)
        self.io.reset(False)
        self.io.sleep(0.002)
        self.io.reset(True)
        self.io.sleep(0.010)
        original = None
        try:
            rc_status = self.standby()
            xosc_status = self.standby(True)
            original = self.sync_word()
            # Alternating values defeat all-zero/all-one MISO and stale readback.
            for word in (b'\x14\x24', b'\x34\x44'):
                self.write_sync_word(word)
                require(self.sync_word() == word, 'register_readback_mismatch')
            self.write_sync_word(original)
            require(self.sync_word() == original, 'register_restore_failed')
            final_status = self.standby()
            return {'spi_command_response': True, 'crystal_standby_observed': True,
                    'register_roundtrip': True, 'original_sync_word': original.hex(),
                    'standby_rc_status': rc_status, 'standby_xosc_status': xosc_status,
                    'final_standby_status': final_status, 'rf_transmissions': 0,
                    'rf_path_verified': False}
        except BaseException:
            try:
                if original is not None:
                    self.write_sync_word(original)
                self.standby()
            except (OSError, HardwareError):
                pass
            raise


def main():
    with LinuxHAT() as io:
        result = Probe(io).run()
        result['host_boot_id'] = io.boot_id
        result['binding'] = 'Orange Pi 5 Plus SPI0_M2; GPIO3_A3 NSS; Waveshare SX1262 HF'
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
