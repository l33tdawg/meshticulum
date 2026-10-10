"""The finite MT05 owner and link that bind the flow candidate to the frozen MT04 port.

This is the actual adapter the decision authorizes: one flow's logical send
accounting, one live queued handle at a time, every emission lane routed through
the frozen scheduler under its normal service and broker leases.

What it is not: authentication, a worker, physical completion, or a claim that a
carrier ACCEPTED result means the peer received anything. `Reservation` and
`CommitRequest` are inert records; only a live handle under a held lease is
evidence of ownership, and Python type identity is not a security boundary.

Nothing here is wired into `sage_lantern` and nothing opens a radio, socket,
broker or serial port.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib

from ..meshticulum_contracts.carrier import CarrierProfile
from ..meshticulum_contracts.errors import ContractError
from ..meshticulum_contracts.identity import FlowKey
from ..meshticulum_contracts.negotiation import CommitOutcome, NegotiatedContract
from ..meshticulum_scheduler.budget import MAX_TIME_US
from ..meshticulum_scheduler.flow_attempt import (AttemptBinding, CommitRequest, FlowAttemptPort,
                                                  FlowDispatchLease, Reservation, profile_cost)
from ..meshticulum_scheduler.scheduler import FrameRole
from .carrier import Delivery
from .flow import (ACK_BYTES, DEFERRED, KIND_ACK, admitted_payload_bound, data_frame_fits,
                   frames_from_byte_grant, parse_frame)

DATA_KIND = "data"
CONTROL_KIND = "ack"

#: The MT05 frame encoding the admitted bound has to leave room for: the DATA
#: header plus the trailing CRC. Named here because the bound is about the
#: complete serialized frame, not about its payload.
DATA_OVERHEAD_BYTES = 12


@dataclass(frozen=True)
class LanePolicy:
    """Which of a flow's own emission kinds is a control lane.

    The frozen owner `prepare` and `AttemptBinding` carry no role field, so the
    adapter fixes the mapping once, at construction, from the flow's own send
    kind. Selection and the scheduler enqueue therefore cannot disagree, and no
    unlocked peek or remote flag can decide a role.
    """

    control_kinds: frozenset[str] = frozenset({CONTROL_KIND})

    def role_for(self, kind: str) -> FrameRole:
        if type(kind) is not str:
            raise ContractError("invalid_flow_port_lane")
        return FrameRole.CONTROL if kind in self.control_kinds else FrameRole.DATA


def admitted_frame_bound(*, contract: NegotiatedContract, profile: CarrierProfile,
                         unaccounted_overhead_bytes: int = 0) -> int:
    """The largest complete serialized MT05 frame this admission accepts.

    `CarrierProfile.payload_capacity` already subtracts the profile's own named
    header and authentication overhead, so that overhead is deducted once, by the
    profile, and never again here. Only overhead the profile does not know about
    is passed in - and it is passed in once.
    """
    if type(contract) is not NegotiatedContract or type(profile) is not CarrierProfile:
        raise ContractError("invalid_flow_port_admission")
    return admitted_payload_bound(
        contract_max_payload_bytes=contract.limits.max_payload_bytes,
        profile_payload_capacity=profile.payload_capacity,
        unaccounted_overhead_bytes=unaccounted_overhead_bytes)


def frame_grant_from_bytes(byte_grant: int, payload_capacity: int,
                           max_reassembly_frames: int) -> int:
    """Free frame slots a negotiated byte grant buys, conservatively.

    The quantum is the admitted maximum payload, so a grant below one quantum
    buys nothing and is never clamped up to a single frame.
    """
    return min(frames_from_byte_grant(byte_grant, payload_capacity), max_reassembly_frames)


@dataclass
class _Candidate:
    """One emission the flow asked for, with the logical identity it settles under."""

    frame_id: int
    sequence: int
    payload: bytes
    payload_digest: bytes
    role: FrameRole
    attempt_ordinal: int
    retry_not_before_us: int
    logical: bool
    kind: str
    reflected: bool = False


@dataclass(frozen=True)
class _Held:
    """The one frame this link currently holds queued under a live owner handle."""

    frame_id: int
    payload: bytes
    role: FrameRole
    sequence: int | None
    sender: str
    #: Whether the flow was told `DEFERRED` and is therefore still waiting for
    #: this handover. A frame the flow was told was sent has already consumed its
    #: own byte, sequence and retry clock, and reporting it again would spend
    #: them twice.
    deferred: bool


class MeshticulumFlowOwner(FlowAttemptPort):
    """One flow's logical admissions, retry identity and live handle.

    The flow decides *what* to emit and hands the exact frame bytes over; this
    owner decides whether that emission may take a logical slot, mints the
    attempt binding, and settles it exactly once.

    Selection and lane are one decision. `offer` queues the exact bytes, derives
    the lane from the flow's own send kind and *arms* that one candidate; a later
    `prepare` may only materialize the armed candidate. The frozen scheduler
    cannot name a role on the owner's behalf, so rather than let the role be
    recomputed from whatever happens to be at the head of the queue, the owner
    refuses to prepare anything it did not arm.
    """

    def __init__(self, *, owner_id: bytes, flow: FlowKey, contract: NegotiatedContract,
                 profile: CarrierProfile, lanes: LanePolicy | None = None,
                 no_send: bool = False):
        if type(owner_id) is not bytes or len(owner_id) != 16 or not any(owner_id):
            raise ContractError("invalid_flow_port_owner_id")
        if type(flow) is not FlowKey or type(contract) is not NegotiatedContract:
            raise ContractError("invalid_flow_port_registration")
        if (type(profile) is not CarrierProfile or contract.route.session != flow.session
                or profile.profile_digest != contract.route.profile_digest):
            raise ContractError("invalid_flow_port_registration")
        self._owner_id = owner_id
        self.flow = flow
        self.contract = contract
        self.profile = profile
        self.route = contract.route
        self.cost = profile_cost(profile)
        self.original_deadlines = contract.deadlines
        self.lanes = lanes or LanePolicy()
        # The fixed logical DATA quantum P: the byte-credit mapping divides by
        # this, and the admitted bound has to leave room for its frame encoding.
        self.payload_capacity = contract.limits.max_payload_bytes
        self.bound = admitted_frame_bound(contract=contract, profile=profile)
        self.no_send = bool(no_send)

        self._revision = 1
        self._token = 1
        self._next_frame_id = 1
        self._queue: deque[_Candidate] = deque()
        self._armed: _Candidate | None = None
        # A refused or credit-blocked emission is held, not unbounded: the queue
        # is capped by the negotiated byte budget and by the frame count that
        # budget buys at the fixed quantum.
        self._max_queued_frames = max(1, contract.limits.max_queued_bytes // self.payload_capacity)
        self._live: Reservation | None = None
        self._live_candidate: _Candidate | None = None
        self._settled_handles: set[int] = set()
        self._unsendable: set[int] = set()
        self._lease_open = False
        self._admitted: dict[int, _Candidate] = {}
        self._unreflected: set[int] = set()
        self._attempts: dict[int, int] = {}
        self._grant = 0
        self._grant_established = False
        self.rf_attempts = 0
        self.reconciled_unsent = 0
        # Every lane that may hold a frame queued for this flow. A host may
        # dispatch through the scheduler without going through any link - and
        # the scheduler may select a different flow's frame entirely - so the
        # exact outcome has to travel back to the transaction it belongs to
        # from settlement rather than from whichever caller happened to ask.
        self._links: list[FlowPortLink] = []

    def attach_link(self, link: FlowPortLink) -> None:
        """Register a lane that reports this owner's handovers and holds."""
        if link not in self._links:
            self._links.append(link)

    def _route_settlement(self, candidate: _Candidate, outcome: CommitOutcome, *,
                          handed_over: bool) -> None:
        """Tell the originating link the exact outcome of its own frame.

        Only the frame this owner just settled is reported, and only by its
        identity, so a link holding anything else settles nothing. A settlement
        with no originating candidate - a refusal decided before preparation -
        describes no link transaction and routes nowhere. `handed_over` is the
        owner's own fact that this frame is no longer queued for it, which is
        what lets a link tell a real emission from a confirmed-unsent refusal.
        """
        for link in self._links:
            link.settle_held(candidate, outcome, handed_over=handed_over)

    @property
    def owner_id(self) -> bytes:
        return self._owner_id

    @property
    def revision(self) -> int:
        return self._revision

    # -- host-facing accounting -------------------------------------------------

    def grant_ceiling_bytes(self) -> int:
        """The widest byte snapshot the negotiated contract can support.

        `window_bytes` and `max_unconsumed_bytes` are the two ceilings the peer
        actually enforces; the smaller one binds. A snapshot wider than either is
        refused rather than clamped, because rounding it down would silently
        widen a bound the negotiation did not grant.
        """
        return min(self.contract.limits.window_bytes,
                   self.contract.limits.max_unconsumed_bytes)

    def accept_grant(self, byte_grant: int, *, reflected: tuple[int, ...] = (),
                     reflected_sequences: tuple[int, ...] = ()) -> None:
        """Install a newly accepted byte-credit snapshot.

        The grant is a snapshot, not an addition: unreflected logical admissions
        are subtracted from it rather than added to a running total. A shrunken or
        zero grant invalidates a prepared-but-unsent handle *for sending* while
        keeping it for exactly-one reconciliation, so attempted uncertainty debt
        and the original frame/retry identity survive. A later wider snapshot that
        the live handle does fit restores it, so an identical repeat is idempotent
        rather than destructive.

        `reflected_sequences` is the canonical receipt that arrived with this
        snapshot: the two move together, in one call, so a receipt can never
        retire frames without rebasing the credit or the reverse.
        """
        if type(byte_grant) is not int or isinstance(byte_grant, bool) or byte_grant < 0:
            raise ContractError("invalid_flow_port_grant")
        if byte_grant > self.grant_ceiling_bytes():
            raise ContractError("flow_port_grant_exceeds_window")
        self.reflect_receipt(reflected_sequences)
        self._grant = frame_grant_from_bytes(byte_grant, self.payload_capacity,
                                             self.contract.limits.max_reassembly_frames)
        self._grant_established = True
        for frame_id in reflected:
            self._unreflected.discard(frame_id)
        if self._live is not None and self._live_candidate is not None:
            if self._live_fits():
                self._unsendable.discard(id(self._live))
            else:
                self._unsendable.add(id(self._live))

    def reflect_receipt(self, sequences: tuple[int, ...] = ()) -> None:
        """Retire attempted admissions a canonical receipt has confirmed.

        The admission itself is kept: it is still the identity a retransmission
        or a credit probe repeats under, and re-minting it would let one frame
        take two logical slots.
        """
        if type(sequences) is not tuple:
            raise ContractError("invalid_flow_port_receipt")
        for sequence in sequences:
            if type(sequence) is not int or isinstance(sequence, bool):
                raise ContractError("invalid_flow_port_receipt")
            candidate = self._admitted.get(sequence)
            if candidate is not None:
                self._unreflected.discard(candidate.frame_id)

    def _has_slot(self) -> bool:
        """Whether one more logical admission fits the current snapshot."""
        if not self._grant_established:
            return False
        pending_new = 1 if (self._live_candidate is not None
                            and self._live_candidate.logical) else 0
        return len(self._unreflected) + pending_new < self._grant

    def _live_fits(self) -> bool:
        """Whether the *existing* prepared reservation still fits the snapshot.

        Finding room for a *new* reservation and judging whether an existing one
        still fits are different questions: at exactly one free slot the new
        question is false and the existing one is true. Treating them as one
        question is what cancelled a live, perfectly sendable handle.
        """
        if not self._grant_established:
            return False
        if self._live_candidate is None or not self._live_candidate.logical:
            return True
        return len(self._unreflected) + 1 <= self._grant

    def settle_sending(self) -> None:
        """Host switch: while set, no lane emits. DATA, retry, ACK, persist and probe all stop."""
        self.no_send = True

    def resume_sending(self) -> None:
        self.no_send = False

    # -- flow-facing ------------------------------------------------------------

    def offer(self, frame: bytes, *, kind: str) -> FrameRole:
        """Queue one emission the flow asked for and report the lane it took.

        No logical slot is consumed here, and a frame that does not fit the
        admitted bound is refused rather than truncated. The candidate this call
        describes is the one armed for the next `prepare`, so the lane the host
        enqueues under and the bytes it enqueues are decided together.
        """
        if type(frame) is not bytes or not frame:
            raise ContractError("invalid_flow_port_frame")
        role = self.lanes.role_for(kind)
        if len(frame) > self.bound:
            raise ContractError("flow_port_frame_exceeds_admission")
        if role is FrameRole.CONTROL and len(frame) > self.contract.limits.max_control_bytes:
            raise ContractError("flow_port_control_exceeds_admission")
        parsed = parse_frame(frame)
        if parsed is None:
            raise ContractError("flow_port_unparsable_frame")
        candidate = self._candidate_for(parsed, frame, role, kind)
        self._armed = candidate
        return role

    def _candidate_for(self, parsed: dict, frame: bytes, role: FrameRole,
                       kind: str) -> _Candidate:
        """The queued candidate this emission *is*, reusing an identical pending one.

        A flow that re-offers a frame the host has not dispatched yet is repeating
        itself, not asking for a second frame. Minting a fresh identity for it is
        how a thousand refusals became a thousand queued frames outside every
        quota; the existing candidate is returned instead.
        """
        digest = hashlib.sha256(frame).digest()
        if kind == CONTROL_KIND:
            # An acknowledgement is control: it retires receipt, it never takes a
            # data slot, and it is never relabelled DATA.
            held = self._queued_matching(kind, -1, digest)
            if held is not None:
                return held
            return self._enqueue_candidate(_Candidate(
                self._mint_frame_id(), -1, frame, digest, role, 1, 0, False, kind))
        # Any identical pending emission is the *same* emission, whether it was
        # first offered as a fresh admission or as a retransmission: repeating it
        # must not append a second copy or mint a second identity.
        held = self._queued_matching(kind, parsed["seq"], digest)
        if held is not None:
            return held
        sequence = parsed["seq"]
        known = self._admitted.get(sequence)
        if known is None:
            candidate = self._enqueue_candidate(_Candidate(
                self._mint_frame_id(), sequence, frame, digest, role, 1, 0, True, kind))
            self._admitted[sequence] = candidate
            return candidate
        # A retransmission or a credit probe reuses the logical admission it
        # repeats, so it can never take a second slot.
        return self._enqueue_candidate(_Candidate(
            known.frame_id, sequence, frame, digest, role,
            self._attempts.get(known.frame_id, 1) + 1, 0, False, kind))

    def _queued_matching(self, kind: str, sequence: int, digest: bytes) -> _Candidate | None:
        for candidate in self._queue:
            if (candidate.kind == kind and candidate.sequence == sequence
                    and candidate.payload_digest == digest):
                return candidate
        return None

    def _mint_frame_id(self) -> int:
        frame_id = self._next_frame_id
        self._next_frame_id += 1
        return frame_id

    def _enqueue_candidate(self, candidate: _Candidate) -> _Candidate:
        queued_bytes = sum(len(entry.payload) for entry in self._queue)
        if (len(self._queue) >= self._max_queued_frames
                or queued_bytes + len(candidate.payload)
                > self.contract.limits.max_queued_bytes):
            raise ContractError("flow_port_queue_limit")
        self._queue.append(candidate)
        return candidate

    def _discard_queued(self, candidate: _Candidate) -> None:
        for index, entry in enumerate(self._queue):
            if entry is candidate:
                del self._queue[index]
                return

    # -- FlowAttemptPort --------------------------------------------------------

    def prepare(self, flow, *, now_us, now_epoch):
        if type(flow) is not FlowKey or flow != self.flow:
            raise ContractError("flow_port_wrong_flow")
        if type(now_us) is not int or not 0 <= now_us <= MAX_TIME_US:
            raise ContractError("invalid_flow_port_clock")
        if self._live is not None:
            raise ContractError("flow_port_live_capacity")
        if self.no_send:
            return None
        candidate = self._armed
        if candidate is None:
            # Selection and lane are one decision: with nothing armed there is
            # nothing this owner is willing to describe, so nothing is handed
            # over rather than whatever happens to be at the head of the queue.
            raise ContractError("flow_port_unarmed_prepare")
        if candidate.logical and not self._has_slot():
            # Hold the emission rather than let it take a slot the peer has not
            # granted. It keeps its identity, its lane and its place.
            return None
        self._armed = None
        binding = AttemptBinding(
            flow=self.flow, route=self.route, contract=self.contract,
            frame_id=candidate.frame_id, payload=candidate.payload,
            payload_digest=candidate.payload_digest, sequence=max(candidate.sequence, 0),
            attempt_ordinal=min(max(candidate.attempt_ordinal, 1),
                                self.contract.limits.max_retry_attempts),
            retry_not_before_us=candidate.retry_not_before_us,
            original_deadlines=self.original_deadlines, profile=self.profile, cost=self.cost)
        # Pure eligibility only: no credit, sequence or attempt is consumed, so a
        # refusal here leaves the flow exactly where it was.
        try:
            binding.check(now_us=now_us, now_epoch=now_epoch)
        except ContractError as error:
            if error.code in ("attempt_not_eligible", "authority_deadline_expired",
                              "authority_expired", "carrier_profile_expired"):
                self._armed = candidate
                return None
            raise
        reservation = Reservation(self.owner_id, self._token.to_bytes(16, "big"),
                                  self._revision, binding, now_us)
        self._token += 1
        self._live = reservation
        self._live_candidate = candidate
        # The reservation owns the candidate now; it is no longer merely queued.
        self._discard_queued(candidate)
        return reservation

    def dispatch_lease(self, reservation):
        # Refuse a forged or foreign handle before any lease object exists.
        self._owned(reservation)
        owner = self

        class Lease(FlowDispatchLease):
            def __init__(self):
                self._committed = False

            def __enter__(self):
                if owner._lease_open:
                    raise ContractError("flow_port_lease_held")
                owner._lease_open = True
                return self

            def __exit__(self, kind, value, traceback):
                owner._lease_open = False
                return False

            def _held(self):
                if not owner._lease_open:
                    raise ContractError("flow_port_lease_missing")
                if self._committed:
                    raise ContractError("flow_port_already_committed")
                owner._owned(reservation)

            def validate(self, handle, *, now_us, now_epoch):
                self._held()
                owner._owned(handle)
                if owner.no_send:
                    # The host lifecycle is re-read here, after every policy lease
                    # and before the carrier is ever invoked, so a stop that
                    # arrives inside the lease costs zero invocations. Entering
                    # this lease never restores send eligibility.
                    raise ContractError("flow_port_no_send")
                if id(handle) in owner._unsendable:
                    # Still a known handle, so it may be reconciled - but it is no
                    # longer valid for sending.
                    raise ContractError("flow_port_handle_not_sendable")
                binding = handle.binding
                binding.check(now_us=now_us, now_epoch=now_epoch)
                return binding

            def commit(self, request):
                self._held()
                if type(request) is not CommitRequest or request.reservation is not reservation:
                    raise ContractError("invalid_attempt_commit")
                owner._settle(request)
                self._committed = True
                return None

        return Lease()

    def _owned(self, reservation):
        if type(reservation) is not Reservation or reservation is not self._live:
            raise ContractError("flow_port_handle_refused")
        if reservation.owner_id != self.owner_id or reservation.owner_revision != self._revision:
            raise ContractError("flow_port_handle_refused")
        return reservation

    # -- settlement -------------------------------------------------------------

    def _settle(self, request: CommitRequest) -> None:
        reservation = request.reservation
        if id(reservation) in self._settled_handles:
            raise ContractError("flow_port_duplicate_completion")
        if reservation is not self._live:
            raise ContractError("flow_port_handle_refused")
        candidate = self._live_candidate
        attempted = request.outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN)
        # Only an accepted carrier disposition is a frame the carrier took. A
        # refusal - whether the scheduler decided it before the invocation or the
        # carrier queue refused inside it - is reported as what it is, and the
        # flow keeps its own byte and sequence to re-offer.
        handed_over = attempted
        if attempted:
            # Once the carrier has been invoked, the attempt is recorded exactly
            # once even though eligibility may have been lost while it was in
            # flight. That loss stays a fence on *future* sends; it is never a
            # reason to refuse accounting for an attempt that really happened,
            # which is what turned a genuine invocation into a fenced flow with
            # zero recorded attempts.
            if candidate is not None and candidate.logical and not candidate.reflected:
                candidate.reflected = True
                self._unreflected.add(candidate.frame_id)
            if candidate is not None:
                self._attempts[candidate.frame_id] = candidate.attempt_ordinal
            self.rf_attempts += 1
        elif candidate is not None and candidate.logical and not candidate.reflected:
            # Known unsent refusal: the prepared slot is released, no credit,
            # sequence or retry attempt is taken, and the flow re-offers.
            self._admitted.pop(candidate.sequence, None)
        self._settled_handles.add(id(reservation))
        self._unsendable.discard(id(reservation))
        self._live = None
        self._live_candidate = None
        self._revision += 1
        if not attempted:
            self.reconciled_unsent += 1
        if candidate is not None:
            # The one point where an exact owner outcome exists. Whether the
            # carrier was invoked or the emission is known to be unsent, the
            # frame's originating link has to hear it here.
            self._route_settlement(candidate, request.outcome, handed_over=handed_over)

    def _sendable(self, reservation) -> bool:
        return (reservation is self._live and reservation.owner_revision == self._revision
                and id(reservation) not in self._unsendable and not self.no_send)

    # -- diagnostics ------------------------------------------------------------

    def snapshot(self) -> dict:
        """Aggregate counters only: no payload, identity or peer content."""
        return {"queued": len(self._queue), "live_handles": int(self._live is not None),
                "logical_admissions": len(self._admitted),
                "unreflected": len(self._unreflected), "frame_grant": self._grant,
                "grant_established": self._grant_established, "rf_attempts": self.rf_attempts,
                "reconciled_unsent": self.reconciled_unsent, "revision": self._revision,
                "no_send": self.no_send, "armed": int(self._armed is not None),
                "queued_bytes": sum(len(entry.payload) for entry in self._queue)}


class _LinkView:
    """The `LinkProfile`-shaped view `ReliableFlow` reads for its airtime estimate."""

    def __init__(self, profile: CarrierProfile):
        self.capacity_bytes_per_second = (
            profile.mtu_bytes / profile.forward_seconds if profile.forward_seconds > 0 else 0.0)


class FlowPortLink:
    """The `SimLink` face a `ReliableFlow` drives, with every lane through the port.

    There is deliberately no second send path: `send` is the only way bytes leave,
    and it goes through `enqueue_from_flow` and `dispatch_one` on the frozen
    scheduler. `pump(max_frames=0)` on the flow is *not* the no-send fence; the
    owner's `settle_sending()` is, because it covers the acknowledgement, expiry
    and probe paths too.

    One frame is held per flow. `send` returns `DEFERRED` when the frame is queued
    under a live handle but not yet handed to the carrier, which is what lets the
    flow keep its byte, sequence and retry clock for the real handover instead of
    spending them at host enqueue. Every dispatch is matched to the frame this
    link queued by scheduler frame identity, so another flow's dispatch is never
    logged, reported or settled as this one's.
    """

    def __init__(self, *, scheduler, owner: MeshticulumFlowOwner, flow: FlowKey,
                 authorize, broker, carrier, monotonic_us, epoch_clock,
                 incarnation_id: int = 1):
        self.scheduler = scheduler
        self.owner = owner
        self.flow = flow
        self.authorize = authorize
        self.broker = broker
        self.carrier = carrier
        self.monotonic_us = monotonic_us
        self.epoch_clock = epoch_clock
        self.incarnation_id = incarnation_id
        self.now = 0.0
        self._inbound: list[Delivery] = []
        self._queued_bytes = 0
        self._view = _LinkView(owner.profile)
        self.sent: list[bytes] = []
        self.refused = 0
        self.foreign_dispatches = 0
        self.last_result = None
        self.handed_over = 0
        self._in_send = False
        self._held: _Held | None = None
        self._drivers: dict[str, object] = {}
        # A host may enqueue now and dispatch later; the frozen scheduler keeps
        # those two operations separate for exactly that reason. True here means
        # "the frame is queued under a live handle", not "it was sent".
        self.defer_to_host = False
        # The owner settles every one of this flow's attempts and may do so from
        # a host dispatch this link never made, so it is told where to report.
        owner.attach_link(self)

    # -- carrier face -----------------------------------------------------------

    def incarnation(self, endpoint: str) -> int:
        return self.incarnation_id

    def profile(self, sender: str):
        return self._view

    def queued_bytes(self, sender: str) -> int:
        return self._queued_bytes

    def inbound(self, endpoint: str) -> list[Delivery]:
        pending, self._inbound = self._inbound, []
        return pending

    def deliver(self, payload: bytes, *, kind: str = "data") -> None:
        """Inject one frame from the peer, as the synthetic carrier would."""
        self._inbound.append(Delivery(payload=payload, corrupted=False, kind=kind,
                                      sent_at=self.now, delivered_at=self.now))

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def attach_flow(self, flow) -> None:
        """Register the flow this link drives, so a deferred handover is reported.

        `ReliableFlow` calls this if the link offers it; the simulated carrier
        has no such notion, so the hook is optional and its absence is the
        ordinary synchronous case.
        """
        self._drivers[flow.endpoint] = flow

    # -- the one send path ------------------------------------------------------

    def send(self, sender: str, payload: bytes, *, kind: str = DATA_KIND):
        if self.owner.no_send:
            self.refused += 1
            return False
        held = self._held
        if held is not None:
            if held.sender == sender and held.payload == payload:
                # The very same emission, still queued: repeating it is not a
                # second frame and must not mint a second identity.
                return DEFERRED
            self.refused += 1
            return False
        try:
            role = self.owner.offer(payload, kind=kind)
            frame_id = self.scheduler.enqueue_from_flow(self.flow, now_us=self.monotonic_us(),
                                                        now_epoch=self.epoch_clock(), role=role)
        except ContractError:
            self.refused += 1
            return False
        if frame_id is None:
            self.refused += 1
            return False
        parsed = parse_frame(payload)
        sequence = None if kind == CONTROL_KIND or parsed is None else parsed["seq"]
        # Only a DATA frame is ever deferred. A control frame's canonical
        # snapshot is committed by the sender the instant it is on the wire, and
        # a queued-but-unsent acknowledgement has no truthful way to report that
        # later, so the host switch stays a DATA re-read rather than a lie.
        deferred = bool(self.defer_to_host and role is FrameRole.DATA)
        self._held = _Held(frame_id, payload, role, sequence, sender, deferred)
        queued = self._held
        self._queued_bytes += len(payload)
        if deferred:
            return DEFERRED
        # A synchronous send consumes the frame's byte, sequence and attempt
        # itself the moment the carrier takes it, so the handover must not be
        # reported while this call is still inside the flow: the flow would be
        # told about the emission it is in the middle of spending and would
        # consume the next frame's bytes under this one's sequence number.
        self._in_send = True
        try:
            handed_over = self.dispatch_queued()
        finally:
            self._in_send = False
        if handed_over:
            return True
        if self._held is queued:
            # The scheduler gave this dispatch to a different flow, so this
            # frame is still queued under this link's own live handle. That is
            # the state the host switch already reports, not a refusal: the flow
            # keeps the byte, sequence and attempt it has not spent, and settles
            # them on the real handover. Reporting a refusal here let the flow
            # re-offer an emission that was already waiting, so the queued probe
            # or retransmission left no record and its eventual dispatch settled
            # nothing at all.
            return DEFERRED
        # The scheduler settled this frame without handing it over, so this is
        # the terminal unsent refusal and the flow re-offers it afresh.
        return False

    def dispatch_queued(self) -> bool:
        """Hand the one queued frame to the carrier under both fresh leases.

        Whatever the scheduler decides lands on this link through the exact
        frame identity the *owner* settles, so this call settles nothing itself:
        it asks for a dispatch and reports whether this link's own frame was the
        one the carrier took. It deliberately does not read the dispatched
        identity back out of the scheduler's result - that reports the flow's
        frame, not this link's, and the two differ as soon as the owner is
        holding an older blocked candidate.
        """
        held = self._held
        if held is None:
            return False
        before = self.handed_over
        result = self.scheduler.dispatch_one(
            authorize=self.authorize, broker=self.broker, send=self.carrier,
            monotonic_us=self.monotonic_us, epoch_clock=self.epoch_clock)
        self.last_result = result
        if self.handed_over > before:
            return True
        if self._held is held:
            # This link's own frame is untouched and still queued under its own
            # live handle. The scheduler gave the slot to a *different* flow, so
            # nothing here may be logged, reported as sent, or settled.
            self.foreign_dispatches += 1
        return False

    def settle_held(self, candidate, outcome: CommitOutcome, *, handed_over: bool) -> bool:
        """Apply the exact outcome of one dispatched frame to this link's hold.

        The scheduler may select this link's frame when some other caller asked,
        or select a different flow's frame entirely, so the outcome arrives from
        the owner that settled it rather than from the link that happens to be
        running. A repeated report of a frame this link has already released
        settles nothing a second time.

        The frame is identified by its exact bytes, not by a frame id: the owner
        mints candidate ids while the scheduler mints its own, and the two spaces
        collide, so an id match would settle one frame using another's identity.

        `handed_over` is the owner's ruling on whether the carrier took the
        frame. The hold is released either way, because the handle it stood for
        has been settled and the frame is no longer queued for it - a refused
        emission the flow still owns is re-offered, and that re-offer is what
        queues it afresh. What differs is only whether this was a real emission,
        logged as sent and reported to the flow, or a refusal reported as one.
        """
        held = self._held
        if held is None or held.payload != candidate.payload:
            return False
        self._held = None
        self._queued_bytes = max(0, self._queued_bytes - len(held.payload))
        if handed_over:
            self.handed_over += 1
            # The exact bytes this frame queued, never the newest payload the
            # flow happened to offer in the meantime.
            self.sent.append(held.payload)
            self._handover(held)
            return True
        self.refused += 1
        # A terminal outcome that is definitely not a handover, so the flow is
        # told the emission it was waiting on will never leave. Without this the
        # queued retry record outlives the frame it describes.
        self._handover_cancelled(held)
        return False

    def _handover_cancelled(self, held: _Held) -> None:
        """Retract the flow's record for an emission the carrier did not take.

        Two lanes can be holding a record for one emission, because the flow
        decides which one this handover belonged to from the marker it set when
        it queued the frame: a deferred retransmission, or a deferred credit
        probe. Both are keyed to the same exact bytes, so both are offered the
        retraction and each removes only its own record.
        """
        driver = self._drivers.get(held.sender)
        if driver is None or self._in_send:
            return
        if held.role is FrameRole.CONTROL:
            # A control frame is queued without a sequence number, because its
            # canonical snapshot is settled by the sender that carries it rather
            # than by any DATA accounting. Only a control emission the flow was
            # told `DEFERRED` is owed this report, and the flow decides that for
            # itself from the exact bytes it queued.
            retract = getattr(driver, "ack_handover_cancelled", None)
            if retract is not None:
                retract(held.payload)
            return
        if held.sequence is None:
            return
        driver.handover_cancelled(held.sequence, held.payload)
        retract_probe = getattr(driver, "probe_handover_cancelled", None)
        if retract_probe is not None:
            retract_probe(held.sequence, held.payload)

    def _handover(self, held: _Held) -> None:
        """Tell the flow its queued frame is finally on the wire.

        Whether the flow is still owed this handover is the flow's own question,
        not this link's: a frame the flow was told `DEFERRED` is waiting for it,
        and so is a frame the flow offered on a dispatch that went to another
        flow, because that flow kept its byte after being told `True`. A frame
        the flow already consumed for itself says so, and nothing is reported
        twice.
        """
        driver = self._drivers.get(held.sender)
        if driver is None or self._in_send:
            return
        if held.role is FrameRole.CONTROL:
            # A queued control frame that really leaves commits the canonical
            # snapshot its sender minted for those exact bytes. Without this the
            # acknowledgement is recorded by the owner and the link while the
            # flow that emitted it never settles the revision it spent, so the
            # next changed snapshot reuses that revision and the peer refuses it
            # as a conflicting grant.
            ack = getattr(driver, "ack_handover", None)
            if ack is not None:
                ack(held.payload, at=self.now)
            return
        if held.sequence is None:
            return
        driver.handover(held.sequence, at=self.now)

    # -- flow-facing accounting -------------------------------------------------

    def reflect_acknowledged(self, *, acked: int, retired: tuple[int, ...],
                             credit_frames: int, canonical: bool) -> bool:
        """Mirror one accepted canonical flow receipt into the owner's own state.

        The flow and the owner keep separate credit views, so a receipt that
        retires frames has to rebase the owner's byte snapshot in the same call,
        and a snapshot the negotiated contract cannot support refuses the whole
        acknowledgement before either side has moved.
        """
        if (type(credit_frames) is not int or isinstance(credit_frames, bool)
                or credit_frames < 0):
            return False
        if not canonical:
            self.owner.reflect_receipt(tuple(retired))
            return True
        try:
            self.owner.accept_grant(credit_frames * self.owner.payload_capacity,
                                    reflected_sequences=tuple(retired))
        except ContractError:
            return False
        return True


__all__ = ["CONTROL_KIND", "DATA_KIND", "DATA_OVERHEAD_BYTES", "FlowPortLink", "LanePolicy",
           "MeshticulumFlowOwner", "admitted_frame_bound", "frame_grant_from_bytes"]
