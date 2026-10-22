"""Reusable offline contract fixture for a new LoRa backend adapter.

This module models the transport boundary. It opens no port, socket or radio
and cannot establish board compatibility, RF completion or delivery.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import struct

from meshticulum.bench.serial_protocol import (
    Decoder,
    Frame,
    Message,
    Outcome,
    RadioProfile,
)
from meshticulum.bench.serial_worker import estimated_airtime_us


@dataclass(frozen=True, slots=True)
class BackendBounds:
    """Explicit board/backend profile bounds; never inferred from country."""

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

    def profile(self, *, board_id):
        profile = RadioProfile(
            self.frequency_hz,
            self.bandwidth_hz,
            self.spreading_factor,
            self.coding_rate,
            self.chip_power_dbm,
            self.preamble,
            self.sync_word,
            self.lease_ms,
            self.max_boot_attempts,
            self.max_boot_estimated_airtime_us,
        )
        profile.encode(board_id=board_id)
        return profile


@dataclass(frozen=True, slots=True)
class BackendDescriptor:
    """Minimum identity a backend provides before offline conformance tests."""

    backend_name: str
    transport_kind: str
    board_id: int
    firmware: str
    boot_id: bytes
    bounds: BackendBounds

    def __post_init__(self):
        if not (type(self.backend_name) is str and 1 <= len(self.backend_name) <= 64
                and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', self.backend_name)):
            raise ValueError('invalid_backend_name')
        if self.transport_kind not in ('serial', 'spi', 'other'):
            raise ValueError('invalid_backend_transport_kind')
        if self.board_id not in (1, 2):
            raise ValueError('unsupported_backend_board_id')
        if not (type(self.firmware) is str and 1 <= len(self.firmware) <= 64
                and len(self.firmware.encode('ascii', errors='ignore')) <= 15
                and self.firmware.isascii() and '\\0' not in self.firmware):
            raise ValueError('invalid_backend_firmware')
        if not (type(self.boot_id) is bytes and len(self.boot_id) == 16
                and any(self.boot_id) and int.from_bytes(self.boot_id[8:], 'big') > 0):
            raise ValueError('invalid_backend_boot_id')
        if type(self.bounds) is not BackendBounds:
            raise ValueError('invalid_backend_bounds')
        self.bounds.profile(board_id=self.board_id)

    @property
    def profile(self):
        return self.bounds.profile(board_id=self.board_id)

    def record(self):
        return {
            'schema': 'meshticulum.backend-descriptor.v1',
            'backend_name': self.backend_name,
            'transport_kind': self.transport_kind,
            'board_id': self.board_id,
            'firmware': self.firmware,
            'boot_id_hex': self.boot_id.hex(),
            'bounds': {
                'frequency_hz': self.bounds.frequency_hz,
                'bandwidth_hz': self.bounds.bandwidth_hz,
                'spreading_factor': self.bounds.spreading_factor,
                'coding_rate': self.bounds.coding_rate,
                'chip_power_dbm': self.bounds.chip_power_dbm,
                'preamble': self.bounds.preamble,
                'sync_word': self.bounds.sync_word,
                'lease_ms': self.bounds.lease_ms,
                'max_boot_attempts': self.bounds.max_boot_attempts,
                'max_boot_estimated_airtime_us':
                    self.bounds.max_boot_estimated_airtime_us,
            },
        }


class FakeBackendChannel:
    """Deterministic fake of the serial channel consumed by SerialWorker."""

    def __init__(self, descriptor, *, fault=None):
        if type(descriptor) is not BackendDescriptor:
            raise ValueError('invalid_backend_descriptor')
        if fault not in (None, 'identity', 'arm_refused', 'transmit_refused',
                         'unknown', 'wrong_digest', 'wrong_boot'):
            raise ValueError('unknown_backend_fault')
        self.descriptor = descriptor
        self.profile = descriptor.profile
        self.fault = fault
        self.time = 100.0
        self.inbound = bytearray()
        self.writes = []
        self.on_transmit = lambda frame: None

    def monotonic(self):
        return self.time

    def offer(self, frame):
        self.inbound.extend(frame.encode())

    def receive_packet(self, packet, *, rssi_dbm=-70, snr_db=3):
        self.offer(Frame(
            Message.RECEIVE,
            self.descriptor.boot_id,
            0,
            struct.pack('!hh', round(rssi_dbm * 10), round(snr_db * 10)) + packet,
        ))

    def write(self, data, deadline):
        if self.time >= deadline:
            raise OSError('backend_deadline')
        frames = Decoder().feed(data)
        if len(frames) != 1:
            raise OSError('backend_write_must_contain_one_frame')
        frame = frames[0]
        self.writes.append(frame)

        def reply(kind, body, *, boot=None):
            self.offer(Frame(
                kind,
                self.descriptor.boot_id if boot is None else boot,
                frame.request,
                body,
            ))

        if frame.type is Message.HELLO:
            board = self.descriptor.board_id if self.fault != 'identity' else 2
            version = self.descriptor.firmware if self.fault != 'identity' else 'mtb1-wrong'
            reply(Message.HELLO_REPLY,
                  (struct.pack('!BBH', board, 1, 255)
                   + version.encode() + b'\0').ljust(20, b'\0'))
            return
        if frame.type is Message.DISARM:
            reply(Message.DISARM_REPLY, b'\0')
            return
        if frame.type is Message.ARM:
            if self.fault == 'arm_refused':
                reply(Message.REFUSED, b'\x07')
            else:
                reply(Message.ARM_REPLY, frame.body)
            return
        if frame.type is not Message.TRANSMIT:
            raise OSError('unsupported_backend_command')

        self.on_transmit(frame)
        if self.fault == 'transmit_refused':
            reply(Message.REFUSED, b'\x08')
            return
        digest = hashlib.sha256(frame.body).digest()
        estimate = estimated_airtime_us(self.profile, len(frame.body))
        reply(Message.TX_RESULT, struct.pack(
            '!B32sQQIh', Outcome.STARTED, digest, estimate, 0, 0, 0))
        if self.fault == 'unknown':
            outcome = Outcome.UNKNOWN
            flags, error = 0x201, -1
        else:
            outcome = Outcome.RF_DONE
            flags, error = 1, 0
        reply(Message.TX_RESULT, struct.pack(
            '!B32sQQIh',
            outcome,
            bytes(32) if self.fault == 'wrong_digest' else digest,
            estimate,
            estimate + 1000,
            flags,
            error,
        ), boot=bytes(16) if self.fault == 'wrong_boot' else None)

    def read(self, timeout):
        if self.inbound:
            data = bytes(self.inbound[:4096])
            del self.inbound[:4096]
            return data
        self.time += max(timeout, 0.0001)
        return b''


def descriptor_document(descriptor):
    """Canonical JSON-safe descriptor representation for evidence retention."""
    return json.dumps(descriptor.record(), sort_keys=True, separators=(',', ':'))
