"""Inert contract for the experimental DATA-only MCU ciphertext queue.

No serial opcode, owner, lease, durable reservation or RF permission is created
here. These offline values cannot replace the future native owner's checks.
Queue acceptance and local RF evidence never authenticate a peer receipt.
"""
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import struct

MAX_DEPTH = 4
MAX_AUTONOMOUS_HOLD_US = 2_000_000
START_GUARD_US = 100_000
MAX_PACKET_BYTES = 255
UINT64_MAX = (1 << 64) - 1
_HEADER = struct.Struct('!4s16s32sQQQB')
_PACKET = struct.Struct('!QBHIQ32s')
_MAGIC = b'BTD1'  # Offline descriptor format; not a serial command.


class BurstContractError(ValueError):
    pass


def _need(ok, reason):
    if not ok:
        raise BurstContractError(reason)


def _integer(value, minimum, maximum, reason):
    _need(type(value) is int and minimum <= value <= maximum, reason)


def _bytes(value, length, reason):
    _need(type(value) is bytes and len(value) == length, reason)


class PacketKind(IntEnum):
    DATA = 1


class Stage(IntEnum):
    QUEUED = 1
    ISSUED = 2
    STARTED = 3
    RF_DONE = 4
    NOT_SENT = 5
    UNKNOWN = 6


TERMINAL = frozenset((Stage.RF_DONE, Stage.UNKNOWN, Stage.NOT_SENT))


@dataclass(frozen=True, slots=True)
class Binding:
    boot: bytes
    context_sha256: bytes

    def __post_init__(self):
        _bytes(self.boot, 16, 'invalid_burst_boot')
        _bytes(self.context_sha256, 32, 'invalid_burst_context')
        _need(any(self.boot) and any(self.context_sha256), 'empty_burst_binding')


def context_sha256(grant_sha256, encoded_profile):
    """Bind exact grant and profile bytes; their authority remains external."""
    _bytes(grant_sha256, 32, 'invalid_burst_grant_digest')
    _need(type(encoded_profile) is bytes and 1 <= len(encoded_profile) <= 320,
          'invalid_burst_encoded_profile')
    return hashlib.sha256(b'meshticulum.tx-burst.context.v1\0' + grant_sha256
                          + struct.pack('!H', len(encoded_profile)) + encoded_profile).digest()


@dataclass(frozen=True, slots=True)
class PacketDescriptor:
    request: int
    size: int
    digest: bytes
    estimated_airtime_us: int
    deadline_us: int
    kind: PacketKind = PacketKind.DATA

    def __post_init__(self):
        _integer(self.request, 1, UINT64_MAX, 'invalid_burst_request')
        _integer(self.size, 1, MAX_PACKET_BYTES, 'invalid_burst_packet_size')
        _bytes(self.digest, 32, 'invalid_burst_packet_digest')
        _need(any(self.digest), 'empty_burst_packet_digest')
        _integer(self.estimated_airtime_us, 1, MAX_AUTONOMOUS_HOLD_US,
                 'invalid_burst_airtime')
        _integer(self.deadline_us, 1, UINT64_MAX, 'invalid_burst_deadline')
        _need(type(self.kind) is PacketKind and self.kind is PacketKind.DATA,
              'burst_requires_local_data_role')

    @classmethod
    def from_ciphertext(cls, request, ciphertext, estimated_airtime_us, deadline_us):
        _need(type(ciphertext) is bytes and 1 <= len(ciphertext) <= MAX_PACKET_BYTES,
              'invalid_burst_ciphertext')
        return cls(request, len(ciphertext), hashlib.sha256(ciphertext).digest(),
                   estimated_airtime_us, deadline_us)

    def check_ciphertext(self, ciphertext):
        _need(type(ciphertext) is bytes and len(ciphertext) == self.size
              and hashlib.sha256(ciphertext).digest() == self.digest,
              'burst_ciphertext_changed')

    def _encode(self):
        return _PACKET.pack(self.request, self.kind, self.size,
                            self.estimated_airtime_us, self.deadline_us, self.digest)


@dataclass(frozen=True, slots=True)
class QueueDescriptor:
    binding: Binding
    enqueued_at_us: int
    original_deadline_us: int
    deadline_us: int
    packets: tuple[PacketDescriptor, ...]

    def __post_init__(self):
        _need(type(self.binding) is Binding, 'invalid_burst_binding')
        _integer(self.enqueued_at_us, 0, UINT64_MAX, 'invalid_burst_clock')
        _integer(self.original_deadline_us, 1, UINT64_MAX, 'invalid_original_burst_deadline')
        _integer(self.deadline_us, 1, UINT64_MAX, 'invalid_burst_deadline')
        _need(self.enqueued_at_us < self.deadline_us <= self.original_deadline_us
              and self.deadline_us - self.enqueued_at_us <= MAX_AUTONOMOUS_HOLD_US,
              'burst_deadline_extended_or_unbounded')
        _need(type(self.packets) is tuple and 1 <= len(self.packets) <= MAX_DEPTH
              and all(type(packet) is PacketDescriptor for packet in self.packets),
              'invalid_burst_packets')
        _need(all(packet.deadline_us == self.deadline_us for packet in self.packets),
              'burst_deadline_not_shared')
        requests = tuple(packet.request for packet in self.packets)
        _need(all(a < b for a, b in zip(requests, requests[1:])), 'burst_request_not_increasing')
        self._room(self.enqueued_at_us, self.packets)

    def _room(self, now_us, packets):
        _need(sum(packet.estimated_airtime_us + START_GUARD_US for packet in packets)
              <= self.deadline_us - now_us, 'burst_cannot_fit_original_deadline')

    def check_admission(self, *, now_us, independent_airtime_us):
        """Pure checks; caller must derive estimates from the admitted waveform."""
        _integer(now_us, self.enqueued_at_us, UINT64_MAX, 'burst_clock_regressed')
        _need(type(independent_airtime_us) is tuple
              and len(independent_airtime_us) == len(self.packets)
              and all(type(value) is int for value in independent_airtime_us)
              and independent_airtime_us == tuple(p.estimated_airtime_us for p in self.packets),
              'burst_independent_airtime_mismatch')
        self._room(now_us, self.packets)

    def check_dispatch(self, request, *, now_us, independent_airtime_us):
        """One packet's deadline check, not permission to touch a driver."""
        _integer(now_us, self.enqueued_at_us, UINT64_MAX, 'burst_clock_regressed')
        packet = self.packet(request)
        _need(type(independent_airtime_us) is int
              and independent_airtime_us == packet.estimated_airtime_us,
              'burst_independent_airtime_mismatch')
        self._room(now_us, (packet,))
        return packet

    def packet(self, request):
        _integer(request, 1, UINT64_MAX, 'invalid_burst_request')
        for packet in self.packets:
            if packet.request == request:
                return packet
        raise BurstContractError('foreign_burst_request')

    def encode(self):
        """Offline descriptor only: excludes ciphertext and assigns no opcode."""
        return _HEADER.pack(_MAGIC, self.binding.boot, self.binding.context_sha256,
                            self.enqueued_at_us, self.original_deadline_us,
                            self.deadline_us, len(self.packets)) + b''.join(p._encode() for p in self.packets)

    @classmethod
    def decode(cls, raw):
        _need(type(raw) is bytes and _HEADER.size <= len(raw)
              <= _HEADER.size + MAX_DEPTH * _PACKET.size, 'invalid_burst_descriptor_size')
        magic, boot, context, queued, original, deadline, count = _HEADER.unpack_from(raw)
        _need(magic == _MAGIC and 1 <= count <= MAX_DEPTH
              and len(raw) == _HEADER.size + count * _PACKET.size, 'invalid_burst_descriptor_shape')
        packets = []
        for offset in range(_HEADER.size, len(raw), _PACKET.size):
            request, kind, size, estimate, packet_deadline, digest = _PACKET.unpack_from(raw, offset)
            _need(kind == PacketKind.DATA, 'burst_requires_local_data_role')
            packets.append(PacketDescriptor(request, size, digest, estimate,
                                            packet_deadline, PacketKind.DATA))
        return cls(Binding(boot, context), queued, original, deadline, tuple(packets))


@dataclass(frozen=True, slots=True)
class PacketEvidence:
    binding: Binding
    packet: PacketDescriptor
    stage: Stage
    at_us: int
    driver_elapsed_us: int | None = None
    irq_flags: int | None = None
    driver_error: int | None = None

    def __post_init__(self):
        _need(type(self.binding) is Binding and type(self.packet) is PacketDescriptor,
              'invalid_burst_evidence_binding')
        _need(type(self.stage) is Stage, 'invalid_burst_evidence_stage')
        _integer(self.at_us, 0, UINT64_MAX, 'invalid_burst_evidence_clock')
        driver = (self.driver_elapsed_us, self.irq_flags, self.driver_error)
        if self.stage in (Stage.STARTED, Stage.RF_DONE):
            _integer(self.driver_elapsed_us, 0, UINT64_MAX, 'invalid_burst_driver_elapsed')
            _integer(self.irq_flags, 0, (1 << 32) - 1, 'invalid_burst_driver_irq')
            _need(type(self.driver_error) is int and self.driver_error == 0,
                  'burst_driver_error_is_not_completion')
            if self.stage is Stage.STARTED:
                _need(driver == (0, 0, 0), 'invalid_burst_started_evidence')
            else:
                _need(self.driver_elapsed_us > 0 and self.irq_flags & 1
                      and not self.irq_flags & 0x200, 'invalid_burst_rf_done_evidence')
        else:
            _need(driver == (None, None, None), 'unexpected_burst_driver_evidence')


class EvidenceHistory:
    """Bounded in-memory transition validator; never a durable attempt journal.

    Intermediate reports may be lost: exact terminal driver evidence can still
    settle physical accounting. UNKNOWN is a sticky fence. Peer ACK acceptance
    and publisher/application completion are deliberately absent from this API.
    """
    def __init__(self, descriptor):
        _need(type(descriptor) is QueueDescriptor, 'invalid_burst_descriptor')
        self.descriptor = descriptor
        self._latest = {}
        self.fenced = False

    def observe(self, evidence):
        try:
            _need(type(evidence) is PacketEvidence
                  and evidence.binding == self.descriptor.binding,
                  'foreign_burst_evidence')
            packet = self.descriptor.packet(evidence.packet.request)
            _need(evidence.packet == packet, 'burst_evidence_packet_changed')
            _need(evidence.at_us >= self.descriptor.enqueued_at_us,
                  'burst_evidence_clock_regressed')
            previous = self._latest.get(packet.request)
            if previous == evidence:
                return False  # Exact retained replay, no second settlement.
            if previous is not None:
                _need(previous.stage not in TERMINAL, 'burst_terminal_evidence_changed')
                _need(evidence.at_us >= previous.at_us, 'burst_evidence_clock_regressed')
                _need(evidence.stage > previous.stage, 'burst_evidence_stage_regressed')
                _need(not (previous.stage in (Stage.ISSUED, Stage.STARTED)
                           and evidence.stage is Stage.NOT_SENT), 'issued_burst_cannot_be_unsent')
            if evidence.stage in (Stage.QUEUED, Stage.ISSUED, Stage.STARTED):
                _need(evidence.at_us < packet.deadline_us, 'burst_activity_after_deadline')
            self._latest[packet.request] = evidence
            if evidence.stage is Stage.UNKNOWN:
                self.fenced = True
            return True
        except BurstContractError:
            self.fenced = True
            raise

    @property
    def pending_requests(self):
        return tuple(packet.request for packet in self.descriptor.packets
                     if packet.request not in self._latest
                     or self._latest[packet.request].stage not in TERMINAL)

    @property
    def rf_done_requests(self):
        return tuple(packet.request for packet in self.descriptor.packets
                     if packet.request in self._latest
                     and self._latest[packet.request].stage is Stage.RF_DONE)
