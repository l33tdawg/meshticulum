"""Dormant MBQ2 body codec and conservative host commit model.

These records are NOT production serial Frames, opcodes, authority, or a
durable journal. No current worker consumes them. A future exclusive owner
must atomically reserve every physical attempt in its durable journal before
transmitting COMMIT and retain each charge even on rejection or uncertainty.
"""
from dataclasses import dataclass
from enum import IntEnum
import hashlib
import struct

from .tx_burst import (QueueDescriptor, Stage,
                       UINT64_MAX, _bytes, _integer, _need)

MAX_BODY = 320
_BEGIN = struct.Struct('!QQB32s')
_STAGE = struct.Struct('!QQBQH32s')
_COMMIT = struct.Struct('!QQ32s')
_RELEASE = struct.Struct('!QQ32sB')
_OUTCOME = struct.Struct('!QBQ32s')
_MANIFEST = struct.Struct('!4s16s32sQQQB')
_PACKET = struct.Struct('!QQH32s')


class BatchKind(IntEnum):
    """Offline discriminators; deliberately absent from serial Message."""
    BEGIN = 1
    STAGE = 2
    COMMIT = 3
    RELEASE = 4


@dataclass(frozen=True, slots=True)
class BatchRecord:
    kind: BatchKind
    boot: bytes
    request: int
    body: bytes

    def __post_init__(self):
        _need(type(self.kind) is BatchKind, 'invalid_batch_kind')
        _bytes(self.boot, 16, 'invalid_batch_boot')
        _need(any(self.boot), 'empty_batch_boot')
        _integer(self.request, 1, UINT64_MAX, 'invalid_batch_request')
        _need(type(self.body) is bytes and len(self.body) <= MAX_BODY,
              'invalid_batch_body')


@dataclass(frozen=True, slots=True)
class BatchPlan:
    queue: QueueDescriptor
    token: int
    job: int
    ciphertexts: tuple[bytes, ...]

    def __post_init__(self):
        _need(type(self.queue) is QueueDescriptor, 'invalid_batch_queue')
        _integer(self.token, 1, UINT64_MAX, 'invalid_batch_token')
        _integer(self.job, self.token + 1, UINT64_MAX, 'invalid_batch_job')
        _need(self.queue.packets[0].request > self.job, 'invalid_batch_request_order')
        _need(type(self.ciphertexts) is tuple
              and len(self.ciphertexts) == len(self.queue.packets), 'invalid_batch_ciphertexts')
        for packet, ciphertext in zip(self.queue.packets, self.ciphertexts):
            packet.check_ciphertext(ciphertext)

    @property
    def manifest(self):
        queue = self.queue
        data = _MANIFEST.pack(b'MBQ2', queue.binding.boot, queue.binding.context_sha256,
                              self.token, self.job, queue.deadline_us, len(queue.packets))
        data += b''.join(_PACKET.pack(p.request, p.estimated_airtime_us, p.size, p.digest)
                         for p in queue.packets)
        return hashlib.sha256(data).digest()

    def begin(self):
        return BatchRecord(BatchKind.BEGIN, self.queue.binding.boot, self.job,
                           _BEGIN.pack(self.token, self.queue.deadline_us,
                                       len(self.queue.packets), self.manifest))

    def stage(self, index):
        _integer(index, 0, len(self.queue.packets) - 1, 'invalid_batch_index')
        packet = self.queue.packets[index]
        return BatchRecord(BatchKind.STAGE, self.queue.binding.boot, packet.request,
                           _STAGE.pack(self.token, self.job, index,
                                       packet.estimated_airtime_us, packet.size, packet.digest)
                           + self.ciphertexts[index])

    def commit(self, request):
        _integer(request, self.queue.packets[-1].request + 1, UINT64_MAX,
                 'invalid_batch_commit_request')
        return BatchRecord(BatchKind.COMMIT, self.queue.binding.boot, request,
                           _COMMIT.pack(self.token, self.job, self.manifest))


def _bound(record, plan, kind):
    _need(type(plan) is BatchPlan and type(record) is BatchRecord
          and record.kind is kind and record.boot == plan.queue.binding.boot,
          'unbound_batch_record')


def check_begin(record, plan):
    _bound(record, plan, BatchKind.BEGIN)
    _need(record == plan.begin(), 'batch_begin_changed')


def check_stage(record, plan):
    _bound(record, plan, BatchKind.STAGE)
    _need(len(record.body) >= _STAGE.size, 'truncated_batch_stage')
    token, job, index, estimate, size, digest = _STAGE.unpack_from(record.body)
    _need(token == plan.token and job == plan.job and index < len(plan.queue.packets),
          'unbound_batch_stage')
    _need(len(record.body) == _STAGE.size + size and record == plan.stage(index),
          'batch_stage_changed')
    # Plan construction already hashes every ciphertext; exact record equality
    # binds the hash AND bytes, not a host-supplied digest alone.
    return plan.queue.packets[index]


def check_commit(record, plan, request):
    _bound(record, plan, BatchKind.COMMIT)
    _need(record == plan.commit(request), 'batch_commit_changed')


@dataclass(frozen=True, slots=True)
class ReleaseEntry:
    request: int
    stage: Stage
    event_us: int
    digest: bytes

    def __post_init__(self):
        _integer(self.request, 1, UINT64_MAX, 'invalid_batch_release_request')
        _need(type(self.stage) is Stage and self.stage in
              (Stage.RF_DONE, Stage.NOT_SENT, Stage.UNKNOWN), 'batch_release_not_terminal')
        _integer(self.event_us, 0, UINT64_MAX, 'invalid_batch_release_clock')
        _bytes(self.digest, 32, 'invalid_batch_release_digest')


def release_record(plan, *, commit_request, request, entries):
    _need(type(plan) is BatchPlan, 'invalid_batch_plan')
    plan.commit(commit_request)
    _integer(request, commit_request + 1, UINT64_MAX, 'invalid_batch_release_request')
    _need(type(entries) is tuple and len(entries) == len(plan.queue.packets),
          'partial_batch_release')
    for entry, packet in zip(entries, plan.queue.packets):
        _need(type(entry) is ReleaseEntry and entry.request == packet.request
              and entry.digest == packet.digest, 'batch_release_packet_changed')
    body = _RELEASE.pack(plan.token, plan.job, plan.manifest, len(entries))
    body += b''.join(_OUTCOME.pack(e.request, e.stage, e.event_us, e.digest) for e in entries)
    return BatchRecord(BatchKind.RELEASE, plan.queue.binding.boot, request, body)


def check_release(record, plan, *, commit_request, request, entries):
    """Caller supplies the ENTIRE durably settled vector, in manifest order."""
    _bound(record, plan, BatchKind.RELEASE)
    expected = release_record(plan, commit_request=commit_request, request=request, entries=entries)
    _need(record == expected, 'batch_release_changed')


class PrepaidCommit:
    """Source model of an already committed durable host reservation.

    This class does not create that reservation. `charges_us` are the caller's
    irrevocable per-attempt host amounts, which may exceed native airtime.
    A lost/malformed/mismatched reply is sticky UNKNOWN; later replies cannot
    un-fence or refund it. Physical reconciliation remains external.
    """
    __slots__ = ('_record', '_charges', '_state')

    def __init__(self, plan, request, charges_us):
        _need(type(plan) is BatchPlan, 'invalid_batch_plan')
        _need(type(charges_us) is tuple and len(charges_us) == len(plan.queue.packets),
              'batch_reservation_incomplete')
        for charge, packet in zip(charges_us, plan.queue.packets):
            _integer(charge, packet.estimated_airtime_us, UINT64_MAX,
                     'invalid_batch_prepaid_charge')
        _need(sum(charges_us) <= UINT64_MAX, 'batch_reservation_overflow')
        self._record = plan.commit(request)
        self._charges = charges_us
        self._state = 'PENDING'

    @property
    def record(self):
        return self._record

    @property
    def charges_us(self):
        return self._charges

    @property
    def state(self):
        return self._state

    @property
    def fenced(self):
        return self._state == 'UNKNOWN'

    def unknown(self):
        self._state = 'UNKNOWN'

    def reply(self, echoed_record, *, accepted):
        if (type(accepted) is not bool or type(echoed_record) is not BatchRecord
                or echoed_record != self._record):
            self.unknown()
            _need(False, 'batch_commit_reply_mismatch')
        if self._state == 'UNKNOWN':
            return  # Exact late replay is evidence, never renewed authority.
        result = 'ACCEPTED' if accepted else 'REFUSED'
        if self._state not in ('PENDING', result):
            self.unknown()
            _need(False, 'batch_commit_reply_conflict')
        self._state = result
