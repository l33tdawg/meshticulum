# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Flow-owned attempt contracts against an explicit, bounded synthetic owner.

This owner has exactly one flow/frame and no ACK, receive, stream or wire
engine. It is a local protocol witness, not an MT05 or authenticated adapter.
"""

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal, ROUND_CEILING
import hashlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meshticulum.contracts.carrier import (
    CarrierKind, CarrierProfile, ProfileBasis,
)
from meshticulum.contracts.errors import ContractError
from meshticulum.contracts.identity import (
    DeadlineSet, FlowKey, PeerID, RouteBinding, ServiceID, ServiceKind, SessionKey,
)
from meshticulum.contracts.negotiation import (
    CapabilityLimits, CommitOutcome, Feature, NegotiatedContract, TrafficClass,
)
from meshticulum.scheduler.budget import Cost, MAX_TIME_US
from meshticulum.scheduler.flow_attempt import (
    AttemptBinding, CommitRequest, MAX_FRAME_ID, MAX_SEQUENCE,
    Reservation, FlowAttemptPort, FlowDispatchLease, profile_cost,
)


def binding_fixture(**changes):
    local = PeerID("fixture-home", "a" * 64)
    remote = PeerID("fixture-peer", "b" * 64)
    session = SessionKey(local, remote, b"i" * 16, b"g" * 16)
    flow = FlowKey(session, local, 1)
    profile = CarrierProfile(CarrierKind.BINARY, 128, 13, 16, b"p" * 32,
                             2, 1, 8, .75, .25, .5, 0, 1000,
                             ProfileBasis.SOURCE_FIXTURE)
    route = RouteBinding(session, ServiceID(remote, ServiceKind.SAGE),
                         "12345678-1234-4234-8234-123456789abc",
                         b"c" * 32, profile.profile_digest, 1)
    deadlines = DeadlineSet(1000, 900, 2100)
    limits = CapabilityLimits(64, 1024, 1024, 128, 16, 4096, 3,
                              8, 2, 65535, 32, 32)
    contract = NegotiatedContract(1, frozenset(Feature), limits, route,
                                  deadlines, TrafficClass.INTERACTIVE, 1, b"n" * 16)
    values = dict(flow=flow, route=route, contract=contract, frame_id=31,
                  payload=b"fixture", payload_digest=hashlib.sha256(b"fixture").digest(),
                  sequence=6, attempt_ordinal=1, retry_not_before_us=100_000_000,
                  original_deadlines=deadlines, profile=profile,
                  cost=Cost(750_000, 1_500_000))
    values.update(changes)
    return AttemptBinding(**values)


def refused(code, action):
    with pytest.raises(ContractError) as error:
        action()
    assert error.value.code == code


class FakeOwner(FlowAttemptPort):
    """Test-only one-frame owner; no remote input or actual send operation."""

    def __init__(self, binding):
        self.binding = binding
        self.flow = binding.flow
        self.original_deadlines = binding.original_deadlines
        self._owner_id = b"o" * 16
        self.revision = self.token = 1
        self.pending = None
        self.active = None
        self.credit = 64
        self.sequence_consumed = False
        self.commits = self.attempts = 0
        self.last_us = 0
        self.closed = False

    @property
    def owner_id(self):
        return self._owner_id

    def progress(self):
        return (self.credit, self.sequence_consumed, self.attempts,
                None if self.binding is None else self.binding.sequence,
                None if self.binding is None else self.binding.attempt_ordinal,
                None if self.binding is None else self.binding.retry_not_before_us)

    def snapshot(self):
        return (self.progress(), self.revision, self.token, self.pending,
                self.last_us, self.closed, self.commits)

    def _clock(self, at_us):
        if type(at_us) is not int or not self.last_us <= at_us <= MAX_TIME_US:
            raise ContractError("fake_owner_clock_refused")
        self.last_us = at_us

    def _known(self, reservation):
        if (type(reservation) is not Reservation or reservation is not self.pending
                or reservation.owner_id != self.owner_id):
            raise ContractError("fake_owner_stale_handle")

    def _sendable(self, reservation):
        self._known(reservation)
        if (self.closed
                or reservation.owner_revision != self.revision
                or reservation.binding is not self.binding):
            raise ContractError("fake_owner_stale_handle")

    def prepare(self, flow, *, now_us, now_epoch):
        if type(flow) is not FlowKey or flow != self.flow:
            raise ContractError("fake_owner_wrong_flow")
        if self.active is not None:
            raise ContractError("fake_owner_lease_held")
        if self.pending is not None:
            raise ContractError("fake_owner_live_capacity")
        if self.closed:
            return None
        binding = self.binding
        try:
            binding.check(now_us=now_us, now_epoch=now_epoch)
        except ContractError as error:
            if error.code == "attempt_not_eligible":
                return None
            raise
        self._clock(now_us)
        handle = Reservation(self.owner_id, self.token.to_bytes(16, "big"),
                             self.revision, binding, now_us)
        self.token += 1
        self.pending = handle
        return handle

    def dispatch_lease(self, reservation):
        return FakeLease(self, reservation)

    def invalidate(self, *, replacement=None, restart=False):
        if self.active is not None:
            raise ContractError("fake_owner_lease_held")
        if replacement is not None:
            if (type(replacement) is not AttemptBinding
                    or replacement.flow != self.flow
                    or replacement.original_deadlines != self.original_deadlines
                    or any(getattr(replacement.contract.deadlines, key) >
                           getattr(self.binding.contract.deadlines, key)
                           for key in DeadlineSet.__dataclass_fields__)):
                raise ContractError("fake_owner_invalid_replacement")
        self.revision += 1
        # A known pending handle is retired for sending, retained only for
        # exactly-one refusal reconciliation under an owner lease.
        self.binding = replacement
        self.closed = restart or replacement is None


class FakeLease(FlowDispatchLease):
    def __init__(self, owner, reservation):
        self.owner = owner
        self.reservation = reservation
        self.active = False
        self.committed = False

    def __enter__(self):
        self.owner._known(self.reservation)
        if self.owner.active is not None:
            raise ContractError("fake_owner_lease_held")
        self.owner.active = self
        self.active = True
        return self

    def _held(self):
        if not self.active or self.owner.active is not self:
            raise ContractError("fake_owner_lease_missing")
        if self.committed:
            raise ContractError("fake_owner_already_committed")

    def validate(self, reservation, *, now_us, now_epoch):
        self._held()
        self.owner._sendable(reservation)
        if reservation is not self.reservation:
            raise ContractError("fake_owner_stale_handle")
        self.owner.binding.check(now_us=now_us, now_epoch=now_epoch)
        self.owner._clock(now_us)
        return self.owner.binding

    def commit(self, request):
        self._held()
        if type(request) is not CommitRequest or request.reservation is not self.reservation:
            raise ContractError("fake_owner_invalid_commit")
        self.owner._known(request.reservation)
        sendable = (not self.owner.closed
                    and request.reservation.owner_revision == self.owner.revision
                    and request.reservation.binding is self.owner.binding)
        attempted = request.outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN)
        if attempted and not sendable:
            raise ContractError("fake_owner_invalid_commit")
        self.owner._clock(request.at_us)
        binding = request.reservation.binding
        tightened = binding.contract.deadlines.tightened(request.effective_deadlines)
        contract = replace(binding.contract, deadlines=tightened)
        if attempted:
            if not self.owner.sequence_consumed:
                self.owner.credit -= binding.payload_bytes
                self.owner.sequence_consumed = True
            self.owner.attempts += 1
            # This literal fake has no RTT estimator; the owner chooses the
            # currently bound profile's initial RTO, not a scheduler input.
            retry_us = int((Decimal(str(binding.profile.initial_rto_seconds))
                            * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
            if binding.attempt_ordinal == contract.limits.max_retry_attempts:
                self.owner.binding = None
                self.owner.closed = True
            else:
                self.owner.binding = replace(binding, contract=contract,
                    attempt_ordinal=binding.attempt_ordinal + 1,
                    retry_not_before_us=request.at_us + retry_us)
        elif self.owner.binding is not None:
            current = self.owner.binding
            current_deadlines = current.contract.deadlines.tightened(tightened)
            if current_deadlines != current.contract.deadlines:
                self.owner.binding = replace(current,
                    contract=replace(current.contract, deadlines=current_deadlines))
        self.owner.commits += 1
        self.owner.pending = None
        self.committed = True

    def __exit__(self, kind, value, traceback):
        self.active = False
        self.owner.active = None
        if not self.committed:
            # No attempted send exists in this owner. Refuse silent lease
            # abandonment; the host must commit a pre-send refusal in finally.
            raise ContractError("fake_owner_uncommitted_exit")
        return False


@pytest.fixture
def binding():
    return binding_fixture()


@pytest.fixture
def owner(binding):
    return FakeOwner(binding)


def commit(owner, handle, outcome, at_us, deadlines=None):
    with owner.dispatch_lease(handle) as lease:
        lease.validate(handle, now_us=at_us, now_epoch=2000)
        lease.commit(CommitRequest(handle, outcome, at_us,
                                  deadlines or handle.binding.contract.deadlines))


def test_binding_checks_and_atomic_reserve_do_not_consume_credit_or_eligibility(owner):
    before = owner.snapshot()
    for _ in range(100):
        assert owner.binding.check(now_us=100_000_000, now_epoch=2000)
    assert owner.snapshot() == before
    assert not hasattr(owner, "peek") and not hasattr(FlowAttemptPort, "peek")
    progress = owner.progress()
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    assert owner.progress() == progress
    assert owner.pending is handle
    refused("fake_owner_live_capacity", lambda: owner.prepare(owner.flow,
        now_us=100_000_000, now_epoch=2000))
    assert owner.progress() == progress
    commit(owner, handle, CommitOutcome.REFUSED_SCHEDULER, 100_000_000)


def test_port_has_atomic_selection_and_readonly_owner_identity(owner):
    before = owner.snapshot()
    assert owner.prepare(owner.flow, now_us=99_999_999, now_epoch=2000) is None
    assert owner.snapshot() == before
    refused("fake_owner_wrong_flow", lambda: owner.prepare(replace(owner.flow, flow_id=2),
        now_us=100_000_000, now_epoch=2000))
    refused("fake_owner_wrong_flow", lambda: owner.prepare(owner.binding,
        now_us=100_000_000, now_epoch=2000))
    assert owner.snapshot() == before
    with pytest.raises(AttributeError):
        owner.owner_id = b"x" * 16
    with pytest.raises(TypeError):
        FlowAttemptPort()
    with pytest.raises(TypeError):
        FlowDispatchLease()


@pytest.mark.parametrize("outcome", [CommitOutcome.REFUSED_SCHEDULER, CommitOutcome.REFUSED_CARRIER])
def test_pre_send_refusals_preserve_exact_identity_and_eligibility(owner, outcome):
    binding = owner.binding
    progress = owner.progress()
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    commit(owner, handle, outcome, 100_000_000)
    assert owner.progress() == progress
    assert owner.binding is binding
    assert owner.attempts == 0 and owner.pending is None and owner.commits == 1
    resubmitted = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    assert resubmitted.reservation_id != handle.reservation_id
    assert resubmitted.binding is binding
    commit(owner, resubmitted, outcome, 100_000_000)


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_attempt_commit_charges_credit_once_and_owner_advances_retry_clock(owner, outcome):
    initial = owner.binding
    first = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    commit(owner, first, outcome, 100_000_000)
    retry = owner.binding
    assert owner.credit == 57 and owner.sequence_consumed and owner.attempts == 1
    assert retry.frame_id == 31 and retry.sequence == 6 and retry.payload == b"fixture"
    assert retry.payload_digest == initial.payload_digest
    assert retry.attempt_ordinal == 2 and retry.retry_not_before_us == 102_000_000
    assert retry.original_deadlines is initial.original_deadlines
    before = owner.snapshot()
    assert owner.prepare(owner.flow, now_us=101_999_999, now_epoch=2000) is None
    assert owner.snapshot() == before
    second = owner.prepare(owner.flow, now_us=102_000_000, now_epoch=2000)
    commit(owner, second, outcome, 102_000_000)
    assert owner.credit == 57 and owner.attempts == 2
    assert owner.binding.attempt_ordinal == 3
    assert owner.binding.retry_not_before_us == 104_000_000


def test_attempt_ordinal_exhaustion_closes_fake_owner_without_wrapping(owner):
    for at_us in (100_000_000, 102_000_000, 104_000_000):
        handle = owner.prepare(owner.flow, now_us=at_us, now_epoch=2000)
        commit(owner, handle, CommitOutcome.UNKNOWN, at_us)
    assert owner.attempts == 3 and owner.credit == 57
    assert owner.prepare(owner.flow, now_us=106_000_000, now_epoch=2000) is None and owner.closed
    assert owner.pending is None


def test_exact_once_requires_live_object_and_held_current_owner_lease(owner):
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    before = owner.snapshot()
    for forged in (replace(handle), replace(handle, owner_id=b"x" * 16),
                   replace(handle, owner_revision=2), replace(handle, reservation_id=b"x" * 16)):
        refused("fake_owner_stale_handle", lambda: owner.dispatch_lease(forged).__enter__())
        assert owner.snapshot() == before
    lease = owner.dispatch_lease(handle)
    request = CommitRequest(handle, CommitOutcome.ACCEPTED, 100_000_000,
                            handle.binding.contract.deadlines)
    refused("fake_owner_lease_missing", lambda: lease.commit(request))
    with lease as held:
        assert held.validate(handle, now_us=100_000_000, now_epoch=2000) is handle.binding
        refused("fake_owner_lease_held", owner.invalidate)
        held.commit(request)
        after = owner.snapshot()
        refused("fake_owner_already_committed", lambda: held.commit(request))
        assert owner.snapshot() == after
    refused("fake_owner_lease_missing", lambda: lease.commit(request))
    refused("fake_owner_stale_handle", lambda: owner.dispatch_lease(handle).__enter__())
    assert owner.commits == owner.attempts == 1


@pytest.mark.parametrize("change", ["cancel", "route", "profile", "restart"])
def test_admitted_lifecycle_changes_invalidate_outstanding_handles(owner, change):
    old = owner.binding
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    if change in ("cancel", "restart"):
        owner.invalidate(restart=change == "restart")
    elif change == "route":
        route = replace(old.route, route_generation=2)
        owner.invalidate(replacement=replace(old, route=route,
            contract=replace(old.contract, route=route)))
    else:
        profile = replace(old.profile, profile_digest=b"q" * 32, forward_seconds=.5)
        route = replace(old.route, profile_digest=profile.profile_digest, route_generation=2)
        owner.invalidate(replacement=replace(old, route=route, profile=profile,
            cost=Cost(500_000, 1_250_000), contract=replace(old.contract, route=route)))
    assert owner.pending is handle
    with owner.dispatch_lease(handle) as lease:
        before = owner.snapshot()
        refused("fake_owner_stale_handle", lambda: lease.validate(handle,
            now_us=100_000_000, now_epoch=2000))
        assert owner.snapshot() == before
        refused("fake_owner_invalid_commit", lambda: lease.commit(CommitRequest(handle,
            CommitOutcome.ACCEPTED, 100_000_000, old.contract.deadlines)))
        lease.commit(CommitRequest(handle, CommitOutcome.REFUSED_SCHEDULER,
                                  100_000_000, old.contract.deadlines))
    assert owner.credit == 64 and owner.attempts == 0 and owner.pending is None
    if change in ("route", "profile"):
        assert owner.binding.original_deadlines is old.original_deadlines
        new = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
        assert new.owner_revision == 2 and new.reservation_id != handle.reservation_id
        commit(owner, new, CommitOutcome.REFUSED_SCHEDULER, 100_000_000)


def test_peer_restart_cannot_replace_the_session_even_with_reused_short_flow_id(owner):
    old = owner.binding
    session = replace(old.flow.session, session_generation=b"r" * 16)
    flow = replace(old.flow, session=session)
    route = replace(old.route, session=session)
    rebound = replace(old, flow=flow, route=route,
                      contract=replace(old.contract, route=route))
    refused("fake_owner_invalid_replacement", lambda: owner.invalidate(replacement=rebound))
    assert owner.binding is old and owner.revision == 1


def test_tightening_preserves_original_three_bounds_and_refuses_extension(owner):
    old = owner.binding
    tighter = DeadlineSet(800, 700, 2050)
    owner.invalidate(replacement=replace(old, contract=replace(old.contract, deadlines=tighter)))
    assert owner.binding.original_deadlines is old.original_deadlines
    assert owner.binding.contract.deadlines == tighter
    refused("fake_owner_invalid_replacement", lambda: owner.invalidate(replacement=old))
    refused("attempt_original_deadline_extended", lambda: replace(old,
        contract=replace(old.contract, deadlines=DeadlineSet(1001, 900, 2100))))
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    refused("attempt_effective_deadline_extended", lambda: CommitRequest(handle,
        CommitOutcome.ACCEPTED, 100_000_000, old.original_deadlines))
    commit(owner, handle, CommitOutcome.REFUSED_SCHEDULER, 100_000_000, tighter)


def test_final_pre_send_deadline_profile_and_eligibility_checks_are_exclusive(binding):
    assert binding.check(now_us=100_000_000, now_epoch=2000)
    refused("attempt_not_eligible", lambda: binding.check(now_us=99_999_999, now_epoch=2000))
    refused("authority_deadline_expired", lambda: binding.check(now_us=900_000_000, now_epoch=2000))
    refused("authority_expired", lambda: binding.check(now_us=100_000_000, now_epoch=2100))
    short = replace(binding, profile=replace(binding.profile, max_age_seconds=100))
    refused("carrier_profile_expired", lambda: short.check(now_us=100_000_000, now_epoch=2000))
    refused("attempt_effective_deadline_extended", lambda: binding.check(now_us=100_000_000,
        now_epoch=2000, effective_deadlines=DeadlineSet(1000, 901, 2100)))


def test_post_send_expiry_cannot_prevent_exact_once_attempt_accounting(owner):
    old = owner.binding
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    with owner.dispatch_lease(handle) as lease:
        lease.validate(handle, now_us=100_000_000, now_epoch=2000)
        # A hypothetical invoked carrier returns after the authority deadline.
        lease.commit(CommitRequest(handle, CommitOutcome.UNKNOWN, 900_000_000,
                                   old.contract.deadlines))
    assert owner.credit == 57 and owner.attempts == owner.commits == 1
    assert owner.binding.original_deadlines is old.original_deadlines
    assert owner.binding.retry_not_before_us == 902_000_000
    refused("authority_deadline_expired", lambda: owner.prepare(owner.flow,
        now_us=902_000_000, now_epoch=2000))


def test_expired_known_pending_handle_reconciles_refusal_without_resurrection(owner):
    old = owner.binding
    progress = owner.progress()
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    with owner.dispatch_lease(handle) as lease:
        refused("authority_deadline_expired", lambda: lease.validate(handle,
            now_us=900_000_000, now_epoch=2000))
        lease.commit(CommitRequest(handle, CommitOutcome.REFUSED_SCHEDULER,
                                   900_000_000, old.contract.deadlines))
    assert owner.progress() == progress and owner.pending is None
    assert owner.commits == 1 and owner.attempts == 0
    refused("authority_deadline_expired", lambda: owner.prepare(owner.flow,
        now_us=900_000_000, now_epoch=2000))
    refused("fake_owner_stale_handle", lambda: owner.dispatch_lease(handle).__enter__())


def test_lease_exit_never_suppresses_post_send_failure_or_rolls_back_commit(owner):
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    with pytest.raises(RuntimeError, match="synthetic post-send failure"):
        with owner.dispatch_lease(handle) as lease:
            lease.commit(CommitRequest(handle, CommitOutcome.UNKNOWN, 100_000_000,
                                       handle.binding.contract.deadlines))
            raise RuntimeError("synthetic post-send failure")
    assert owner.attempts == owner.commits == 1 and owner.pending is None


def test_complete_profile_and_cost_are_bound_even_if_digest_is_reused(binding):
    assert profile_cost(binding.profile) == Cost(750_000, 1_500_000)
    changed = replace(binding.profile, forward_seconds=.5)
    refused("attempt_profile_cost_mismatch", lambda: replace(binding, profile=changed))
    new = replace(binding, profile=changed, cost=Cost(500_000, 1_250_000))
    owner = FakeOwner(binding)
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    forged = replace(handle, binding=new)
    refused("fake_owner_stale_handle", lambda: owner.dispatch_lease(forged).__enter__())
    assert owner.credit == 64 and owner.pending is handle
    commit(owner, handle, CommitOutcome.REFUSED_SCHEDULER, 100_000_000)


def test_microsecond_cost_rounds_each_component_up_without_shared_cost_omission(binding):
    profile = replace(binding.profile, forward_seconds=.0000001,
                      firmware_ack_seconds=.0000001, reverse_control_seconds=.0000001)
    assert profile_cost(profile) == Cost(1, 3)
    refused("attempt_profile_cost_mismatch", lambda: replace(binding, profile=profile, cost=Cost(1, 1)))


@pytest.mark.parametrize("sequence", [0, 65535])
def test_uint16_sequence_edges_are_literal_and_never_masked(binding, sequence):
    value = replace(binding, sequence=sequence)
    assert value.sequence == sequence
    owner = FakeOwner(value)
    handle = owner.prepare(owner.flow, now_us=100_000_000, now_epoch=2000)
    commit(owner, handle, CommitOutcome.ACCEPTED, 100_000_000)
    assert owner.binding.sequence == sequence


@pytest.mark.parametrize("field,code,values", [
    ("frame_id", "invalid_attempt_frame_id", [0, -1, True, 1.0, "1", MAX_FRAME_ID + 1, 10 ** 10000]),
    ("sequence", "invalid_attempt_sequence", [-1, True, 1.0, "1", MAX_SEQUENCE + 1, float("nan"), float("inf")]),
    ("attempt_ordinal", "invalid_attempt_ordinal", [0, -1, True, 1.0, "1", 4, 10 ** 10000]),
    ("retry_not_before_us", "invalid_attempt_clock", [-1, True, 1.0, "1", MAX_TIME_US + 1, float("nan"), float("inf")]),
])
def test_integer_fields_reject_aliases_nonfinite_and_overflow(binding, field, code, values):
    for value in values:
        refused(code, lambda: replace(binding, **{field: value}))


@pytest.mark.parametrize("payload", [b"", bytearray(b"fixture"), memoryview(b"fixture"), "fixture", b"x" * 65])
def test_payload_has_exact_type_and_negotiated_capacity(binding, payload):
    refused("invalid_attempt_payload", lambda: replace(binding, payload=payload))


def test_payload_digest_matches_exact_bytes_and_namespace_is_full(binding):
    refused("attempt_payload_digest_mismatch", lambda: replace(binding, payload=b"changed"))
    refused("invalid_attempt_payload_digest", lambda: replace(binding, payload_digest=bytearray(32)))
    refused("invalid_attempt_binding", lambda: replace(binding,
        flow=replace(binding.flow, session=replace(binding.flow.session, session_id=b"x" * 16))))
    refused("invalid_attempt_binding", lambda: replace(binding,
        route=replace(binding.route, carrier_binding_digest=b"x" * 32)))
    refused("invalid_attempt_binding", lambda: replace(binding,
        profile=replace(binding.profile, profile_digest=b"x" * 32)))
    with pytest.raises(FrozenInstanceError):
        binding.payload = b"changed"
    assert b"fixture".decode() not in repr(binding)


@pytest.mark.parametrize("field", ["flow", "route", "contract", "original_deadlines", "profile", "cost"])
def test_nested_type_aliases_cannot_be_substituted(binding, field):
    value = getattr(binding, field)
    alias_type = type("Alias", (type(value),), {})
    alias = alias_type(**{key: getattr(value, key) for key in value.__dataclass_fields__})
    refused("invalid_attempt_binding", lambda: replace(binding, **{field: alias}))


@pytest.mark.parametrize("field,bad,code", [
    ("owner_id", bytes(16), "invalid_attempt_owner_id"),
    ("owner_id", bytearray(b"o" * 16), "invalid_attempt_owner_id"),
    ("reservation_id", bytes(16), "invalid_attempt_reservation_id"),
    ("reservation_id", b"r" * 15, "invalid_attempt_reservation_id"),
    ("owner_revision", True, "invalid_attempt_owner_revision"),
    ("owner_revision", 0, "invalid_attempt_owner_revision"),
    ("owner_revision", 1 << 64, "invalid_attempt_owner_revision"),
    ("reserved_at_us", True, "invalid_attempt_clock"),
    ("reserved_at_us", 99_999_999, "attempt_reserved_before_eligibility"),
    ("binding", {}, "invalid_attempt_reservation"),
])
def test_reservation_record_is_strict_but_not_live_ownership_proof(binding, field, bad, code):
    handle = Reservation(b"o" * 16, b"r" * 16, 1, binding, 100_000_000)
    refused(code, lambda: replace(handle, **{field: bad}))
    with pytest.raises(FrozenInstanceError):
        handle.owner_revision = 2


@pytest.mark.parametrize("field,bad,code", [
    ("outcome", "attempted_unknown", "invalid_attempt_commit"),
    ("outcome", None, "invalid_attempt_commit"),
    ("reservation", {}, "invalid_attempt_commit"),
    ("at_us", True, "invalid_attempt_clock"),
    ("at_us", 1.0, "invalid_attempt_clock"),
    ("at_us", float("nan"), "invalid_attempt_clock"),
    ("at_us", MAX_TIME_US + 1, "invalid_attempt_clock"),
    ("at_us", 99_999_999, "attempt_commit_clock_regressed"),
    ("effective_deadlines", {}, "attempt_effective_deadline_extended"),
    ("effective_deadlines", DeadlineSet(1001, 900, 2100), "attempt_effective_deadline_extended"),
    ("effective_deadlines", DeadlineSet(1000, 901, 2100), "attempt_effective_deadline_extended"),
    ("effective_deadlines", DeadlineSet(1000, 900, 2101), "attempt_effective_deadline_extended"),
])
def test_commit_request_cannot_widen_deadlines_or_forge_outcome_types(binding, field, bad, code):
    handle = Reservation(b"o" * 16, b"r" * 16, 1, binding, 100_000_000)
    request = CommitRequest(handle, CommitOutcome.UNKNOWN, 100_000_000, binding.contract.deadlines)
    refused(code, lambda: replace(request, **{field: bad}))
    assert not hasattr(request, "next_retry_not_before_us")
