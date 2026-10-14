"""Explicit GFSK250 profile, distinct from the accepted GFSK125 contract.

GF record version 2 fixes 250 kbps, 62.5 kHz deviation and 467 kHz RX
bandwidth. It grants no RF authority. Existing GF version 1 never accepts it.
"""
from dataclasses import dataclass, asdict
import struct

from .gfsk_profile import GfskProfileError, GfskRegisterPlan, need, RECORD

MAGIC, VERSION = b'GF', 2
BIT_RATE_BPS, DEVIATION_HZ, RX_BANDWIDTH_HZ = 250000, 62500, 467000
PREAMBLE_BITS, SYNC_WORD, SHAPING_CODE = 32, b'MTGFSK01', 0x09
# Read-only firmware capability reply: record identity/size and fixed modulation.
CAPABILITY = b'GF\x02' + bytes([RECORD.size]) + struct.pack(
    '!III', BIT_RATE_BPS, DEVIATION_HZ, RX_BANDWIDTH_HZ)


@dataclass(frozen=True, slots=True)
class Gfsk250Profile:
    frequency_hz: int
    chip_power_dbm: int
    lease_ms: int
    max_boot_attempts: int
    max_boot_estimated_airtime_us: int
    bit_rate_bps: int = BIT_RATE_BPS
    deviation_hz: int = DEVIATION_HZ
    rx_bandwidth_hz: int = RX_BANDWIDTH_HZ
    preamble_bits: int = PREAMBLE_BITS
    sync_word: bytes = SYNC_WORD
    shaping_code: int = SHAPING_CODE

    def encode(self, *, board_id):
        need(type(self) is Gfsk250Profile, 'wrong_gfsk250_profile_class')
        need(type(board_id) is int and board_id in (1, 2), 'unsupported_gfsk_board')
        need(all(type(v) is int for v in (
            self.frequency_hz, self.chip_power_dbm, self.lease_ms, self.max_boot_attempts,
            self.max_boot_estimated_airtime_us, self.bit_rate_bps, self.deviation_hz,
            self.rx_bandwidth_hz, self.preamble_bits, self.shaping_code)), 'invalid_gfsk_integer')
        need((self.bit_rate_bps, self.deviation_hz, self.rx_bandwidth_hz,
              self.preamble_bits, self.shaping_code) ==
             (BIT_RATE_BPS, DEVIATION_HZ, RX_BANDWIDTH_HZ, PREAMBLE_BITS, SHAPING_CODE)
             and type(self.sync_word) is bytes and self.sync_word == SYNC_WORD,
             'unreviewed_gfsk250_modulation')
        need(902000000 + RX_BANDWIDTH_HZ // 2 <= self.frequency_hz
             <= 928000000 - RX_BANDWIDTH_HZ // 2
             and 0 <= self.chip_power_dbm <= (10 if board_id == 1 else 22)
             and 1000 <= self.lease_ms <= 300000
             and 1 <= self.max_boot_attempts <= 100000
             and 1 <= self.max_boot_estimated_airtime_us <= 300000000,
             'invalid_gfsk_bounds')
        return RECORD.pack(MAGIC, VERSION, self.frequency_hz, self.bit_rate_bps,
                           self.deviation_hz, self.rx_bandwidth_hz, self.chip_power_dbm,
                           self.preamble_bits, self.sync_word, self.shaping_code,
                           self.lease_ms, self.max_boot_attempts,
                           self.max_boot_estimated_airtime_us)

    @classmethod
    def decode(cls, data, *, board_id):
        need(type(data) is bytes and len(data) == RECORD.size, 'invalid_gfsk_record_size')
        magic, version, hz, br, dev, bw, power, preamble, sync, shaping, lease, count, budget = RECORD.unpack(data)
        need(magic == MAGIC and version == VERSION, 'wrong_gfsk_record_type')
        result = cls(hz, power, lease, count, budget, br, dev, bw, preamble, sync, shaping)
        result.encode(board_id=board_id)
        return result

    def to_dict(self):
        self.encode(board_id=2)
        value = asdict(self)
        value['sync_word_hex'] = value.pop('sync_word').hex()
        value['schema'] = 'meshticulum.gfsk250-profile.v2'
        return value

    @classmethod
    def from_dict(cls, value, *, board_id):
        fields = {'schema', 'frequency_hz', 'chip_power_dbm', 'lease_ms', 'max_boot_attempts',
                  'max_boot_estimated_airtime_us', 'bit_rate_bps', 'deviation_hz',
                  'rx_bandwidth_hz', 'preamble_bits', 'sync_word_hex', 'shaping_code'}
        need(type(value) is dict and set(value) == fields
             and value['schema'] == 'meshticulum.gfsk250-profile.v2', 'wrong_gfsk_record_schema')
        need(type(value['sync_word_hex']) is str and value['sync_word_hex'] == SYNC_WORD.hex(),
             'wrong_gfsk_sync_record')
        data = dict(value)
        data.pop('schema')
        data['sync_word'] = bytes.fromhex(data.pop('sync_word_hex'))
        profile = cls(**data)
        profile.encode(board_id=board_id)
        return profile


def estimated_airtime_us(profile, size):
    """Conservative packet bits/rate, not measured latency or delivery."""
    need(type(profile) is Gfsk250Profile, 'wrong_gfsk250_profile_class')
    profile.encode(board_id=2)
    need(type(size) is int and 1 <= size <= 255, 'invalid_gfsk_payload_size')
    bits = profile.preamble_bits + 8 * (len(profile.sync_word) + 1 + size + 2)
    return (bits * 1000000 + profile.bit_rate_bps - 1) // profile.bit_rate_bps


def sx1262_plan(profile, size=255):
    estimated_airtime_us(profile, size)
    br = 32 * 32000000 // profile.bit_rate_bps
    dev = profile.deviation_hz * (1 << 25) // 32000000
    modulation = br.to_bytes(3, 'big') + bytes([SHAPING_CODE, 0x09]) + dev.to_bytes(3, 'big')
    packet = struct.pack('!HBBBBBBB', PREAMBLE_BITS, 0x07, 64, 0, 1, size, 0x02, 1)
    return GfskRegisterPlan(b'\0', modulation, packet, SYNC_WORD, b'\xff\xff', b'\x10\x21', 0x01FF)
