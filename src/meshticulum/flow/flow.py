"""Bounded reliable flow candidate for Meshticulum (MT05).

Revision 9, 9 October 2026. Revision 1 is preserved byte-for-byte in
work/ember-meshticulum-mt05-20261004/frozen-r1/, the revision 5 candidate it
replaces is held at
work/meshticulum-plan-audit-20261004/credit-contract-review/inputs/, the
revision 6 bytes this revision repairs are held at
work/meshticulum-resume-review-20261004/held-r6/ and
work/meshticulum-r6-review-20261004/frozen-inputs/, and the revision 8 bytes
this revision repairs are held at
work/rook-meshticulum-r8e-review-20261006/candidate/.

What revision 9 adds on top of revision 8, under the two P1 findings of
work/rook-meshticulum-r8e-review-20261006/DECISION.json:

* **A committed acknowledgement retires only what it stated.** The snapshot and
  revision a queued emission recorded are always settled, because that frame did
  leave and did mint that revision. The receive-state flags are settled only when
  nothing has been raised since the frame was sealed: an emission minted before a
  later frame, completion or consume states an older truth, and letting it clear
  newer work loses the only signal that a newer statement is owed. Equal credit
  does not make two acknowledgements equal, since the cumulative base and the
  selective bitmap move independently of it (R8E-F01).
* **The owning limits travel with the comparison.** Rebuilding a queued emission
  under the constructor's defaults replaces the flow's own configuration, so a
  flow whose reassembly ceiling exceeds that default refuses its own valid
  emission and fences an otherwise definite settlement (R8E-F02).

What revision 7 adds on top of revision 6, under the seven repair groups of
work/meshticulum-r6-review-20261004/DECISION.json:

* **A queued frame is not a sent frame.** A host link may accept a frame and hand
  it over later; it says so with ``DEFERRED``, and until the real handover this
  endpoint consumes no byte, no sequence number and no retry deadline. The
  attempt and the retry clock start at the instant the carrier is invoked, so a
  host admission delay can neither start a flight early nor manufacture a
  duplicate retry (CF01, CF02).
* **The startup branch is bounded on both sides.** A receiver without a shared
  snapshot re-announces its first canonical statement a bounded number of times
  and then fails the direction; a sender with queued data, no credit and no
  acknowledged frame fails too. Neither can idle with no remaining event
  (R6-CR-F01).
* **The shared startup snapshot is validated as the initial state.** Base,
  bitmap and revision must be the canonical initial ones, and a credit the
  receiving endpoint cannot hold - or that no explicit trusted capacity
  supports - is refused rather than installed (R6-CR-F02).
* **Receipt and owner credit move together.** An accepted canonical ACK is
  offered to a host owner that mirrors the same snapshot before either side
  commits, so a refused or over-wide snapshot refuses the whole acknowledgement
  with nothing moved (PORT-R6-03).

What revision 6 changes under the frozen MT03/MT04 decision
(work/meshticulum-plan-audit-20261004/credit-contract-review/DECISION.json):

* **Explicit candidate framing.** Both directions use the MT3 magic. MT2 and
  unknown magic, kinds and lengths are refused before any state mutation. This
  is a deliberate candidate incompatibility, not an older-peer fallback.
* **A snapshot, not a grant.** An ACK binds the canonical cumulative receipt
  base, the selective receipt bitmap and the currently free frame credit into
  one immutable directional snapshot, stamped with the revision of that exact
  tuple. The tuple mints a higher revision when it changes; repeated, persisted
  and probe-answer copies of the same tuple retain theirs.
* **Strict revision order.** The revision is an unsigned 32-bit counter from 1
  to 4294967295, ordered numerically. There is no modulus, no wrap and no zero
  sentinel. Exhaustion fences the direction rather than reusing a statement.
* **Whole-ACK freshness.** An older revision is ignored as an entire ACK: it
  cannot move credit, retire receipt, or change probe, silence or retry timing.
  An equal revision is idempotent only for an identical tuple and is refused
  without mutation otherwise.
* **Peer capacity is not inferred.** Without a fixture-established shared
  startup snapshot, the sender begins at zero credit and needs an explicit
  startup grant. A local limit is never evidence of remote room.

What one flow is: one ordered directional transfer over one carrier, with two
independently governed directions. What it is not: a stream, an object
transfer, an authenticated service, or a scheduler.

Semantics this revision enforces, each with a discriminating test:

* **Terminal is terminal.** A completed, failed or cancelled direction does not
  absorb, deliver or re-complete on later input. The one exception is
  deliberate: a duplicate of a frame already admitted is answered with the
  current receipt and credit state, because that is how a lost ACK is repaired.
* **An ACK is only as good as the sequence space it can account for.** A
  cumulative or selective acknowledgement beyond what was actually sent is
  refused, so no ACK can fabricate completion.
* **Directions complete independently.** A short transfer in one direction
  cannot mark the other direction finished.
* **Everything received is bounded**: payload size, schema, total frames, the
  reorder window, and total unconsumed bytes. Credit falls to zero as the
  application stops consuming, and consume() returns it.
* **Nothing is widened silently.** Out-of-range generations, flow ids,
  sequences, flags, bitmaps and limits are refused rather than masked.

Isolated: this module imports the simulated carrier and nothing from the
runtime.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import math
import struct
import zlib

MAGIC = b"MT3"
KIND_DATA = 0
KIND_ACK = 1
FLAG_LAST = 0x01
KNOWN_FLAGS = FLAG_LAST
SEQ_MODULUS = 1 << 16
SACK_BITS = 32

_DATA = struct.Struct("!3sBBBBHH")
DATA_HEADER_BYTES = _DATA.size
# The ACK binds the canonical receipt (cumulative base plus selective bitmap)
# and the currently free frame credit into one directional snapshot, stamped
# with the revision of that exact tuple. Zero is not a revision and is never a
# sentinel for the older frame shape: an older shape is refused by magic and by
# length, before anything is mutated.
_ACK = struct.Struct("!3sBBBHHBI")
ACK_HEADER_BYTES = _ACK.size
GRANT_REV_MIN = 1
GRANT_REV_MAX = (1 << 32) - 1
ACK_BYTES = _ACK.size + SACK_BITS // 8 + 4
_CRC = struct.Struct("!I")
MIN_FRAME_BYTES = _DATA.size + _CRC.size


class FlowState(Enum):
    OPEN = "open"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PEER_LOST = "peer_lost"


class FlowError(Exception):
    pass


class _Deferred(int):
    """The link queued the frame under a live handle without handing it over.

    Falsy on purpose. A caller that only tests truthiness reads "not sent", which
    is the honest reading, while a caller that cares can tell a deferral from a
    refusal by identity and therefore must not consume a byte, a sequence number
    or a retry deadline for a frame that has not left. The attempt and the retry
    clock belong to the instant the host actually dispatches it.

    It is an ``int`` subclass carrying zero for one reason: it is a value a host
    or reviewer will put in a diagnostic record, and an opaque singleton that
    makes ``json.dumps`` raise takes the whole measurement down with it. Zero
    serializes as "no bytes sent", which is exactly what it means, and identity
    still separates it from a refusal.
    """

    __slots__ = ()

    def __new__(cls):
        return super().__new__(cls, 0)

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "DEFERRED"

    __str__ = __repr__


#: Returned by a host link's ``send`` when the frame is queued but not yet given
#: to the carrier. See :meth:`ReliableFlow.handover`.
DEFERRED = _Deferred()


@dataclass(frozen=True)
class FlowLimits:
    frame_payload_bytes: int = 64
    window_frames: int = 16
    initial_credit_frames: int = 16
    max_reassembly_frames: int = 64
    max_delivered_bytes: int = 65536
    max_queued_bytes: int = 8192
    max_total_frames: int = 4096
    max_retries: int = 6
    # RFC 6298 starts an unmeasured path at one second. Starting lower makes a
    # burst on a slow carrier look like loss and buys spurious retransmissions.
    initial_rto_s: float = 1.0
    min_rto_s: float = 0.2
    max_rto_s: float = 8.0
    ack_delay_s: float = 0.05
    seq_modulus: int = SEQ_MODULUS
    max_generation: int = 255
    max_flow_id: int = 255
    # A receiver that has room and is still waiting re-announces its credit a
    # bounded number of times, because the grant that would restart the sender
    # can be the one frame the reverse link loses.
    persist_interval_s: float = 0.5
    max_persist_acks: int = 3

    def __post_init__(self):
        for name, value in (
            ("frame_payload_bytes", self.frame_payload_bytes), ("window_frames", self.window_frames),
            ("initial_credit_frames", self.initial_credit_frames),
            ("max_reassembly_frames", self.max_reassembly_frames),
            ("max_delivered_bytes", self.max_delivered_bytes),
            ("max_queued_bytes", self.max_queued_bytes),
            ("max_total_frames", self.max_total_frames), ("max_retries", self.max_retries),
            ("seq_modulus", self.seq_modulus), ("max_generation", self.max_generation),
            ("max_flow_id", self.max_flow_id), ("max_persist_acks", self.max_persist_acks),
        ):
            # `bool` is an `int`, and a fractional value that reaches the wire
            # packer raises a raw struct error after work is already queued.
            if type(value) is not int:
                raise FlowError(f"invalid limit: {name} must be an int")
        for name, value in (
            ("initial_rto_s", self.initial_rto_s), ("min_rto_s", self.min_rto_s),
            ("max_rto_s", self.max_rto_s), ("ack_delay_s", self.ack_delay_s),
            ("persist_interval_s", self.persist_interval_s),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise FlowError(f"invalid limit: {name} must be a finite number")
        rules = (
            (self.frame_payload_bytes >= 1, "frame_payload_bytes >= 1"),
            (self.window_frames >= 1, "window_frames >= 1"),
            (2 * self.window_frames < self.seq_modulus, "window_frames must stay under half the sequence space"),
            (self.initial_credit_frames >= 1, "initial_credit_frames >= 1"),
            (self.max_reassembly_frames >= 1, "max_reassembly_frames >= 1"),
            (2 * self.max_reassembly_frames < self.seq_modulus,
             "max_reassembly_frames must stay under half the sequence space"),
            (self.seq_modulus <= 1 << 16, "seq_modulus must fit the 16-bit sequence field"),
            (self.max_total_frames <= 0xFFFF, "max_total_frames must fit the 16-bit total field"),
            (self.max_persist_acks >= 0, "max_persist_acks >= 0"),
            (self.persist_interval_s > 0, "persist_interval_s > 0"),
            (self.max_delivered_bytes >= self.frame_payload_bytes, "max_delivered_bytes >= one frame"),
            (self.max_queued_bytes >= self.frame_payload_bytes, "max_queued_bytes >= one frame"),
            (self.max_total_frames >= 1, "max_total_frames >= 1"),
            (self.max_retries >= 1, "max_retries >= 1"),
            (0 < self.min_rto_s <= self.initial_rto_s <= self.max_rto_s, "min_rto <= initial_rto <= max_rto"),
            (self.ack_delay_s >= 0, "ack_delay_s >= 0"),
            (self.seq_modulus >= 4, "seq_modulus >= 4"),
            (0 < self.max_generation <= 255, "0 < max_generation <= 255"),
            (0 < self.max_flow_id <= 255, "0 < max_flow_id <= 255"),
        )
        for ok, rule in rules:
            if not ok:
                raise FlowError(f"invalid limit: {rule}")


def check_grant_revision(value, name: str = "grant_revision") -> int:
    """Strict unsigned 32-bit revision in 1..4294967295.

    ``bool`` is an ``int`` in Python, and zero is not a "missing" revision to be
    filled in later. Both are refused, along with every non-integer: a value
    that reaches the wire packer unchecked becomes a silently wrapped or masked
    statement about someone else's credit.
    """
    if type(value) is not int or not GRANT_REV_MIN <= value <= GRANT_REV_MAX:
        raise FlowError(f"{name} must be an int in {GRANT_REV_MIN}..{GRANT_REV_MAX}")
    return value


def grant_is_ahead(candidate: int, held: int) -> bool:
    """Whether a revision is strictly ahead of the one held.

    Plain numeric order. The counter has no modulus, so there is no half-space
    window to compare inside and no wrap-around to reason about.
    """
    return check_grant_revision(candidate) > check_grant_revision(held)


@dataclass(frozen=True)
class StartupGrant:
    """An immutable, fixture-established shared startup snapshot.

    The decision allows an isolated fixture to establish one identical snapshot
    at both endpoints: the initial cumulative receipt base, an empty selective
    bitmap and a free frame credit bounded by the *remote* capacity. Nothing in
    this record is inferred by a sender from its own local limits, and a real
    host establishes the same state through an explicit startup grant exchange
    instead of sharing a fixture value.
    """

    credit_frames: int
    ack_base: int = 0
    sack: int = 0
    revision: int = GRANT_REV_MIN

    def __post_init__(self):
        for name in ("credit_frames", "ack_base", "sack"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise FlowError(f"invalid startup grant: {name}")
        if self.sack >= (1 << SACK_BITS):
            raise FlowError("invalid startup grant: sack wider than the bitmap")
        check_grant_revision(self.revision, "startup grant revision")
        # A startup snapshot is the *initial* canonical state, not an arbitrary
        # later one wearing fixture clothes. An initial receipt base that is not
        # zero, a non-empty selective bitmap, or a revision past the first all
        # describe a state the shared handshake never produces, and installing
        # one would attribute to the peer a statement it never made.
        if self.ack_base != 0:
            raise FlowError("invalid startup grant: ack_base must be the initial base")
        if self.sack != 0:
            raise FlowError("invalid startup grant: sack must be empty")
        if self.revision != GRANT_REV_MIN:
            raise FlowError("invalid startup grant: revision must be the startup revision")

    @property
    def snapshot(self) -> tuple[int, int, int]:
        return (self.ack_base, self.sack, self.credit_frames)


def remote_startup_credit(limits: FlowLimits, *, agreed_window_frames: int | None = None) -> int:
    """The free frame credit of a shared startup snapshot, from *remote* bounds.

    Computed from the receiver's own limits - its free receive bytes divided by
    the fixed admitted maximum payload quantum, its reorder slots, its declared
    initial credit and the agreed send window - because those are the ceilings
    the peer actually enforces. A sender that has no shared snapshot starts at
    zero and waits for an explicit startup grant; it never treats its own local
    limit as evidence of room.
    """
    window = limits.window_frames if agreed_window_frames is None else agreed_window_frames
    if type(window) is not int or isinstance(window, bool) or window < 0:
        raise FlowError("invalid agreed window")
    by_bytes = limits.max_delivered_bytes // limits.frame_payload_bytes
    return max(0, min(window, limits.initial_credit_frames,
                      limits.max_reassembly_frames, by_bytes))


def frames_from_byte_grant(byte_grant: int, quantum: int) -> int:
    """Port ruling (a): a byte grant buys ``floor(byte_grant / quantum)`` frames.

    A grant smaller than one admitted quantum buys nothing. It is never clamped
    up to a single frame, because that would admit a frame the peer's byte
    ceiling cannot hold.
    """
    if type(byte_grant) is not int or isinstance(byte_grant, bool) or byte_grant < 0:
        raise FlowError("byte grant must be a non-negative int")
    if type(quantum) is not int or isinstance(quantum, bool) or quantum < 1:
        raise FlowError("byte grant quantum must be a positive int")
    return byte_grant // quantum


def admitted_payload_bound(*, contract_max_payload_bytes: int, profile_payload_capacity: int,
                           unaccounted_overhead_bytes: int = 0) -> int:
    """The admitted size of one complete serialized MT05 frame.

    ``CarrierProfile.payload_capacity`` already subtracts the profile's own
    named header and authentication overhead, so that overhead is not deducted
    a second time. Only overhead the profile does not know about is passed in,
    and it is deducted once.
    """
    for name, value in (("contract_max_payload_bytes", contract_max_payload_bytes),
                        ("profile_payload_capacity", profile_payload_capacity),
                        ("unaccounted_overhead_bytes", unaccounted_overhead_bytes)):
        if type(value) is not int or isinstance(value, bool) or value < 0:
            raise FlowError(f"invalid admitted bound: {name}")
    return min(contract_max_payload_bytes, profile_payload_capacity) - unaccounted_overhead_bytes


def data_frame_fits(quantum: int, bound: int) -> bool:
    """Whether a DATA quantum leaves room for the MT05 frame encoding inside it."""
    return 1 <= quantum and quantum + DATA_HEADER_BYTES + _CRC.size <= bound


def _before(a: int, b: int, modulus: int) -> bool:
    """True when ``a`` precedes ``b`` in modular order."""
    delta = (b - a) % modulus
    return 0 < delta < modulus // 2


def _crc(payload: bytes) -> bytes:
    return _CRC.pack(zlib.crc32(payload) & 0xFFFFFFFF)


def _check_field(value: int, limit: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FlowError(f"{name} must be an int")
    if not 0 <= value <= limit:
        raise FlowError(f"{name} out of range: {value}")
    return value


def build_data_frame(*, flow_id: int, generation: int, seq: int, total: int, last: bool,
                     payload: bytes, modulus: int = SEQ_MODULUS,
                     limits: FlowLimits | None = None) -> bytes:
    """Seal one data frame. Public so a test can inject a frame the sender never sent."""
    limits = limits or FlowLimits(seq_modulus=modulus)
    _check_field(flow_id, limits.max_flow_id, "flow_id")
    _check_field(generation, limits.max_generation, "generation")
    _check_field(seq, modulus - 1, "seq")
    if len(payload) > limits.frame_payload_bytes:
        raise FlowError("payload exceeds the negotiated frame size")
    flags = FLAG_LAST if last else 0
    body = _DATA.pack(MAGIC, KIND_DATA, flow_id, generation, flags, seq, total) + payload
    return body + _crc(body)


def build_ack_frame(*, flow_id: int, generation: int, ack_seq: int, credit: int, sack: int,
                    modulus: int = SEQ_MODULUS, limits: FlowLimits | None = None,
                    grant_revision: int = GRANT_REV_MIN) -> bytes:
    """Seal one acknowledgement. Public so a test can inject a snapshot the receiver never sent."""
    limits = limits or FlowLimits(seq_modulus=modulus)
    _check_field(flow_id, limits.max_flow_id, "flow_id")
    _check_field(generation, limits.max_generation, "generation")
    _check_field(ack_seq, modulus - 1, "ack_seq")
    if not 0 <= credit <= limits.max_reassembly_frames:
        raise FlowError("credit out of range")
    check_grant_revision(grant_revision)
    bitmap = (sack & ((1 << SACK_BITS) - 1)).to_bytes(SACK_BITS // 8, "big")
    body = _ACK.pack(MAGIC, KIND_ACK, flow_id, generation, ack_seq, credit,
                     SACK_BITS, grant_revision) + bitmap
    return body + _crc(body)


def parse_frame(frame: bytes, modulus: int = SEQ_MODULUS):
    """Return a parsed frame, or None when it is malformed in any way.

    Strict on purpose: a wrong length, an unknown flag, an out-of-range sequence
    or a bitmap whose declared size does not match the bytes present is refused
    rather than coerced.
    """
    if not isinstance(frame, (bytes, bytearray)) or len(frame) < MIN_FRAME_BYTES:
        return None
    if frame[:3] != MAGIC:
        return None
    if _crc(frame[:-4]) != frame[-4:]:
        return None
    kind = frame[3]
    try:
        if kind == KIND_DATA:
            _, _, flow_id, generation, flags, seq, total = _DATA.unpack(frame[: _DATA.size])
            if flags & ~KNOWN_FLAGS:
                return None
            if seq >= modulus or total < 1:
                return None
            payload = frame[_DATA.size:-_CRC.size]
            return {"kind": KIND_DATA, "flow_id": flow_id, "generation": generation,
                    "seq": seq, "last": bool(flags & FLAG_LAST), "total": total,
                    "payload": payload}
        if kind == KIND_ACK:
            _, _, flow_id, generation, ack_seq, credit, sack_len, grant_revision = _ACK.unpack(
                frame[: _ACK.size])
            # One fixed bitmap width. A wider frame that CRC-validates would
            # otherwise be read at the wrong offsets and acknowledge a sequence
            # the peer never named. A coherently declared 16/2-byte or 64/8-byte
            # width is refused on the declared width itself, so it is not mistaken
            # for the malformed older 19-byte or MT2 21-byte shapes.
            if sack_len != SACK_BITS or sack_len // 8 != len(frame) - _ACK.size - _CRC.size:
                return None
            if ack_seq >= modulus:
                return None
            if not GRANT_REV_MIN <= grant_revision <= GRANT_REV_MAX:
                return None
            bitmap = frame[_ACK.size:_ACK.size + sack_len // 8]
            return {"kind": KIND_ACK, "flow_id": flow_id, "generation": generation,
                    "ack_seq": ack_seq, "credit": credit, "grant_revision": grant_revision,
                    "sack": int.from_bytes(bitmap, "big") if bitmap else 0}
    except struct.error:
        return None
    return None


def build_ack_from_wire(frame: bytes, modulus: int = SEQ_MODULUS, *,
                        limits: FlowLimits | None = None) -> bytes | None:
    """Rebuild the canonical form of a sealed acknowledgement, or return None.

    A protocol refusal is never emitted on this lane under either signature, so
    encoding refusals back to protocol bytes is unnecessary. Both accepted
    layouts therefore canonicalise to the same bytes, and the comparison belongs
    to the flow that owns the snapshot rather than to its transport.

    The owning limits travel with the call. Rebuilding under the constructor's
    defaults does not merely restate the same frame: a flow configured with a
    smaller window or reassembly ceiling than those defaults refuses its own
    valid emission, because the credit it legitimately advertised is out of
    range for a configuration that never applied to it.
    """
    parsed = parse_frame(frame, modulus)
    if parsed is None or parsed["kind"] != KIND_ACK:
        return None
    return build_ack_frame(flow_id=parsed["flow_id"], generation=parsed["generation"],
                           ack_seq=parsed["ack_seq"], credit=parsed["credit"],
                           sack=parsed["sack"], modulus=modulus, limits=limits,
                           grant_revision=parsed["grant_revision"])


@dataclass
class _Pending:
    seq: int
    payload: bytes
    sent_at: float
    attempts: int
    rto: float
    # When the frame was handed to the link, as distinct from `sent_at`, which is
    # an estimate of when it finished transmitting. Only this one is a real past
    # instant, so only this one may be used to measure a round trip.
    handed_over_at: float = 0.0


class ReliableFlow:
    """One endpoint of one flow. Drive it with :meth:`pump`."""

    def __init__(self, endpoint: str, link, limits: FlowLimits | None = None, *,
                 flow_id: int = 1, generation: int = 1, sends: bool = True,
                 receive: bool = False, startup: StartupGrant | None = None,
                 agreed_startup_capacity_frames: int | None = None):
        if endpoint not in ("a", "b"):
            raise FlowError("unknown endpoint")
        if startup is not None and type(startup) is not StartupGrant:
            raise FlowError("startup must be a StartupGrant")
        self.limits = limits or FlowLimits()
        # The snapshot states how much room the *peer* has. A sender may not read
        # that out of its own limits, so the bound is either the explicit trusted
        # host/fixture value or - when the fixture is symmetric - this endpoint's
        # own declared receive capacity. A receiver additionally has to be able
        # to hold what the shared snapshot says it advertised, because a
        # snapshot it cannot honour is not a compatible one.
        if agreed_startup_capacity_frames is not None and (
                type(agreed_startup_capacity_frames) is not int
                or isinstance(agreed_startup_capacity_frames, bool)
                or agreed_startup_capacity_frames < 0):
            raise FlowError("agreed startup capacity must be a non-negative int")
        if startup is None and agreed_startup_capacity_frames is not None:
            raise FlowError("an agreed startup capacity needs a shared startup snapshot")
        self.agreed_startup_capacity_frames = agreed_startup_capacity_frames
        if startup is not None:
            local = remote_startup_credit(self.limits)
            if receive and startup.credit_frames > local:
                raise FlowError("startup grant exceeds the local receive capacity")
            bound = (agreed_startup_capacity_frames
                     if agreed_startup_capacity_frames is not None else local)
            if startup.credit_frames > bound:
                raise FlowError("startup grant exceeds the agreed remote capacity")
        self.endpoint = endpoint
        self.peer = "b" if endpoint == "a" else "a"
        self.link = link
        self.startup = startup
        self.flow_id = _check_field(flow_id, self.limits.max_flow_id, "flow_id")
        self.generation = _check_field(generation, self.limits.max_generation, "generation")
        self.seq_modulus = self.limits.seq_modulus
        self.failure: str | None = None

        # Directions are declared, never inferred from traffic: a finite
        # transfer in one direction must not be able to mark the other one
        # finished.
        self.send_state = FlowState.OPEN if sends else FlowState.COMPLETE
        self.recv_state = FlowState.OPEN if receive else FlowState.COMPLETE
        self._send_enabled = sends
        self._receive_enabled = receive

        self._peer_incarnation = link.incarnation(self.peer)
        self._outbound = bytearray()
        self._started = False
        self._next_seq = 0
        self._total = 0
        self._in_flight: dict[int, _Pending] = {}
        self._acked_frames = 0
        # Credit that is granted and then lost leaves queued data, an empty
        # window and nothing to expire. The probe clock is what keeps an event on
        # the flow in that state; `_probe_payload` is the last frame the receiver
        # is known to hold, which is the only frame a probe may repeat.
        self.impossible_rtt_samples = 0
        # The revision this endpoint stamps on the acknowledgements it sends, the
        # canonical tuple that revision belongs to, and the newest revision it has
        # seen from its peer. `None` means nothing is established yet: a sender
        # with no peer revision has no credit either, because a local limit is not
        # evidence of remote room.
        self._grant_revision: int | None = None
        self._grant_snapshot: tuple[int, int, int] | None = None
        self._grant_fenced = False
        # An ACK this endpoint has offered and the host still holds queued, with
        # the exact sealed bytes, the snapshot they carry and the revision they
        # were minted under. It is the canonical outcome record for that one
        # emission: the snapshot is committed when the frame really leaves, and
        # retracted without minting anything when the host settles it unsent. A
        # control frame carries no sequence number, so its exact bytes are its
        # identity on this lane. The generation it was minted at is appended as a
        # fifth element, after the four the review boundary already indexes, and
        # decides how much of the receive state the commit may clear; see
        # `_ack_work_gen`.
        self._ack_pending: (tuple[bytes, tuple[int, int, int], int, bool]
                            | tuple[bytes, tuple[int, int, int], int, bool, int]
                            | None) = None
        self._peer_grant_rev: int | None = None
        self._peer_grant_snapshot: tuple[int, int, int] | None = None
        self.stale_grants = 0
        self.conflicting_grants = 0
        self.repeat_grants = 0
        self._probe_at: float | None = None
        self._probe_attempts = 0
        self._probe_payload: bytes | None = None
        # Set only while a credit probe is queued under a live host handle and
        # waiting for its real handover. The probe repeats the bytes of a frame
        # that is no longer in flight, so without this marker a deferred probe
        # handover and the retry of those same bytes are indistinguishable.
        self._probe_outstanding = False
        # Retransmissions this flow has queued under a live handle and not yet
        # been told about, by sequence, with the exact bytes that were queued.
        # They are owed a handover even if their flight is acknowledged first.
        self._deferred_retries: dict[int, deque[bytes]] = {}
        # Sequence numbers whose frame state this flow has already spent on its
        # own synchronous handover, cleared as each one is acknowledged. A
        # control frame reports a handover too, and it borrows the sequence
        # number below the acknowledgement floor, so only an exact record of
        # what was really consumed keeps a stale report from eating a live frame.
        self._handed_over: set[int] = set()
        self.zero_window_probes = 0
        # Widened while the peer keeps answering "no room yet", so a closed window
        # is not probed once per round trip for as long as it stays closed.
        self._probe_gap_scale = 1.0

        self._expected = 0
        self._buffer: dict[int, bytes] = {}
        self._delivered = bytearray()
        self._received_frames = 0
        self._total_frames = 0
        self._stalled = False

        # Peer capacity is never inferred from this endpoint's own limits. A
        # sender with no explicitly established shared startup snapshot begins at
        # zero and needs an explicit startup grant from the receiver.
        self._peer_credit = 0
        self._ack_dirty = False
        self._reassert_pending = False
        # Monotonic counter of receive work raised since this flow was created.
        # Every site that marks receive state for reporting advances it, so a
        # queued emission can tell "nothing has happened since I was sealed" from
        # "the receiver has moved on and my snapshot is no longer the newest".
        self._ack_work_gen = 0
        self._last_ack_at = -1.0
        self._credit_advertised = self.limits.max_reassembly_frames

        if startup is not None:
            check_grant_revision(startup.revision, "startup grant revision")
            self._grant_revision = startup.revision
            self._grant_snapshot = startup.snapshot
            self._peer_grant_rev = startup.revision
            self._peer_grant_snapshot = startup.snapshot
            self._peer_credit = startup.credit_frames
        # Without a shared snapshot the peer holds no credit, so nothing would
        # ever speak and the flow would idle with no remaining event. The
        # receiver therefore states its first canonical snapshot - and, because
        # the grant that would restart the sender can be the one frame the
        # reverse link loses, re-states it a bounded number of times before
        # failing the direction. The sender's wait for that grant is bounded
        # too, so neither branch can idle forever with no remaining event.
        self.startup_pending = startup is None and receive
        self._startup_announce_left = self.limits.max_persist_acks
        self._startup_first_at: float | None = None
        self._startup_wait_at: float | None = None
        self._startup_wait_s = self.limits.persist_interval_s * (
            self.limits.max_persist_acks + self.limits.max_retries)

        self._srtt: float | None = None
        self._rttvar: float | None = None
        self._rto = self.limits.initial_rto_s

        self.stale_rejected = 0
        self.foreign_frames = 0
        self.duplicates = 0
        self.out_of_window = 0
        self.corrupt_rejected = 0
        self.retransmits = 0
        self.refused_sends = 0
        self.impossible_acks = 0
        self.stale_acks = 0
        self.window_refused_grants = 0
        self.out_of_transfer = 0
        self.post_terminal_rejected = 0
        self.duplicate_prompted_acks = 0
        self.oversized_rejected = 0
        self.invalid_total = 0
        self.invalid_fin = 0
        self.unexpected_data = 0
        self.peak_buffered_frames = 0
        self.peak_queued_bytes = 0
        self.peak_delivered_bytes = 0
        self._fenced = False
        self._ack_floor: int | None = None
        self._persist_left = self.limits.max_persist_acks
        self.deferred_sends = 0

        # A host link that keeps one queued frame per flow has to be able to
        # report the actual handover, because only then does the attempt and the
        # retry clock begin. The simulated carrier has no such notion, so the
        # hook is optional and its absence is the ordinary synchronous case.
        attach = getattr(link, "attach_flow", None)
        if callable(attach):
            attach(self)

    # -- application side ------------------------------------------------

    def send(self, data: bytes) -> None:
        """Queue one finite transfer. One flow carries one transfer; a second call is refused."""
        if self.send_state is not FlowState.OPEN:
            raise FlowError("flow is not open for sending")
        if self._started:
            raise FlowError("this flow already carries a transfer; open a new flow")
        if not data:
            raise FlowError("empty transfer")
        if len(data) > self.limits.max_queued_bytes:
            raise FlowError("queue ceiling")
        size = self.limits.frame_payload_bytes
        self._outbound += data
        self._total = (len(self._outbound) + size - 1) // size
        if self._total > self.limits.max_total_frames:
            self._outbound.clear()
            self._total = 0
            raise FlowError("transfer exceeds the negotiated frame count")
        self._started = True
        self.peak_queued_bytes = max(self.peak_queued_bytes, len(self._outbound))

    def consume(self, count: int) -> bytes:
        """Take delivered bytes. Credit is only returned by consuming them."""
        if count < 0:
            raise FlowError("negative consume")
        taken = bytes(self._delivered[:count])
        del self._delivered[:count]
        self._raise_ack_work()
        return taken

    @property
    def delivered(self) -> bytes:
        """Bytes received and not yet consumed."""
        return bytes(self._delivered)

    @property
    def state(self) -> FlowState:
        for value in (self.send_state, self.recv_state):
            if value in (FlowState.FAILED, FlowState.CANCELLED, FlowState.PEER_LOST):
                return value
        if self.send_state is FlowState.COMPLETE and self.recv_state is FlowState.COMPLETE:
            return FlowState.COMPLETE
        return FlowState.OPEN

    @property
    def complete(self) -> bool:
        return self.state is FlowState.COMPLETE

    @property
    def rto(self) -> float:
        return self._rto

    def buffered_frames(self) -> int:
        return len(self._buffer)

    def queued_bytes(self) -> int:
        return len(self._outbound)

    def in_flight(self) -> int:
        return len(self._in_flight)

    def acked_frames(self) -> int:
        """Frames this endpoint has had acknowledged, cumulatively or selectively."""
        return self._acked_frames

    def settled(self) -> bool:
        return not self._outbound and not self._in_flight

    def stall(self) -> None:
        """Model a consumer that stops taking delivery; credit must fall to zero."""
        self._stalled = True
        self._raise_ack_work()

    def unstall(self) -> None:
        self._stalled = False
        self._drain()
        self._raise_ack_work()

    def carrier_changed(self) -> None:
        """A route or lane change invalidates the round-trip estimate.

        Without this the old carrier's RTT drives retry deadlines on the new
        one, so a slower lane looks exactly like loss until the deadlines are
        renegotiated.
        """
        self._srtt = None
        self._rttvar = None
        self._rto = self.limits.initial_rto_s
        for pending in self._in_flight.values():
            pending.rto = self._rto

    def cancel(self) -> None:
        """Stop both directions and release bounded state; a cancel is never a completion."""
        self._outbound.clear()
        self._in_flight.clear()
        self._buffer.clear()
        self._delivered.clear()
        # Session-wide output fence. The completed-receive duplicate response is
        # a live-session repair and must not survive the session it belonged to.
        self._fenced = True
        if self.send_state is FlowState.OPEN:
            self.send_state = FlowState.CANCELLED
        if self.recv_state is FlowState.OPEN:
            self.recv_state = FlowState.CANCELLED
        self.failure = self.failure or "cancelled"

    def grant_startup(self) -> None:
        """Receiver side: state the current canonical snapshot without being asked.

        A sender that holds no shared startup snapshot has zero credit, so
        something has to speak first. This only marks the snapshot dirty; it
        grants nothing until :meth:`pump` actually sends it and the peer accepts
        that revision. A fixture that establishes a shared snapshot at both
        endpoints does not need it.
        """
        self._raise_ack_work()

    # -- driving ---------------------------------------------------------

    def pump(self, now: float, *, max_frames: int | None = None, drain: bool = True) -> None:
        if self.link.incarnation(self.peer) != self._peer_incarnation:
            self._fail_session("peer_restarted")
        if drain:
            self._receive(now)
        if self.send_state is FlowState.OPEN:
            self._expire(now)
        # Acks keep flowing after this endpoint's receive direction finishes: the
        # peer still has to learn that its final frames arrived, and that is the
        # only way a lost final ACK is repaired.
        if not self._fenced and self.recv_state in (FlowState.OPEN, FlowState.COMPLETE):
            self._emit_ack(now)
        if self.send_state is FlowState.OPEN:
            self._transmit(now, max_frames=max_frames)
            self._settle_send_state()
            self._probe_credit(now, max_frames=max_frames)
        self.peak_buffered_frames = max(self.peak_buffered_frames, len(self._buffer))
        self.peak_delivered_bytes = max(self.peak_delivered_bytes, len(self._delivered))

    def _fail_session(self, reason: str) -> None:
        # A whole-session end fences output exactly as a cancel does. A completed
        # receive direction is not resurrected by it - `recv_state` stays COMPLETE
        # - but the duplicate re-assertion it may still emit is a *live-session*
        # repair. After a peer restart or a lock it would be answering for a
        # session that no longer exists, so the fence covers every session end,
        # not only the one the user asked for.
        self._fenced = True
        if self.send_state is FlowState.OPEN:
            self.send_state = FlowState.FAILED
        if self.recv_state is FlowState.OPEN:
            self.recv_state = FlowState.FAILED
        self.failure = self.failure or reason

    def _fail_send(self, reason: str) -> None:
        if self.send_state is FlowState.OPEN:
            self.send_state = FlowState.FAILED
            self.failure = self.failure or reason

    def _fail_recv(self, reason: str) -> None:
        if self.recv_state is FlowState.OPEN:
            self.recv_state = FlowState.FAILED
            self.failure = self.failure or reason

    def _receive(self, now: float) -> None:
        for delivery in self.link.inbound(self.endpoint):
            frame = parse_frame(delivery.payload, self.seq_modulus)
            if frame is None:
                self.corrupt_rejected += 1
                continue
            self.absorb(frame, now)

    def absorb(self, frame: dict, now: float) -> None:
        """Apply one already-parsed frame.

        A lone flow reads its own link through :meth:`pump`. When several flows
        share a link the mux demultiplexes by flow id and calls this instead.
        Admission is fenced in this order: flow id, generation, direction
        eligibility, then terminal state.
        """
        if frame["flow_id"] != self.flow_id:
            self.foreign_frames += 1
            return
        if self._fenced:
            self.post_terminal_rejected += 1
            return
        if frame["generation"] != self.generation:
            self.stale_rejected += 1
            return
        if frame["kind"] == KIND_DATA:
            if not self._receive_enabled:
                self.unexpected_data += 1
                return
            if self.recv_state in (FlowState.FAILED, FlowState.CANCELLED, FlowState.PEER_LOST):
                self.post_terminal_rejected += 1
                return
            if self.recv_state is FlowState.COMPLETE:
                # A duplicate of something already admitted re-asserts receipt so
                # a lost ACK can be repaired. Anything new after completion is
                # refused, so terminal state cannot be resurrected.
                if self._already_admitted(frame["seq"]):
                    self._reassert()
                else:
                    self.post_terminal_rejected += 1
                return
            self._accept(frame)
            return
        if self.send_state is not FlowState.OPEN:
            self.post_terminal_rejected += 1
            return
        self._on_ack(frame, now)

    # -- receiver --------------------------------------------------------

    def _already_admitted(self, seq: int) -> bool:
        return _before(seq, self._expected, self.seq_modulus) or seq in self._buffer

    def _raise_ack_work(self) -> None:
        """Mark receive state for reporting, and record that it is newer.

        Every route that makes an acknowledgement necessary comes through here so
        that one counter answers the only question a queued emission has to ask
        before it retires receive work: has anything happened since I was sealed?
        """
        self._ack_work_gen += 1
        self._ack_dirty = True

    def _reassert(self) -> None:
        self._ack_work_gen += 1
        self._ack_dirty = True
        self._reassert_pending = True

    def _accept(self, frame: dict) -> None:
        payload = frame["payload"]
        if len(payload) > self.limits.frame_payload_bytes:
            self.oversized_rejected += 1
            return
        # A frame from the peer proves it heard this endpoint's first canonical
        # statement, so the bounded startup reannouncement is answered.
        self.startup_pending = False
        total = frame["total"]
        if total > self.limits.max_total_frames:
            self.invalid_total += 1
            return
        if self._total_frames and total != self._total_frames:
            self.invalid_total += 1
            return
        # FIN semantics: only the last frame of the declared transfer may claim
        # LAST. A frame that claims it early is refused before any mutation.
        if frame["last"] and frame["seq"] != (total - 1) % self.seq_modulus:
            self.invalid_fin += 1
            return
        # An already-admitted frame is a duplicate whatever the declared span
        # says, and it is recognised *before* that test rather than after it.
        # Running the span test first made a lost cumulative ACK unrecoverable:
        # the sender's retransmission of data this receiver had already delivered
        # was refused as out-of-transfer, so it never reached the duplicate
        # branch, never re-asserted receipt, and retried to its ceiling while the
        # receiver sat complete. Only frames behind `_expected` are recognised
        # here, and only once something has been delivered: a frame before the
        # start of a transfer that has delivered nothing is still refused by the
        # span test below, which is where it was refused before. The persistence
        # budget resets exactly as it does for every other admitted frame.
        seq = frame["seq"]
        if self._received_frames and _before(seq, self._expected, self.seq_modulus):
            self._total_frames = total
            self._persist_left = self.limits.max_persist_acks
            self.duplicates += 1
            self._reassert()
            return
        # Admission is against the remaining absolute transfer span, not just the
        # reorder window: a frame past the declared end is not part of this
        # transfer however well ordered it looks. The first frame to arrive
        # declares the span, so its own total is the one it is judged against.
        span = (seq - self._expected) % self.seq_modulus
        if self._received_frames + span >= (self._total_frames or total):
            self.out_of_transfer += 1
            return
        self._total_frames = total
        self._persist_left = self.limits.max_persist_acks

        if self._stalled:
            self._buffer_frame(seq, payload)
            return
        if seq == self._expected:
            if self._deliver_frame(payload):
                self._drain()
            return
        if seq in self._buffer:
            self.duplicates += 1
            self._reassert()
            return
        self._buffer_frame(seq, payload)

    def _buffer_frame(self, seq: int, payload: bytes) -> None:
        span = (seq - self._expected) % self.seq_modulus
        if span >= self.limits.max_reassembly_frames:
            self.out_of_window += 1
            return
        if seq in self._buffer:
            self.duplicates += 1
            self._reassert()
            return
        self._buffer[seq] = payload
        self._raise_ack_work()

    def _deliver_frame(self, payload: bytes) -> bool:
        if self._total_frames and self._received_frames >= self._total_frames:
            self.out_of_transfer += 1
            return False
        if len(self._delivered) + len(payload) > self.limits.max_delivered_bytes:
            self._fail_recv("receive_ceiling")
            return False
        self._delivered += payload
        self._received_frames += 1
        self._expected = (self._expected + 1) % self.seq_modulus
        self._raise_ack_work()
        if self._total_frames and self._received_frames >= self._total_frames:
            self.recv_state = FlowState.COMPLETE
        return True

    def _drain(self) -> None:
        while self._expected in self._buffer:
            payload = self._buffer[self._expected]
            if len(self._delivered) + len(payload) > self.limits.max_delivered_bytes:
                # Wait for the consumer instead of failing an honest reordered
                # flow. The frame keeps the byte capacity it already reserved.
                return
            self._buffer.pop(self._expected)
            if not self._deliver_frame(payload):
                return

    def _credit(self) -> int:
        if self._stalled:
            return 0
        # Every held reorder frame is charged the fixed admitted maximum payload
        # quantum rather than whatever it happens to carry, and every unconsumed
        # delivered byte is charged as itself: the snapshot has to cover both,
        # and rounding the conservative figure down is what stops a short frame
        # from buying room the receiver cannot actually hold.
        quantum = self.limits.frame_payload_bytes
        used = len(self._buffer) * quantum + len(self._delivered)
        room_frames = max(0, self.limits.max_delivered_bytes - used) // quantum
        return max(0, min(self.limits.max_reassembly_frames - len(self._buffer), room_frames))

    def _sack_bitmap(self) -> int:
        bitmap = 0
        for offset in range(1, SACK_BITS + 1):
            if (self._expected + offset) % self.seq_modulus in self._buffer:
                bitmap |= 1 << (SACK_BITS - offset)
        return bitmap

    def _snapshot_revision(self, snapshot: tuple[int, int, int]) -> int | None:
        """The revision for ``snapshot``, minted only when the tuple has moved.

        Repeated, persisted and probe-answer copies of one canonical tuple keep
        the revision they were minted with, so a peer can tell "the same state
        again" from "a newer state". A changed tuple needs a higher revision, and
        the counter has no wrap: at the maximum this direction fences instead of
        reusing a statement that already described different bytes.
        """
        if self._grant_revision is not None and snapshot == self._grant_snapshot:
            return self._grant_revision
        if self._grant_revision is None:
            return GRANT_REV_MIN
        if self._grant_revision >= GRANT_REV_MAX:
            self._grant_fenced = True
            self._fail_recv("grant_revision_exhausted")
            return None
        return self._grant_revision + 1

    def _emit_ack(self, now: float) -> bool:
        if self._grant_fenced:
            return False
        # The startup branch is bounded twice over: by how many statements were
        # emitted, and by how long the branch has been open. The count alone is
        # not enough, because a link that refuses the emission outright never
        # spends it - and that is the same "no remaining event" state this branch
        # exists to close. The bound is checked on every call, whether or not
        # there is something new to report.
        if self.startup_pending:
            if self._startup_first_at is None:
                self._startup_first_at = now
            if (self._startup_announce_left <= 0
                    or now - self._startup_first_at > self._startup_wait_s):
                self._fail_recv("startup_grant_unanswered")
                return False
        credit = self._credit()
        if credit != self._credit_advertised:
            self._raise_ack_work()
        persist = False
        if not self._ack_dirty:
            # Nothing new to report. Two bounded reasons to speak anyway: the
            # sender may be waiting on a grant this link already lost, or no
            # shared startup snapshot exists at all and this endpoint's first
            # canonical statement is the only thing that can unblock the flow -
            # and that one can be lost too.
            waiting = (self._total_frames and self._received_frames < self._total_frames
                       and credit > 0 and self._last_ack_at >= 0)
            persist = bool(waiting and self._persist_left > 0)
            if not persist and not self.startup_pending:
                return False
            if self._last_ack_at >= 0 and now - self._last_ack_at < self.limits.persist_interval_s:
                return False
        # The rate bound covers every ACK, including a completed receiver's
        # duplicate response: three duplicates in one instant are one ACK.
        if self._last_ack_at >= 0 and now - self._last_ack_at < self.limits.ack_delay_s:
            return False
        snapshot = (self._expected, self._sack_bitmap(), credit)
        revision = self._snapshot_revision(snapshot)
        if revision is None:
            return False
        frame = build_ack_frame(flow_id=self.flow_id, generation=self.generation,
                                ack_seq=self._expected, credit=credit,
                                sack=self._sack_bitmap(), modulus=self.seq_modulus,
                                limits=self.limits, grant_revision=revision)
        outcome = self.link.send(self.endpoint, frame, kind="ack")
        if outcome is DEFERRED:
            # Queued under a live handle, not handed over: a shared dispatch
            # gave the slot to another flow, so the acknowledgement carries
            # nothing yet. The record keeps the exact bytes, the canonical
            # snapshot they state and the revision minted for them, and every
            # bounded counter stays unspent until the frame really leaves.
            # Without it the next snapshot change mints this same revision for
            # different bytes and the peer refuses the newer statement as a
            # conflicting reuse of one it already holds. The generation sealed
            # with it is what keeps that record from also retiring receive work
            # that had not happened yet when the frame was built.
            self._ack_pending = (frame, snapshot, revision, persist, self._ack_work_gen)
            return False
        if outcome:
            # The snapshot is committed only once it is actually on the wire. A
            # refused emission mints no revision and replenishes no grant, so a
            # transport refusal cannot create canonical state. Nothing can have
            # arrived between sealing and sending on this path, so the commit is
            # allowed to retire the work it was built from.
            self._commit_ack(frame, snapshot, revision, now=now, persist=persist)
            return True
        return False

    def _commit_ack(self, frame: bytes, snapshot: tuple[int, int, int], revision: int, *,
                    now: float, persist: bool, work_gen: int | None = None) -> None:
        """Make one state actually on the wire this endpoint's canonical snapshot.

        The synchronous emission and the delayed handover both settle here, so a
        host that enqueues now and dispatches later cannot record a different
        canonical state from one that dispatches immediately. A handover for an
        emission this endpoint has already spent is ignored, which is what keeps
        one emission to exactly one commitment.

        What the commit retires is bounded by what the emission actually stated.
        The recorded snapshot and revision are always settled - that emission
        left, and it minted that revision. The receive-state flags are settled
        only when no newer work has been raised since the frame was sealed: an
        older queued acknowledgement states a base, a SACK and a credit that were
        true when it was built, so letting it clear work that arrived afterwards
        loses the only signal that a newer statement is owed. Equal credit does
        not make two acknowledgements equal; cumulative base and selective
        receipt move independently of it. ``work_gen`` is ``None`` when the
        caller is the synchronous path, where no such window exists.
        """
        if self._ack_pending is not None and self._ack_pending[0] != frame:
            return
        self._ack_pending = None
        self._grant_snapshot = snapshot
        self._grant_revision = revision
        superseded = work_gen is not None and self._ack_work_gen != work_gen
        if self._reassert_pending and not superseded:
            self.duplicate_prompted_acks += 1
            self._reassert_pending = False
        if not superseded:
            self._ack_dirty = False
        self._last_ack_at = now
        self._credit_advertised = snapshot[2]
        if persist:
            self._persist_left -= 1
        if self.startup_pending:
            self._startup_announce_left -= 1

    # -- sender ----------------------------------------------------------

    def _transmit(self, now: float, *, max_frames: int | None) -> None:
        # A sender with no established peer snapshot has no credit and no
        # acknowledged frame, so nothing would ever expire and no probe is
        # eligible. That is the one state with no remaining event, so the wait
        # for a first grant is bounded here and ends explicitly.
        if self._outbound and self._peer_grant_rev is None:
            if self._startup_wait_at is None:
                self._startup_wait_at = now
            elif now - self._startup_wait_at > self._startup_wait_s:
                self._fail_send("startup_grant_absent")
                return
        sent = 0
        allowance = min(self.limits.window_frames, max(0, self._peer_credit))
        while self._outbound and len(self._in_flight) < allowance:
            if max_frames is not None and sent >= max_frames:
                return
            size = self.limits.frame_payload_bytes
            payload = bytes(self._outbound[:size])
            seq = self._next_seq
            frame = build_data_frame(flow_id=self.flow_id, generation=self.generation,
                                     seq=seq, total=self._total, last=len(self._outbound) <= size,
                                     payload=payload, modulus=self.seq_modulus, limits=self.limits)
            outcome = self.link.send(self.endpoint, frame)
            if outcome is DEFERRED:
                # Queued under a live handle, not handed over. No byte, sequence
                # number or retry deadline is consumed, so a host admission
                # delay cannot start the attempt early, and a retry cannot be
                # armed against a frame that has not left.
                self.deferred_sends += 1
                return
            if not outcome:
                self.refused_sends += 1
                return
            self._consume_handover(seq, payload, now)
            sent += 1

    def _consume_handover(self, seq: int, payload: bytes, now: float) -> None:
        """Take exactly the state one frame consumes when it is handed over."""
        del self._outbound[:len(payload)]
        self._in_flight[seq] = _Pending(seq=seq, payload=payload,
                                        sent_at=now + self._airtime_delay(),
                                        attempts=1, rto=self._rto,
                                        handed_over_at=now)
        self._next_seq = (seq + 1) % self.seq_modulus
        self._handed_over.add(seq)

    def handover(self, seq: int, *, at: float) -> bool:
        """Record that a frame this endpoint had queued was actually handed over.

        A host link reports this when a frame it accepted as ``DEFERRED`` is
        finally dispatched, so the attempt begins at the real handover instant
        rather than at the earlier enqueue. The same emission can be a fresh
        DATA frame, a retransmission that expired while it was queued, or a
        credit probe, and each one settles different state: charging a deferred
        retry or probe as though nothing had happened leaves the owner's attempt
        count ahead of the flow's and lets the next pump start a further attempt
        against a clock that never restarted.
        """
        if self.send_state is not FlowState.OPEN:
            return False
        # A retry or a probe repeats an emission that was already spent, so both
        # are settled before anything asks whether this sequence was consumed:
        # the retry of a frame this flow sent synchronously reuses its sequence
        # number, and it is a real second emission rather than a repeat report.
        if seq in self._deferred_retries:
            # A retransmission this flow queued and is still owed a handover for.
            # It outlives its own pending flight: the acknowledgement it was
            # answering may retire that flight while the frame is still waiting
            # for the host, and the flow would otherwise never learn this
            # emission left - leaving the one held frame to block every later
            # offer for good. A sequence may be queued more than once, because a
            # refusal re-offers the same bytes under the same sequence number, so
            # the oldest outstanding one is settled first.
            queued = self._deferred_retries[seq]
            payload = queued.popleft()
            if not queued:
                del self._deferred_retries[seq]
            pending = self._in_flight.get(seq)
            if pending is not None and pending.payload != payload:
                pending = None
            self._settle_retry(pending, at)
            return True
        if self._probe_outstanding and self._in_flight.get(seq) is None:
            # The probe repeats the last frame the receiver acknowledged, so it
            # names a sequence that is not in flight and its bytes are identical
            # to that frame's. Only the marker the deferred emission set can
            # tell this handover apart from the retry of the same bytes, and a
            # probe is only ever queued with nothing else in flight.
            self._probe_outstanding = False
            self._settle_probe(at)
            return True
        if seq in self._handed_over:
            # Already spent. The synchronous path consumed this frame's byte and
            # sequence when the link took it, so a report of the same emission
            # describes a handover that has already happened.
            return False
        if self._outbound and seq == self._next_seq:
            payload = bytes(self._outbound[:self.limits.frame_payload_bytes])
            self._consume_handover(seq, payload, at)
            return True
        return False

    def _expire(self, now: float) -> None:
        for pending in list(self._in_flight.values()):
            if now - pending.sent_at < pending.rto:
                continue
            if pending.attempts >= self.limits.max_retries:
                self._fail_send("retry_ceiling")
                return
            frame = build_data_frame(flow_id=self.flow_id, generation=self.generation,
                                     seq=pending.seq, total=self._total,
                                     last=len(pending.payload) < self.limits.frame_payload_bytes,
                                     payload=pending.payload, modulus=self.seq_modulus,
                                     limits=self.limits)
            outcome = self.link.send(self.endpoint, frame)
            if outcome is DEFERRED:
                # The retransmission is already queued under a live handle. It
                # keeps its place and its clock, so a host that has not
                # dispatched it cannot be charged with a second attempt - but it
                # is remembered, because the acknowledgement it was answering
                # may still arrive and retire the pending frame before the host
                # gets round to dispatching it. The handover is owed either way.
                self.deferred_sends += 1
                # One queued reservation carries exactly one outcome record. The
                # flow re-offers the same bytes on every pump while the host holds
                # them, so appending unconditionally would grow a record per call
                # against a single held frame: 1000 identical pumps would leave
                # 1000 markers for one emission and a single real handover would
                # remove one, stranding the other 999.
                outstanding = self._deferred_retries.setdefault(pending.seq, deque())
                if pending.payload not in outstanding:
                    outstanding.append(pending.payload)
                continue
            if not outcome:
                self.refused_sends += 1
                continue
            self._settle_retry(pending, now)

    def _settle_retry(self, pending: _Pending | None, at: float) -> None:
        """Spend exactly one retry attempt on a frame that has really been handed over.

        One emission consumes one retry attempt, one new retry clock and one
        retransmission, and no new DATA: the byte and the sequence number were
        taken when this frame was first admitted. The synchronous expiry path and
        the deferred handover path both settle here so that a host which enqueues
        now and dispatches later cannot record a different attempt from one that
        dispatches immediately.

        A retransmission may be dispatched after the acknowledgement it was
        answering has retired the original flight. It is still a frame that
        really left, so it is still counted; there is simply no pending flight
        left for it to advance.
        """
        if pending is not None:
            pending.attempts += 1
            pending.sent_at = at + self._airtime_delay()
            pending.rto = min(self.limits.max_rto_s,
                              max(self.limits.min_rto_s, pending.rto * 2))
            pending.handed_over_at = at
        self.retransmits += 1
        # A retransmission has taken this handover, so no queued probe is still
        # waiting for one. A probe is only ever queued with nothing in flight,
        # which is why a retry and a probe can never both be outstanding.
        self._probe_outstanding = False

    def _settle_probe(self, at: float) -> None:
        """Spend exactly one bounded silence attempt on a handover credit probe.

        The probe is the duplicate the retransmission lane carries and not a new
        admission, so it takes no sequence number, no byte and no new DATA slot.
        Only the probe's own silence clock moves, and it starts from the moment
        the frame actually left rather than from the moment it was queued.
        """
        self.zero_window_probes += 1
        self._probe_attempts += 1
        self._probe_at = at + self._probe_interval()

    def handover_cancelled(self, seq: int, frame: bytes) -> bool:
        """Retract the record for an emission the host will never hand over.

        The exact terminal outcome for this frame is already known and it is not
        a handover: the carrier was invoked and refused, or the host settled the
        frame as unsent. Either way the queued emission is finished, so the
        record it left has to be removed here. Leaving it behind is the mirror of
        the abandoned hold - a marker nothing will ever consume, accumulating one
        per offer while the flow waits for a handover that is not coming.

        The caller holds the encoded frame, while the record is keyed by the
        application bytes the frame carries, so the comparison is made by
        re-encoding the record's own payload the same way the retry lane encodes
        it. Nothing else moves: no attempt, no retry clock, no retransmission and
        no DATA byte. The flow still owns its frame and re-offers it unchanged.
        """
        queued = self._deferred_retries.get(seq)
        if not queued:
            return False
        for payload in list(queued):
            attempt = build_data_frame(flow_id=self.flow_id, generation=self.generation,
                                       seq=seq, total=self._total,
                                       last=len(payload) < self.limits.frame_payload_bytes,
                                       payload=payload, modulus=self.seq_modulus,
                                       limits=self.limits)
            if attempt == frame:
                queued.remove(payload)
                if not queued:
                    del self._deferred_retries[seq]
                return True
        return False

    def probe_handover_cancelled(self, seq: int, frame: bytes) -> bool:
        """Retract a queued credit probe the host will never hand over.

        A probe that was enqueued and then refused by the carrier, or settled as
        unsent by the scheduler, has reached its terminal outcome without ever
        leaving. The marker it set is the only record that the emission is owed a
        handover, so leaving it behind keeps a spent obligation open: the flow
        waits on a frame that will not come, and the *next* real handover for that
        sequence is classified as the probe's, so a fresh DATA frame takes the
        abandoned probe's identity and the probe is charged an attempt the radio
        never made.

        The record is retracted only for the exact probe this flow queued: the
        probe repeats the last frame the receiver acknowledged, and its bytes and
        sequence have to match that emission, not merely the sequence number. No
        attempt is spent, no probe clock moves and no DATA byte is admitted - the
        probe interval that was already armed stays where it is, so the flow asks
        again on its own schedule.
        """
        if not self._probe_outstanding or self._in_flight:
            return False
        if self._probe_payload is None or self._ack_floor is None:
            return False
        probe_seq = (self._ack_floor - 1) % self.seq_modulus
        if seq != probe_seq:
            return False
        attempt = build_data_frame(flow_id=self.flow_id, generation=self.generation,
                                   seq=probe_seq, total=self._total, last=False,
                                   payload=self._probe_payload, modulus=self.seq_modulus,
                                   limits=self.limits)
        if attempt != frame:
            return False
        self._probe_outstanding = False
        return True

    def ack_handover(self, frame: bytes, *, at: float) -> bool:
        """Commit the canonical snapshot of a queued acknowledgement that left.

        A control frame carries no sequence number, so the host identifies it by
        the exact bytes it queued and this endpoint rebuilds its own canonical
        form to compare against them. Only that exact emission is settled, and
        the revision travels with the record rather than being re-derived here:
        re-deriving would read a revision from whatever the snapshot has since
        become, which is exactly the newer statement this handover is not. The
        acknowledgement clock starts at the real dispatch instant rather than at
        the earlier enqueue, the same way the retry and probe clocks do.
        """
        pending = self._ack_pending
        if pending is None:
            return False
        wire, snapshot, revision, persist, work_gen = pending
        if build_ack_from_wire(frame, self.seq_modulus, limits=self.limits) != wire:
            return False
        self._commit_ack(wire, snapshot, revision, now=at, persist=persist,
                         work_gen=work_gen)
        return True

    def ack_handover_cancelled(self, frame: bytes) -> bool:
        """Retract the record for an acknowledgement the host will never send.

        The emission reached its terminal outcome without leaving, so no
        revision is minted, no grant is replenished and no counter is spent. The
        flow still holds its dirty receive state and re-offers the
        acknowledgement on its own schedule.
        """
        pending = self._ack_pending
        if pending is None:
            return False
        if build_ack_from_wire(frame, self.seq_modulus, limits=self.limits) != pending[0]:
            return False
        self._ack_pending = None
        return True

    def _airtime_delay(self) -> float:
        """Seconds until the frame just handed over has finished transmitting.

        A serial radio holds everything queued behind it. Arming the retry clock
        at the moment of queueing makes a full window on a slow carrier look
        exactly like loss, which is the same mistake as starting the RTO too low.
        """
        try:
            capacity = float(self.link.profile(self.endpoint).capacity_bytes_per_second)
            ahead = self.link.queued_bytes(self.endpoint)
        except Exception:
            return 0.0
        return ahead / capacity if capacity > 0 else 0.0

    def _probe_interval(self) -> float:
        """How long to wait for an answer before asking the receiver again.

        Backed off while the only answer is "no room yet": a peer whose window is
        genuinely closed is not a fault, and probing it once per round trip would
        be chatter for as long as its application takes to consume.
        """
        base = min(self.limits.max_rto_s, max(self.limits.min_rto_s, self._rto))
        return min(self.limits.max_rto_s, base * self._probe_gap_scale)

    def _probe_credit(self, now: float, *, max_frames: int | None) -> None:
        """Ask a silent receiver for its current window, or fail explicitly.

        The state this exists for has no other event left in it: the application
        consumed, the receiver granted credit, the grant was lost, and every
        bounded persistence re-announcement went with it. Nothing is in flight,
        so nothing expires; nothing is received, so nothing resets. The sender
        would sit open forever holding queued data.

        The probe is a retransmission of the last frame the receiver *acknowledged*,
        so it is a duplicate by construction: it cannot admit anything past a
        genuinely closed window, and the receiver answers a duplicate with its
        current receipt and credit. That answer is the recovery — and it is also
        why an answered zero-credit probe is not a failure: a peer that keeps
        saying "no room yet" is alive, and the wait belongs to its application.
        Only consecutive silence is bounded, and then the flow ends in `credit_stalled`
        rather than idling with no remaining event.
        """
        if max_frames == 0 or self._in_flight or not self._outbound or self._peer_credit > 0:
            self._probe_at = None
            self._probe_attempts = 0
            return
        # With nothing acknowledged there is no frame the receiver is known to
        # hold, and a "probe" would be new data aimed at a closed window.
        if self._acked_frames == 0 or self._probe_payload is None:
            self._probe_at = None
            return
        if self._probe_at is None:
            self._probe_at = now + self._probe_interval()
            return
        if now < self._probe_at:
            return
        if self._probe_attempts >= self.limits.max_retries:
            self._fail_send("credit_stalled")
            return
        frame = build_data_frame(flow_id=self.flow_id, generation=self.generation,
                                 seq=(self._ack_floor - 1) % self.seq_modulus, total=self._total,
                                 last=False, payload=self._probe_payload,
                                 modulus=self.seq_modulus, limits=self.limits)
        outcome = self.link.send(self.endpoint, frame)
        if outcome is DEFERRED:
            # Queued under a live handle. It is not an answered probe and not a
            # refusal, so the probe clock waits for the host rather than
            # spending an attempt on a frame that has not left.
            self.deferred_sends += 1
            self._probe_outstanding = True
            self._probe_at = now + self._probe_interval()
            return
        if not outcome:
            self.refused_sends += 1
            self._probe_at = now + self._probe_interval()
            return
        self.zero_window_probes += 1
        self._probe_attempts += 1
        self._probe_at = now + self._probe_interval()

    def _ack_admissible(self, frame: dict) -> bool:
        """An ACK may only account for sequence numbers this endpoint actually sent."""
        acked = frame["ack_seq"]
        if not (acked == self._next_seq or _before(acked, self._next_seq, self.seq_modulus)):
            return False
        if not 0 <= frame["credit"] <= self.limits.max_reassembly_frames:
            return False
        for offset in range(1, SACK_BITS + 1):
            if not frame["sack"] & (1 << (SACK_BITS - offset)):
                continue
            position = (acked + offset) % self.seq_modulus
            if not _before(position, self._next_seq, self.seq_modulus):
                return False
        return True

    def _on_ack(self, frame: dict, now: float) -> None:
        revision = frame["grant_revision"]
        if not self._ack_admissible(frame):
            self.impossible_acks += 1
            return
        acked = frame["ack_seq"]
        snapshot = (acked, frame["sack"], frame["credit"])
        previous_floor = self._ack_floor
        # Cumulative progress only moves forward. An older acknowledgement - even
        # a CRC-valid one - may not roll the credit back and idle the sender.
        if self._ack_floor is not None and _before(acked, self._ack_floor, self.seq_modulus):
            self.stale_acks += 1
            return
        # Freshness is decided before anything is mutated, and it decides the
        # whole acknowledgement. A revision older than the one held is ignored
        # entirely: it may not move credit, retire receipt, or touch probe,
        # silence or retry timing, because every one of those is a capacity
        # decision an old statement is not entitled to make. An equal revision is
        # idempotent only for an identical canonical tuple, and is refused without
        # mutation otherwise. Advancing the cumulative base never bypasses this:
        # the revision is checked here, not after the receipt has been applied.
        held = self._peer_grant_rev
        grant_credit, repeat = False, False
        if held is None:
            grant_credit = True
        elif revision < held:
            self.stale_grants += 1
            return
        elif revision == held:
            if snapshot != self._peer_grant_snapshot:
                self.conflicting_grants += 1
                return
            # The same canonical tuple may repair receipt again - that is how a
            # lost acknowledgement is recovered - but it cannot replenish
            # capacity, and it cannot retire one attempt twice because a retired
            # attempt is no longer in flight to be found again.
            repeat = True
        else:
            grant_credit = True
        # Retirement is computed without mutating, because the canonical snapshot
        # has to be offered to a host owner that mirrors the same credit *before*
        # either side commits. A snapshot the owner refuses as wider than the
        # negotiated byte/window contract refuses the whole acknowledgement, with
        # nothing moved on either side.
        newly = []
        for seq, pending in self._in_flight.items():
            covered = _before(seq, acked, self.seq_modulus)
            offset = (seq - acked) % self.seq_modulus
            if covered or (0 < offset <= SACK_BITS and frame["sack"] & (1 << (SACK_BITS - offset))):
                newly.append(pending)
        if not self._reflect_receipt(acked, [pending.seq for pending in newly],
                                     frame["credit"], canonical=grant_credit):
            self.window_refused_grants += 1
            return
        self._ack_floor = acked
        if repeat:
            self.repeat_grants += 1
        if grant_credit:
            self._peer_credit = frame["credit"]
            self._peer_grant_rev = revision
            self._peer_grant_snapshot = snapshot
        # The peer spoke, whatever it granted, so the startup wait is answered.
        self.startup_pending = False
        self._startup_wait_at = None
        for pending in newly:
            del self._in_flight[pending.seq]
            self._handed_over.discard(pending.seq)
        for pending in newly:
            self._acked_frames += 1
            if pending.attempts == 1:
                # Measured from the handover instant, never from `sent_at`. That
                # field is an estimate of when the link would finish the frame,
                # built from a queue-depth reading that counts duplicated bytes
                # while the radio serialises one slot: with a simulated duplicate
                # it overstates the delay, and the acknowledgement arrived at
                # 0.11s against an estimate of 0.158s, which a sample taken from
                # it turns into a *negative* round trip. A measurement must come
                # from a real past instant, and this one cannot precede it.
                self._sample_rtt(now - pending.handed_over_at)
            # The newest frame the receiver is known to hold: the only frame a
            # credit probe may repeat without aiming new data at a closed window.
            if pending.seq == (acked - 1) % self.seq_modulus:
                self._probe_payload = pending.payload
        # A valid acknowledgement is the peer speaking, whatever it granted. The
        # silence is what is bounded, not the wait for an application to consume.
        # An answer that grants nothing is progress of a kind - the peer is alive -
        # so it clears the silence count, but it also widens the gap: the window is
        # still closed and re-asking on every round trip would be chatter. Only a
        # grant or an advance of the cumulative floor is recovery.
        moved = (previous_floor is not None and _before(previous_floor, acked, self.seq_modulus)) or frame["credit"] > 0
        # No artificial ceiling on the scale: the interval itself saturates at
        # `max_rto_s`, so a long-closed window is re-asked every eight seconds
        # however small the round trip is.
        self._probe_gap_scale = 1.0 if moved else self._probe_gap_scale * 2
        self._probe_attempts = 0
        self._probe_at = None
        self._settle_send_state()

    def _reflect_receipt(self, acked: int, retired: list[int], credit_frames: int, *,
                         canonical: bool) -> bool:
        """Hand an accepted canonical receipt to a host owner that mirrors it.

        A finite owner keeps its own byte credit and its own set of attempted but
        unreflected admissions, and the two have to move together: retiring
        receipt without rebasing the credit, or the reverse, is the split brain
        the port review reproduced. The owner is reached only through an
        optional link hook, so a plain simulated carrier is unaffected.

        Returns False when that owner refuses the snapshot as wider than the
        negotiated byte/window contract, so the caller can refuse the whole
        acknowledgement before either side has moved.
        """
        reflect = getattr(self.link, "reflect_acknowledged", None)
        if reflect is None:
            return True
        return bool(reflect(acked=acked, retired=tuple(retired),
                            credit_frames=credit_frames, canonical=canonical))

    def _settle_send_state(self) -> None:
        if self.send_state is not FlowState.OPEN or not self._started:
            return
        if not self._outbound and not self._in_flight and self._acked_frames >= self._total:
            self.send_state = FlowState.COMPLETE

    def _sample_rtt(self, sample: float) -> None:
        # Defence in depth behind the handover-time measurement above: an
        # impossible round trip is refused rather than folded into the estimator,
        # because a negative sample would drag the retry clock below the time a
        # frame takes to transmit and start a retransmission storm on a clean link.
        if not sample > 0:
            self.impossible_rtt_samples += 1
            return
        limits = self.limits
        if self._srtt is None:
            self._srtt = sample
            self._rttvar = sample / 2
        else:
            self._rttvar = 3 / 4 * self._rttvar + 1 / 4 * abs(self._srtt - sample)
            self._srtt = 7 / 8 * self._srtt + 1 / 8 * sample
        self._rto = min(limits.max_rto_s, max(limits.min_rto_s, self._srtt + 4 * self._rttvar))
