"""Bounded host codec for the native bench coprocessor.

These checks bind local driver evidence to an exact boot/request/ciphertext.
They do not authenticate a remote peer, establish packet delivery or open a
serial port. The admitted exclusive worker must supply that separate authority.
"""
from dataclasses import dataclass, field
from enum import IntEnum
import hashlib
import hmac
import struct
import zlib

END, ESC, ESC_END, ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD
HEADER = struct.Struct('!2sBB16sQH')
MAX_BODY = 320
MAX_RAW = HEADER.size + MAX_BODY + 4
MAX_ENCODED = MAX_RAW * 2 + 2
MAX_READ = 4096
RF_TX_DONE, RF_TIMEOUT = 1, 0x200


class SerialError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise SerialError(code)


class Message(IntEnum):
    HELLO = 1
    ARM = 2
    TRANSMIT = 3
    STATUS = 4
    DISARM = 5
    HELLO_REPLY = 0x81
    ARM_REPLY = 0x82
    TX_RESULT = 0x83
    STATUS_REPLY = 0x84
    DISARM_REPLY = 0x85
    RECEIVE = 0x90
    REFUSED = 0xFF


class Outcome(IntEnum):
    STARTED = 1
    RF_DONE = 2
    UNKNOWN = 3


@dataclass(frozen=True, slots=True)
class Frame:
    type: Message
    boot: bytes
    request: int
    body: bytes = field(default=b'', repr=False)

    def __post_init__(self):
        require(type(self.type) is Message, 'unsupported_message')
        require(type(self.boot) is bytes and len(self.boot) == 16, 'invalid_boot')
        require(type(self.request) is int and 0 <= self.request < 2**64, 'invalid_request')
        require(type(self.body) is bytes and len(self.body) <= MAX_BODY, 'invalid_body')

    def encode(self):
        raw = HEADER.pack(b'MB', 1, self.type, self.boot, self.request, len(self.body)) + self.body
        raw += struct.pack('!I', zlib.crc32(raw))
        return bytes([END]) + raw.replace(bytes([ESC]), bytes([ESC, ESC_ESC])).replace(
            bytes([END]), bytes([ESC, ESC_END])) + bytes([END])


class Decoder:
    def __init__(self):
        self._buffer = bytearray()
        self._started = self._escaped = self._discarded = False
        self.rejected = 0

    def _finish(self):
        raw = self._buffer
        require(not self._escaped and not self._discarded and len(raw) >= HEADER.size + 4,
                'truncated_or_oversized')
        magic, version, kind, boot, request, size = HEADER.unpack_from(raw)
        require(magic == b'MB' and version == 1, 'unsupported_version')
        require(size <= MAX_BODY and len(raw) == HEADER.size + size + 4, 'size_mismatch')
        require(struct.unpack('!I', raw[-4:])[0] == zlib.crc32(raw[:-4]), 'crc_mismatch')
        require(kind in Message._value2member_map_, 'unsupported_message')
        return Frame(Message(kind), boot, request, bytes(raw[HEADER.size:-4]))

    def feed(self, data):
        require(type(data) is bytes and len(data) <= MAX_READ, 'invalid_read_size')
        frames = []
        for byte in data:
            if byte == END:
                if self._started and (self._buffer or self._escaped or self._discarded):
                    try:
                        frames.append(self._finish())
                    except SerialError:
                        self.rejected += 1
                self._buffer.clear()
                self._started = True
                self._escaped = self._discarded = False
            elif self._started and not self._discarded:
                if self._escaped:
                    self._escaped = False
                    if byte not in (ESC_END, ESC_ESC):
                        self._discarded = True
                        continue
                    byte = END if byte == ESC_END else ESC
                elif byte == ESC:
                    self._escaped = True
                    continue
                if len(self._buffer) == MAX_RAW:
                    self._discarded = True
                else:
                    self._buffer.append(byte)
        return frames


@dataclass(frozen=True, slots=True)
class FirmwareIdentity:
    boot: bytes
    board_id: int
    version: str

    @classmethod
    def from_reply(cls, frame, *, request, expected_board_id, expected_version):
        require(frame.type is Message.HELLO_REPLY and frame.request == request and request > 0,
                'wrong_hello_reply')
        require(len(frame.body) == 20, 'invalid_hello_size')
        board, ready, max_frame = struct.unpack('!BBH', frame.body[:4])
        version_bytes, separator, padding = frame.body[4:].partition(b'\0')
        require(separator and not any(padding), 'invalid_firmware_version')
        require(version_bytes == expected_version.encode('ascii'), 'wrong_firmware_version')
        require(board in (1, 2) and board == expected_board_id, 'wrong_board')
        require(ready == 1 and max_frame == 255, 'radio_not_ready')
        require(any(frame.boot[:8]) and int.from_bytes(frame.boot[8:], 'big') > 0, 'invalid_boot')
        return cls(frame.boot, board, expected_version)


@dataclass(frozen=True, slots=True)
class RadioProfile:
    frequency_hz: int
    bandwidth_hz: int
    spreading_factor: int
    coding_rate: int
    chip_power_dbm: int
    preamble: int
    sync_word: int
    lease_ms: int
    max_boot_attempts: int
    max_boot_estimated_airtime_us: int

    def encode(self, *, board_id):
        require(board_id in (1, 2), 'unsupported_board')
        require(all(type(value) is int for value in (
            self.frequency_hz, self.bandwidth_hz, self.spreading_factor, self.coding_rate,
            self.chip_power_dbm, self.preamble, self.sync_word, self.lease_ms,
            self.max_boot_attempts, self.max_boot_estimated_airtime_us)), 'invalid_profile_type')
        # Common supported bench hardware/calibration range, not permission
        # to transmit throughout this band. The admitted run pins its profile.
        require(self.bandwidth_hz in (62500, 125000, 250000, 500000)
                and 902000000 + self.bandwidth_hz // 2 <= self.frequency_hz
                <= 928000000 - self.bandwidth_hz // 2
                and 7 <= self.spreading_factor <= 12 and 5 <= self.coding_rate <= 8
                and 0 <= self.chip_power_dbm <= (10 if board_id == 1 else 22)
                and 8 <= self.preamble <= 64 and 1 <= self.sync_word <= 255
                and 1000 <= self.lease_ms <= 300000 and 1 <= self.max_boot_attempts <= 100000
                and 1 <= self.max_boot_estimated_airtime_us <= 300000000, 'invalid_profile')
        return struct.pack('!IIBBbHBIIQ', self.frequency_hz, self.bandwidth_hz,
                           self.spreading_factor, self.coding_rate, self.chip_power_dbm,
                           self.preamble, self.sync_word, self.lease_ms, self.max_boot_attempts,
                           self.max_boot_estimated_airtime_us)


@dataclass(frozen=True, slots=True)
class TxEvidence:
    outcome: Outcome
    estimated_airtime_us: int
    driver_elapsed_us: int
    irq_flags: int
    driver_error: int

    @classmethod
    def from_reply(cls, frame, *, boot, request, packet):
        require(frame.type is Message.TX_RESULT and frame.boot == boot and frame.request == request
                and request > 0 and len(frame.body) == 55, 'unbound_tx_result')
        require(type(packet) is bytes and 1 <= len(packet) <= 255, 'invalid_packet')
        outcome, digest, estimate, elapsed, flags, error = struct.unpack('!B32sQQIh', frame.body)
        require(hmac.compare_digest(digest, hashlib.sha256(packet).digest()), 'wrong_packet_digest')
        require(outcome in Outcome._value2member_map_ and 0 < estimate <= 300000000,
                'invalid_tx_evidence')
        if outcome == Outcome.RF_DONE:
            require(flags & RF_TX_DONE and not flags & RF_TIMEOUT and error == 0,
                    'contradictory_rf_completion')
        if outcome == Outcome.STARTED:
            require(elapsed == flags == error == 0, 'contradictory_tx_start')
        return cls(Outcome(outcome), estimate, elapsed, flags, error)


@dataclass(frozen=True, slots=True)
class RawReceive:
    rssi_dbm: float
    snr_db: float
    packet: bytes = field(repr=False)

    @classmethod
    def from_frame(cls, frame, *, boot):
        require(frame.type is Message.RECEIVE and frame.boot == boot and frame.request == 0
                and 5 <= len(frame.body) <= 259, 'invalid_receive')
        rssi, snr = struct.unpack('!hh', frame.body[:4])
        return cls(rssi / 10, snr / 10, frame.body[4:])
