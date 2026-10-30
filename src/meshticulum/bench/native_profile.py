"""Strict, read-only codec for opt-in native MNP1 timing diagnostics.

No device is opened. The admitted owner must first disarm and reconcile every
retained physical result. Records are cumulative within the exact MCU boot;
inclusive parent phases overlap their children and must not be added together.
"""
from dataclasses import dataclass
from enum import IntEnum
import struct

from .serial_protocol import Frame, Message, require


class NativePhase(IntEnum):
    TX_FINISH_CLEANUP = 0
    RX_START_CALL = 1
    TX_DONE_TO_RX_RETURN = 2
    ISSUED_TO_STARTED = 3
    ISSUED_PUBLISH = 4
    ISSUE_STANDBY = 5
    ISSUE_IRQ_REREAD = 6
    ISSUE_RX_DRAIN = 7
    ISSUE_IRQ_CLEAR = 8
    ISSUE_COPY_PREPARE = 9
    ISSUE_TX_START_CALL = 10
    STARTED_PUBLISH = 11


RECORD = struct.Struct('!IIIIQQQ')
INCLUSIVE_PHASES = frozenset((NativePhase.TX_DONE_TO_RX_RETURN,
                            NativePhase.ISSUED_TO_STARTED))


def query(*, boot, request, phase=None):
    require(type(request) is int and request > 0, 'invalid_native_profile_request')
    require(phase is None or type(phase) is NativePhase, 'invalid_native_profile_phase')
    return Frame(Message.NATIVE_PROFILE, boot, request,
                 b'' if phase is None else bytes((phase,)))


def memory_query(*, boot, request):
    require(type(request) is int and request > 0, 'invalid_native_profile_request')
    return Frame(Message.NATIVE_PROFILE, boot, request, b'\xfe')


class NativeRxReason(IntEnum):
    READ_PAYLOAD_CRC = 0
    READ_HEADER = 1
    READ_BOTH = 2
    READ_INTEGRITY_UNATTRIBUTED = 3
    READ_TIMEOUT = 4
    READ_OTHER_ERROR = 5
    INVALID_REPORTED_LENGTH = 6
    OUTPUT_RESERVE_LOW = 7
    OUTPUT_OFFER_FAILED = 8
    SYNC_TX_HEADER_PREEMPTED = 9
    IRQ_RESTART_CRC_ONLY = 10
    IRQ_RESTART_HEADER_ONLY = 11
    IRQ_RESTART_BOTH = 12
    QUEUED_HEADER_STALL = 13
    RX_EXPORTED = 14
    TIMEOUT_ONLY_RESTART = 15
    BACKPRESSURE_DEFER_VISITS = 16


def rx_reasons_query(*, boot, request):
    require(type(request) is int and request > 0, 'invalid_native_profile_request')
    return Frame(Message.NATIVE_PROFILE, boot, request, b'\xfd')


@dataclass(frozen=True, slots=True)
class NativeRxReasons:
    """Boot-cumulative software observations; not authenticated packet loss.

    The first fourteen counters partition the firmware's existing drop sites.
    Backpressure counts loop visits, not packets. Saturation makes totals lower
    bounds; no subtraction across MCU boots or saturated observations is valid.
    """
    counts: tuple[int, ...]
    saturated_mask: int

    @classmethod
    def from_frame(cls, frame, *, boot, request):
        require(type(frame) is Frame and frame.type is Message.NATIVE_PROFILE_REPLY
                and type(boot) is bytes and len(boot) == 16 and frame.boot == boot
                and type(request) is int and request > 0 and frame.request == request,
                'wrong_native_rx_reasons_reply')
        require(len(frame.body) == 80 and frame.body[:8] == b'MNR1\x01\x11\x04\x01',
                'invalid_native_rx_reasons_schema')
        mask, *counts = struct.unpack('!18I', frame.body[8:])
        require(mask >> len(NativeRxReason) == 0
                and all(not (mask & (1 << index)) or count == 2**32 - 1
                        for index, count in enumerate(counts)),
                'invalid_native_rx_reasons_saturation')
        return cls(tuple(counts), mask)

    @property
    def exact(self):
        return self.saturated_mask == 0

    @property
    def dropped_total(self):
        """Sum of drop-site counters; a lower bound if any such counter saturated."""
        return sum(self.counts[:14])


@dataclass(frozen=True, slots=True)
class NativeMemorySnapshot:
    internal_free_bytes: int
    internal_region_minimum_sum_bytes: int
    internal_largest_free_block_bytes: int
    loop_stack_minimum_free_bytes: int

    @classmethod
    def from_frame(cls, frame, *, boot, request):
        require(type(frame) is Frame and frame.type is Message.NATIVE_PROFILE_REPLY
                and type(boot) is bytes and len(boot) == 16 and frame.boot == boot
                and type(request) is int and request > 0 and frame.request == request,
                'wrong_native_memory_reply')
        require(len(frame.body) == 24 and frame.body[:8] == b'MNS1\x01\x01\x00\x00',
                'invalid_native_memory_schema')
        free, minimum, largest, stack = struct.unpack('!IIII', frame.body[8:])
        require(minimum <= free and largest <= free, 'invalid_native_memory_counters')
        # Zero headroom remains visible; decoding is not memory qualification.
        return cls(free, minimum, largest, stack)


def _header(frame, *, boot, request, phase):
    require(type(frame) is Frame and frame.type is Message.NATIVE_PROFILE_REPLY
            and type(boot) is bytes and len(boot) == 16 and frame.boot == boot
            and type(request) is int and request > 0 and frame.request == request,
            'wrong_native_profile_reply')
    require(len(frame.body) == (8 if phase is None else 8 + RECORD.size),
            'invalid_native_profile_size')
    expected = b'MNP1' + bytes((len(NativePhase), RECORD.size,
                               255 if phase is None else phase, 1))
    require(frame.body[:8] == expected, 'invalid_native_profile_schema')


@dataclass(frozen=True, slots=True)
class NativeProfileCapability:
    phases: int = len(NativePhase)
    record_bytes: int = RECORD.size

    @classmethod
    def from_frame(cls, frame, *, boot, request):
        _header(frame, boot=boot, request=request, phase=None)
        return cls()


@dataclass(frozen=True, slots=True)
class NativeProfileRecord:
    phase: NativePhase
    calls: int
    failures: int
    invalid: int
    flags: int
    total_us: int
    min_us: int
    max_us: int

    @classmethod
    def from_frame(cls, frame, *, boot, request, phase):
        require(type(phase) is NativePhase, 'invalid_native_profile_phase')
        _header(frame, boot=boot, request=request, phase=phase)
        calls, failures, invalid, flags, total, minimum, maximum = RECORD.unpack(frame.body[8:])
        require(flags & ~3 == 0 and failures <= calls and invalid <= calls
                and bool(flags & 2) == bool(invalid), 'invalid_native_profile_counters')
        require((calls != 0 or failures == invalid == flags == total == minimum == maximum == 0)
                and (not flags & 1 or calls == 2**32-1 or total == 2**64-1),
                'invalid_native_profile_saturation')
        require(minimum <= maximum <= total, 'invalid_native_profile_durations')
        valid = calls - invalid
        if not flags & 1:
            require((valid == 0 and minimum == maximum == total == 0)
                    or (valid > 0 and minimum * valid <= total <= maximum * valid),
                    'invalid_native_profile_aggregate')
        return cls(phase, calls, failures, invalid, flags, total, minimum, maximum)

    @property
    def inclusive(self):
        return self.phase in INCLUSIVE_PHASES

    @property
    def exact(self):
        """False if any duration was invalid or an aggregate saturated."""
        return self.flags == 0
