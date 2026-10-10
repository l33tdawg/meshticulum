# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Inert flow-owned attempt values and a trusted-host port protocol.

These records bind a complete frame, its original deadlines and the current
admitted route/profile/contract. They do not allocate a sequence, authenticate
a peer, authorize a service, maintain a retry engine, or prove carrier drain.
The flow owner supplies opaque live reservations and holds its current-flow
dispatch lease through one commit. Python typing and record equality do not
establish that ownership or lease provenance.

The scheduler must preserve a refused frame's flow identity and eligibility.
On ACCEPTED or UNKNOWN the owner commits one attempt, consumes any previously
unsent credit once, and derives retry timing from its own current state. A
carrier result never proves application delivery. Post-send expiry must not
prevent accounting; subsequent sends still obey all original deadlines.
"""

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import hashlib

from ..contracts.carrier import CarrierProfile
from ..contracts.errors import ContractError, integer, octets
from ..contracts.identity import DeadlineSet, FlowKey, RouteBinding
from ..contracts.negotiation import CommitOutcome, NegotiatedContract
from .budget import Cost, MAX_TIME_US


MAX_FRAME_ID = (1 << 64) - 1
MAX_SEQUENCE = (1 << 16) - 1
MAX_OWNER_REVISION = (1 << 64) - 1


def _nonce(value, code):
    octets(value, 16, code)
    if not any(value):
        raise ContractError(code)


def _time(value):
    return integer(value, 0, MAX_TIME_US, "invalid_attempt_clock")


def _no_extension(candidate, bound, code):
    if type(candidate) is not DeadlineSet or type(bound) is not DeadlineSet:
        raise ContractError(code)
    if any(getattr(candidate, field) > getattr(bound, field)
           for field in DeadlineSet.__dataclass_fields__):
        raise ContractError(code)


def profile_cost(profile):
    """Ceil each forward/firmware-ACK/reverse estimate, in microseconds."""
    if type(profile) is not CarrierProfile:
        raise ContractError("invalid_attempt_profile")
    values = [int((Decimal(str(value)) * 1_000_000).to_integral_value(
        rounding=ROUND_CEILING)) for value in
        (profile.forward_seconds, profile.firmware_ack_seconds,
         profile.reverse_control_seconds)]
    return Cost(values[0], sum(values))


@dataclass(frozen=True, slots=True, repr=False)
class AttemptBinding:
    flow: FlowKey
    route: RouteBinding
    contract: NegotiatedContract
    frame_id: int
    payload: bytes
    payload_digest: bytes
    sequence: int
    attempt_ordinal: int
    retry_not_before_us: int
    original_deadlines: DeadlineSet
    profile: CarrierProfile
    cost: Cost

    def __post_init__(self):
        if (type(self.flow) is not FlowKey or type(self.route) is not RouteBinding
                or type(self.contract) is not NegotiatedContract
                or type(self.original_deadlines) is not DeadlineSet
                or type(self.profile) is not CarrierProfile or type(self.cost) is not Cost
                or self.flow.session != self.route.session
                or self.contract.route != self.route
                or self.profile.profile_digest != self.route.profile_digest):
            raise ContractError("invalid_attempt_binding")
        integer(self.frame_id, 1, MAX_FRAME_ID, "invalid_attempt_frame_id")
        integer(self.sequence, 0, MAX_SEQUENCE, "invalid_attempt_sequence")
        integer(self.attempt_ordinal, 1, self.contract.limits.max_retry_attempts,
                "invalid_attempt_ordinal")
        _time(self.retry_not_before_us)
        if (type(self.payload) is not bytes
                or not 1 <= len(self.payload) <= min(self.contract.limits.max_payload_bytes,
                                                     self.profile.payload_capacity)):
            raise ContractError("invalid_attempt_payload")
        octets(self.payload_digest, 32, "invalid_attempt_payload_digest")
        if hashlib.sha256(self.payload).digest() != self.payload_digest:
            raise ContractError("attempt_payload_digest_mismatch")
        if self.cost != profile_cost(self.profile):
            raise ContractError("attempt_profile_cost_mismatch")
        _no_extension(self.contract.deadlines, self.original_deadlines,
                      "attempt_original_deadline_extended")

    @property
    def payload_bytes(self):
        return len(self.payload)

    def check(self, *, now_us, now_epoch, effective_deadlines=None):
        """Pure eligibility check; supplied clocks and admission remain external."""
        _time(now_us)
        self.original_deadlines.check(now_us / 1_000_000, now_epoch)
        self.contract.deadlines.check(now_us / 1_000_000, now_epoch)
        self.profile.check_fresh(now_us / 1_000_000)
        if effective_deadlines is not None:
            _no_extension(effective_deadlines, self.contract.deadlines,
                          "attempt_effective_deadline_extended")
            effective_deadlines.check(now_us / 1_000_000, now_epoch)
        if now_us < self.retry_not_before_us:
            raise ContractError("attempt_not_eligible")
        return True


@dataclass(frozen=True, slots=True, repr=False)
class Reservation:
    """Host-owned value; only its owner can establish exact live object identity."""
    owner_id: bytes
    reservation_id: bytes
    owner_revision: int
    binding: AttemptBinding
    reserved_at_us: int

    def __post_init__(self):
        _nonce(self.owner_id, "invalid_attempt_owner_id")
        _nonce(self.reservation_id, "invalid_attempt_reservation_id")
        integer(self.owner_revision, 1, MAX_OWNER_REVISION, "invalid_attempt_owner_revision")
        if type(self.binding) is not AttemptBinding:
            raise ContractError("invalid_attempt_reservation")
        _time(self.reserved_at_us)
        if self.reserved_at_us < self.binding.retry_not_before_us:
            raise ContractError("attempt_reserved_before_eligibility")


@dataclass(frozen=True, slots=True, repr=False)
class CommitRequest:
    """Exactly-one host reconciliation request, without scheduler retry authority.

    A valid post-send request may arrive after expiry. Its tightened deadlines
    remain a future-send fence; constructing this record never authorizes a
    send or establishes a live handle. The owner derives subsequent timing.
    """
    reservation: Reservation
    outcome: CommitOutcome
    at_us: int
    effective_deadlines: DeadlineSet

    def __post_init__(self):
        if type(self.reservation) is not Reservation or type(self.outcome) is not CommitOutcome:
            raise ContractError("invalid_attempt_commit")
        _time(self.at_us)
        if self.at_us < self.reservation.reserved_at_us:
            raise ContractError("attempt_commit_clock_regressed")
        binding = self.reservation.binding
        _no_extension(self.effective_deadlines, binding.contract.deadlines,
                      "attempt_effective_deadline_extended")
        _no_extension(self.effective_deadlines, binding.original_deadlines,
                      "attempt_original_deadline_extended")


class FlowDispatchLease(AbstractContextManager, ABC):
    """Trusted host lease active across final validation, send and one commit.

    validate must check exact live sendable reservation identity and current owner,
    session/route/profile revision, cancellation and original deadlines.
    commit must reconcile one accepted/unknown attempt even when post-send
    deadlines expire. Refusal preserves credit, sequence and retry timing.
    Known pending handles may enter a reconciliation lease after cancellation,
    expiry or admitted route/profile change. validate then denies a send while
    a refusal commit finalizes that unsent handle once. Such entry never
    restores send eligibility. Copied, foreign and finalized handles refuse.
    commit returns exactly None; exit returns only False or None and cannot
    suppress failures or roll back an attempted commit. An unavailable lease
    or failed commit requires the host to fence, never repeat uncertainty.
    """

    @abstractmethod
    def __enter__(self) -> "FlowDispatchLease": ...

    @abstractmethod
    def __exit__(self, kind, value, traceback) -> bool | None: ...

    @abstractmethod
    def validate(self, reservation: Reservation, *, now_us: int,
                 now_epoch: int | float) -> AttemptBinding: ...

    @abstractmethod
    def commit(self, request: CommitRequest) -> None: ...


class FlowAttemptPort(ABC):
    """One host-owned handle per flow; eligible selection/reservation is atomic.

    prepare must select the current eligible attempt and refuse a second live
    handle. dispatch_lease must refuse forged, wrong-owner or finalized handles;
    cancellation, admitted route/profile changes and peer restart invalidate
    outstanding handles for sending while retaining known pending handles for
    exactly-one reconciliation. Such changes never widen original deadlines or
    silently resurrect an old session. Host lifecycle writes serialize with
    a held read/dispatch lease through exactly-one commit.
    """

    @property
    @abstractmethod
    def owner_id(self) -> bytes: ...

    @abstractmethod
    def prepare(self, flow: FlowKey, *, now_us: int,
                now_epoch: int | float) -> Reservation | None: ...

    @abstractmethod
    def dispatch_lease(self, reservation: Reservation) -> FlowDispatchLease: ...
