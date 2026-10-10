"""Configure C's SX1262 and return to standby, with SetRx/SetTx denied at SPI.

This is a hardware configuration check, not an RF test or a transmission grant.
The finite benchmark runtime must separately supply authority and antenna state.
"""
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meshticulum_bench.linux_hat import HardwareError, LinuxHAT, require
from meshticulum_bench.serial_protocol import RadioProfile
from meshticulum_bench.sx1262 import SX1262


class NoRFTxRx:
    ALLOWED = frozenset((0xC0, 0x80, 0x1D, 0x0D, 0x8A, 0x11, 0x96, 0x07,
        0x89, 0x98, 0x17, 0x86, 0x9D, 0x95, 0x8E, 0x8B, 0x8C, 0x8F, 0x93, 0x02, 0x12))

    def __init__(self, device):
        self.device = device
        self.commands = Counter()
        self.trace = []

    def __getattr__(self, name):
        return getattr(self.device, name)

    def transfer(self, data):
        require(type(data) is bytes and bool(data) and data[0] in self.ALLOWED,
                'configuration_probe_opcode_refused')
        self.commands[data[0]] += 1
        response = self.device.transfer(data)
        # Only configuration commands are admitted, so this contains no received
        # packets, application bytes, private keys or GNSS data.
        self.trace.append({'command': data.hex(), 'response': response.hex()})
        self.trace = self.trace[-32:]
        return response


def main():
    # A frequency register setting only. On-air country/antenna checks remain
    # pending; the transport guard makes either RF mode command impossible here.
    profile = RadioProfile(921375000, 125000, 8, 5, 0, 8, 0x42, 1000, 1, 1000000)
    with LinuxHAT() as device:
        guarded = NoRFTxRx(device)
        radio = SX1262(guarded)
        result = {'schema': 'meshticulum.sx1262-configuration-check.v1',
                  'host_boot_id': device.boot_id, 'profile': asdict(profile),
                  'configured': False, 'rf_path_verified': False}
        try:
            radio.configure(profile)
            result.update({
                'status': radio.status(), 'sync_word': radio._register(0x0740, 2).hex(),
                'device_errors': radio._read(0x17, 2).hex(),
                'configured': True})
        except HardwareError as error:
            result['error'] = str(error)
        finally:
            radio.close()
        if not radio.fenced:
            result['final_status'] = radio.status()
            require((result['final_status'] >> 4) & 7 == 2, 'probe_not_in_final_standby')
        result['fenced'] = radio.fenced
        result['rf_transmit_commands'] = guarded.commands[0x83]
        result['rf_receive_commands'] = guarded.commands[0x82]
        result['configuration_trace'] = guarded.trace
        result['spi_command_counts'] = {f'{opcode:02x}': count
                                        for opcode, count in sorted(guarded.commands.items())}
    result['descriptors_released'] = True
    print(json.dumps(result))
    return 0 if result['configured'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
