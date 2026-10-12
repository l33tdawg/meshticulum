"""Typed, opt-in 125 kbps GFSK configuration and airtime; opens no hardware.

This record is deliberately distinct from the existing 30-byte LoRa ARM body.
Existing workers/admission continue to reject it until a reviewed GFSK adapter
binds the record, a finite authority and durable attempt accounting together.
Register encodings follow Semtech SX1261/2 section13.4 and RadioLib7.7.1.
"""
from dataclasses import dataclass
import struct


class GfskProfileError(ValueError):
    pass


def need(ok, reason):
    if not ok:
        raise GfskProfileError(reason)


RECORD = struct.Struct('!2sBIIIIbH8sBIIQ')
MAGIC, VERSION = b'GF', 1
BIT_RATE_BPS, DEVIATION_HZ, RX_BANDWIDTH_HZ = 125000, 31250, 312000
PREAMBLE_BITS, SYNC_WORD, SHAPING_CODE = 32, b'MTGFSK01', 0x09
CRC_BYTES, LENGTH_BYTES = 2, 1


@dataclass(frozen=True, slots=True)
class GfskProfile:
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
        need(type(board_id) is int and board_id in (1, 2), 'unsupported_gfsk_board')
        need(all(type(v) is int for v in (
            self.frequency_hz, self.chip_power_dbm, self.lease_ms, self.max_boot_attempts,
            self.max_boot_estimated_airtime_us, self.bit_rate_bps, self.deviation_hz,
            self.rx_bandwidth_hz, self.preamble_bits, self.shaping_code)), 'invalid_gfsk_integer')
        need((self.bit_rate_bps, self.deviation_hz, self.rx_bandwidth_hz,
              self.preamble_bits, self.shaping_code) ==
             (BIT_RATE_BPS, DEVIATION_HZ, RX_BANDWIDTH_HZ, PREAMBLE_BITS, SHAPING_CODE)
             and type(self.sync_word) is bytes and self.sync_word == SYNC_WORD,
             'unreviewed_gfsk_modulation')
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


def estimated_airtime_us(profile, size):
    """Ceiling of physical packet bits/bitrate, including variable length byte.

    Preamble is specified in bits; sync and CRC are bytes. The length byte is
    additional to the 1..255-byte ciphertext. Host/USB/SPI/RX turnaround are
    separate time budgets. No measured latency or delivery is implied.
    """
    need(type(profile) is GfskProfile, 'wrong_gfsk_profile_class')
    profile.encode(board_id=2)
    need(type(size) is int and 1 <= size <= 255, 'invalid_gfsk_payload_size')
    bits = profile.preamble_bits + 8 * (len(profile.sync_word) + LENGTH_BYTES + size + CRC_BYTES)
    return (bits * 1000000 + profile.bit_rate_bps - 1) // profile.bit_rate_bps


@dataclass(frozen=True, slots=True)
class GfskRegisterPlan:
    """Inert command/register values; no SPI transport or grant is present."""
    packet_type: bytes
    modulation: bytes
    packet: bytes
    sync_word: bytes
    crc_initial: bytes
    crc_polynomial: bytes
    whitening_initial: int


def sx1262_plan(profile, size=255):
    need(type(profile) is GfskProfile, 'wrong_gfsk_profile_class')
    profile.encode(board_id=2)
    estimated_airtime_us(profile, size)
    # SetModulationParams: BR24, Gaussian BT, RxBW, Fdev24.
    br = 32 * 32000000 // profile.bit_rate_bps
    dev = profile.deviation_hz * (1 << 25) // 32000000
    modulation = br.to_bytes(3, 'big') + bytes([SHAPING_CODE, 0x19]) + dev.to_bytes(3, 'big')
    # SetPacketParams: preamble_bits16, detector32, sync_bits64, no address,
    # variable-length header, payload length, CRC2 non-inverted, whitening on.
    packet = struct.pack('!HBBBBBBB', PREAMBLE_BITS, 0x07, 64, 0, 1, size, 0x02, 1)
    return GfskRegisterPlan(b'\0', modulation, packet, SYNC_WORD, b'\xff\xff', b'\x10\x21', 0x01FF)
