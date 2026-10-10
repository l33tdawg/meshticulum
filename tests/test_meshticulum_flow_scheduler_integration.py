# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Actual scheduler integration against an independently stateful fake owner.

The owner below mints opaque live objects and records credit/sequence/RF/retry
transitions. It does not call scheduler selection, accounting or retry helpers.
All authority, carrier evidence and owner state are synthetic; no real flow,
device, network, carrier drain or application delivery is proved here.
"""

from collections import Counter
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
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
from meshticulum.scheduler.budget import (
    BudgetPolicy, Cost, CounterSample, Ledger,
)
from meshticulum.scheduler.flow_attempt import (
    AttemptBinding, CommitRequest, FlowAttemptPort, FlowDispatchLease, Reservation,
)
from meshticulum.scheduler.scheduler import (
    DispatchGrant, FrameRole, LeaseScope, Scheduler, SchedulerPolicy,
)


METRICS = {}


@dataclass
class OwnerState:
    binding: AttemptBinding
    credit: int = 8
    next_sequence: int = 7
    rf_attempts: int = 0
    retry_deadline_us: int | None = None
    revision: int = 1
    live: Reservation | None = None
    committed_logical_frames: set = field(default_factory=set)
    logical_rf_counts: dict = field(default_factory=dict)
    lease_active: bool = False
    terminal_requests: list = field(default_factory=list)
    terminal_handles: set = field(default_factory=set)
    cancelled: bool = False


class FakeOwner(FlowAttemptPort):
    """A deliberately separate credit/attempt oracle and trusted fake port.

    A completion lease may yield for an exact owned cancelled/expired handle;
    validate denies sending while commit can reconcile an unsent/attempted
    candidate. A copied dataclass cannot acquire such a lease.
    """
    def __init__(self):
        self._owner_id = b"o" * 16
        self.next_token = 1
        self.states = {}
        self.events = []
        self.validate_calls = []
        self.commit_calls = []
        self.on_peek = self.on_prepare = self.on_lease_enter = lambda: None
        self.on_validate = self.on_commit = self.on_lease_exit = lambda: None
        self.commit_fault = None
        self.commit_return = None
        self.exit_return = None

    @property
    def owner_id(self):
        return self._owner_id

    def add(self, binding):
        assert binding.flow not in self.states
        self.states[binding.flow] = OwnerState(binding)

    def peek(self, flow):
        self.on_peek()
        state = self.states[flow]
        return None if state.cancelled else state.binding

    def prepare(self, flow, *, now_us, now_epoch):
        self.on_prepare()
        state = self.states[flow]
        binding = state.binding
        if state.live is not None:
            raise ContractError("fake_owner_already_reserved")
        if state.cancelled:
            raise ContractError("fake_owner_binding_refused")
        try:
            self._eligible(state, now_us, now_epoch)
        except ContractError as error:
            if error.code in ("fake_owner_retry_not_eligible", "fake_owner_attempt_ordinal_refused"):
                return None
            raise
        reservation = Reservation(self.owner_id, self.next_token.to_bytes(16, "big"),
                                  state.revision, binding, now_us)
        self.next_token += 1
        state.live = reservation
        self.events.append(("prepare", reservation))
        return reservation

    @staticmethod
    def _eligible(state, now_us, now_epoch):
        binding = state.binding
        if type(now_us) is not int or now_us < binding.retry_not_before_us:
            raise ContractError("fake_owner_retry_not_eligible")
        if state.cancelled:
            raise ContractError("fake_owner_cancelled")
        if binding.attempt_ordinal != state.logical_rf_counts.get(binding.frame_id, 0) + 1:
            raise ContractError("fake_owner_attempt_ordinal_refused")
        if (binding.frame_id in state.committed_logical_frames
                and (state.retry_deadline_us is None or now_us < state.retry_deadline_us)):
            raise ContractError("fake_owner_retry_not_eligible")
        # Independent comparisons, rather than using AttemptBinding.check as
        # the owner oracle. Synthetic observations are not physical evidence.
        for bounds in (binding.original_deadlines, binding.contract.deadlines):
            if (now_us >= bounds.application_deadline_monotonic * 1_000_000
                    or now_us >= bounds.authority_deadline_monotonic * 1_000_000
                    or now_epoch >= bounds.authority_expires_epoch):
                raise ContractError("fake_owner_expired")
        if (now_us < binding.profile.measured_at_monotonic * 1_000_000
                or now_us >= (binding.profile.measured_at_monotonic
                              + binding.profile.max_age_seconds) * 1_000_000):
            raise ContractError("fake_owner_stale_profile")

    def _owned(self, reservation):
        if type(reservation) is not Reservation:
            raise ContractError("fake_owner_handle_refused")
        state = self.states.get(reservation.binding.flow)
        if (state is None or state.live is not reservation
                or reservation.owner_id != self.owner_id
                or reservation.owner_revision != state.revision
                or reservation.binding is not state.binding):
            raise ContractError("fake_owner_handle_refused")
        return state

    def dispatch_lease(self, reservation):
        state = self._owned(reservation)
        owner = self
        class Lease(FlowDispatchLease):
            def __enter__(self):
                assert not state.lease_active
                state.lease_active = True
                try:
                    owner.on_lease_enter()
                except BaseException:
                    state.lease_active = False
                    raise
                return self

            def __exit__(self, *_):
                try:
                    owner.on_lease_exit()
                    return owner.exit_return
                finally:
                    state.lease_active = False

            def validate(self, handle, *, now_us, now_epoch):
                owner.on_validate()
                owner.validate_calls.append(handle)
                assert state.lease_active
                assert owner._owned(handle) is state
                owner._eligible(state, now_us, now_epoch)
                return state.binding

            def commit(self, request):
                owner.commit_calls.append(request)
                owner.on_commit()
                assert type(request) is CommitRequest
                assert state.lease_active
                assert owner._owned(request.reservation) is state
                assert id(request.reservation) not in state.terminal_handles
                if owner.commit_fault == "before":
                    raise RuntimeError("synthetic owner completion unavailable")
                attempted = request.outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN)
                if attempted:
                    frame = request.reservation.binding.frame_id
                    if frame not in state.committed_logical_frames:
                        state.credit -= len(state.binding.payload)
                        state.committed_logical_frames.add(frame)
                        state.next_sequence += 1
                    state.rf_attempts += 1
                    state.logical_rf_counts[frame] = state.logical_rf_counts.get(frame, 0) + 1
                    state.retry_deadline_us = request.at_us + 2_000_000
                state.terminal_handles.add(id(request.reservation))
                state.terminal_requests.append(request)
                state.live = None
                state.revision += 1
                owner.events.append(("attempted" if attempted else "returned", request.reservation))
                if owner.commit_fault == "after":
                    raise RuntimeError("synthetic owner acknowledgement lost")
                return owner.commit_return

        return Lease()

    def retry(self, flow, now_us):
        state = self.states[flow]
        assert state.live is None
        assert state.retry_deadline_us is not None and now_us >= state.retry_deadline_us
        state.binding = replace(state.binding, attempt_ordinal=state.logical_rf_counts[state.binding.frame_id] + 1,
                                retry_not_before_us=state.retry_deadline_us)

    def oracle(self, flow):
        state = self.states[flow]
        return {"credit": state.credit, "next_sequence": state.next_sequence,
                "rf_attempts": state.rf_attempts, "retry_deadline_us": state.retry_deadline_us,
                "live_handles": int(state.live is not None),
                "terminal_callbacks": len(state.terminal_requests)}


class IntegrationRig:
    """Reusable fake host fixture for independent transaction/carrier reviewers."""
    def __init__(self, *, policy=None, budget=None, sample=True):
        self.local = PeerID("fixture-home", "a" * 64)
        self.peer = PeerID("fixture-uplink", "b" * 64)
        self.other = PeerID("fixture-other", "c" * 64)
        self.now_us = self.latest_us = 100_000_000
        self.now_epoch = 2000
        self.owner = FakeOwner()
        self.ledger = Ledger(budget or BudgetPolicy(60_000_000, 30_000_000,
            60_000_000, 10_000_000, quantization_us=1, max_reservations=1),
            b"l" * 16, self.now_us)
        if sample:
            self.ledger.observe(CounterSample(self.now_us, 10_000_000, b"l" * 16), self.now_us)
        args = dict(local_peer=self.local, ledger=self.ledger, epoch=b"s" * 16,
                    broker_epoch=b"b" * 16)
        if policy is not None:
            args["policy"] = policy
        self.scheduler = Scheduler(**args)
        self.records = {}
        self.calls = []
        self.requests = []
        self.active = {LeaseScope.SERVICE: False, LeaseScope.BROKER: False}
        self.on_service = self.on_broker = self.on_send = lambda: None
        self.on_service_exit = self.on_broker_exit = lambda: None
        self.outcome = CommitOutcome.ACCEPTED

    def open(self, *, flow_id=1, peer=None, kind=ServiceKind.SAGE, limits=None,
             deadlines=None, profile=None, original_deadlines=None, payload=b"abc"):
        peer = peer or self.peer
        session = SessionKey(self.local, peer, b"i" * 16, b"g" * 16)
        flow = FlowKey(session, self.local, flow_id)
        profile = profile or CarrierProfile(CarrierKind.BINARY, 128, 13, 16,
            bytes([flow_id]) * 32, 2, 1, 8, .75, .25, .5, 90, 1000,
            ProfileBasis.SOURCE_FIXTURE)
        route = RouteBinding(session, ServiceID(peer, kind),
            "12345678-1234-4234-8234-123456789abc", b"c" * 32,
            profile.profile_digest, 1)
        limits = limits or CapabilityLimits(64, 1024, 1024, 128, 16,
                                            4096, 6, 8, 2, 65535, 32, 32)
        deadlines = deadlines or DeadlineSet(1000, 900, 2100)
        contract = NegotiatedContract(1, frozenset(Feature), limits, route,
            deadlines, TrafficClass.BULK if kind is ServiceKind.KNOWLEDGE else
            TrafficClass.INTERACTIVE, 1, b"n" * 16)
        binding = AttemptBinding(flow, route, contract, 100 + flow_id, payload,
            hashlib.sha256(payload).digest(), 7, 1, self.now_us,
            original_deadlines or deadlines, profile, Cost(750_000, 1_500_000))
        self.owner.add(binding)
        self.records[flow] = (contract, profile)
        self.scheduler.open_flow(flow, contract, profile, attempt_owner=self.owner,
                                now_us=self.now_us, now_epoch=self.now_epoch)
        return flow

    def enqueue(self, flow, role=FrameRole.DATA):
        return self.scheduler.enqueue_from_flow(flow, now_us=self.now_us,
                                                now_epoch=self.now_epoch, role=role)

    def lease(self, request, scope):
        if scope is LeaseScope.SERVICE:
            self.requests.append(request)
            self.on_service()
        else:
            assert self.active[LeaseScope.SERVICE]
            self.on_broker()
        @contextmanager
        def guard():
            self.active[scope] = True
            try:
                yield DispatchGrant(request, scope, request.deadlines, 1, request.broker_epoch)
            finally:
                try:
                    (self.on_service_exit if scope is LeaseScope.SERVICE else self.on_broker_exit)()
                finally:
                    self.active[scope] = False
        return guard()

    def service(self, request):
        return self.lease(request, LeaseScope.SERVICE)

    def broker(self, request):
        return self.lease(request, LeaseScope.BROKER)

    def send(self, frame):
        assert all(self.active.values())
        assert self.owner.states[frame.request.flow].lease_active
        self.calls.append(frame)
        self.on_send()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def clock(self):
        self.latest_us = max(self.latest_us, self.now_us)
        return self.now_us

    def dispatch(self, **changes):
        args = dict(authorize=self.service, broker=self.broker, send=self.send,
                    monotonic_us=self.clock, epoch_clock=lambda: self.now_epoch)
        args.update(changes)
        return self.scheduler.dispatch_one(**args)

    def budget(self):
        return self.ledger.snapshot(self.latest_us)


@pytest.fixture
def rig():
    return IntegrationRig()


def assert_unsent_owner(owner, flow, *, terminals=0, live=0):
    assert owner.oracle(flow) == {"credit": 8, "next_sequence": 7, "rf_attempts": 0,
        "retry_deadline_us": None, "live_handles": live, "terminal_callbacks": terminals}


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_real_scheduler_uses_exact_owner_handle_under_three_leases_and_commits_once(rig, outcome):
    flow = rig.open()
    queue_id = rig.enqueue(flow)
    handle = rig.owner.states[flow].live
    assert handle is not None
    assert queue_id != handle.binding.frame_id
    assert_unsent_owner(rig.owner, flow, live=1)
    rig.outcome = outcome
    result = rig.dispatch()
    assert result.outcome is outcome and result.invoked
    assert result.flow_committed and not result.flow_fenced
    assert len(rig.calls) == len(rig.owner.commit_calls) == 1
    assert rig.calls[0].request.flow_reservation is handle
    assert rig.owner.commit_calls[0].reservation is handle
    assert rig.owner.commit_calls[0].outcome is outcome
    assert rig.owner.oracle(flow) == {"credit": 5, "next_sequence": 8, "rf_attempts": 1,
        "retry_deadline_us": 102_000_000, "live_handles": 0, "terminal_callbacks": 1}
    assert rig.budget()["estimated_shared_us"] == 1_500_000
    assert rig.budget()["pending_reservations"] == 0
    assert rig.dispatch().outcome is None
    assert len(rig.calls) == len(rig.owner.commit_calls) == 1


def test_owned_queue_cannot_accept_raw_payload_or_second_live_handle(rig):
    flow = rig.open()
    rig.enqueue(flow)
    before = rig.scheduler.snapshot()
    with pytest.raises(ContractError):
        rig.scheduler.enqueue(flow, b"other", now_us=rig.now_us, now_epoch=rig.now_epoch)
    with pytest.raises(ContractError):
        rig.enqueue(flow)
    assert rig.scheduler.snapshot() == before
    assert_unsent_owner(rig.owner, flow, live=1)


@pytest.mark.parametrize("cap", ["global_frames", "global_bytes", "peer_bytes", "control_bytes"])
def test_atomic_queue_pressure_returns_only_refused_candidate_without_credit_use(cap):
    changes = {
        "global_frames": {"max_queued_frames": 1, "max_control_queued_frames": 1},
        "global_bytes": {"max_queued_bytes": 3, "max_peer_queued_bytes": 3, "max_control_queued_bytes": 3},
        "peer_bytes": {"max_peer_queued_bytes": 3},
        "control_bytes": {"max_control_queued_bytes": 3},
    }[cap]
    rig = IntegrationRig(policy=SchedulerPolicy(**changes))
    first = rig.open()
    second = rig.open(flow_id=2, peer=rig.other if cap == "global_bytes" else None)
    role = FrameRole.CONTROL if cap == "control_bytes" else FrameRole.DATA
    rig.enqueue(first, role)
    first_handle = rig.owner.states[first].live
    with pytest.raises(ContractError, match="^scheduler_queue_limit$"):
        rig.enqueue(second, role)
    assert rig.scheduler.snapshot()["queued_frames"] == 1
    assert rig.scheduler.snapshot()["queued_bytes"] == 3
    assert rig.owner.states[first].live is first_handle
    assert_unsent_owner(rig.owner, first, live=1)
    assert_unsent_owner(rig.owner, second, terminals=1)
    assert len(rig.owner.commit_calls) == 1
    assert rig.owner.commit_calls[0].reservation.binding.flow == second
    assert rig.calls == [] and rig.budget()["estimated_shared_us"] == 0


@pytest.mark.parametrize("allocation", ["before", "after"])
def test_prepare_exception_fences_without_second_allocation_or_credit_use(rig, allocation):
    flow = rig.open()
    prepare = rig.owner.prepare
    calls = []
    def failed_prepare(*args, **kwargs):
        calls.append(True)
        if allocation == "after":
            prepare(*args, **kwargs)
        raise RuntimeError("synthetic allocation acknowledgement lost")
    rig.owner.prepare = failed_prepare
    with pytest.raises(RuntimeError):
        rig.enqueue(flow)
    assert rig.scheduler.snapshot()["fenced_flows"] == 1
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    with pytest.raises(ContractError, match="^scheduler_flow_fenced$"):
        rig.enqueue(flow)
    assert calls == [True]
    assert rig.owner.states[flow].credit == 8 and rig.owner.states[flow].rf_attempts == 0
    assert rig.calls == [] and rig.budget()["estimated_shared_us"] == 0


@pytest.mark.parametrize("mismatch", ["route", "profile"])
def test_stale_owner_binding_cannot_be_queued_under_admitted_original_context(rig, mismatch):
    flow = rig.open()
    old = rig.owner.states[flow].binding
    if mismatch == "route":
        route = replace(old.route, route_generation=2)
        binding = replace(old, route=route, contract=replace(old.contract, route=route))
    else:
        # Opaque reference remains unchanged; actual local field equality is
        # still required. This profile change leaves literal cost unchanged.
        binding = replace(old, profile=replace(old.profile, initial_rto_seconds=3))
    rig.owner.states[flow].binding = binding
    with pytest.raises(ContractError, match="^flow_attempt_binding_refused$"):
        rig.enqueue(flow)
    assert_unsent_owner(rig.owner, flow, terminals=1)
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    assert rig.calls == []


def test_open_registration_owner_id_callback_cannot_reenter_scheduler(rig):
    seen = []
    class ReentrantOwner(FakeOwner):
        @property
        def owner_id(self):
            seen.append(True)
            with pytest.raises(ContractError, match="^scheduler_reentry$"):
                rig.dispatch()
            return self._owner_id
    rig.owner = ReentrantOwner()
    flow = rig.open()
    assert seen == [True]
    assert rig.scheduler.snapshot()["flows"] == 1
    assert_unsent_owner(rig.owner, flow)


def test_budget_refusal_returns_exact_candidate_once_without_rf_state_or_hidden_retry():
    rig = IntegrationRig(sample=False)
    flow = rig.open()
    rig.enqueue(flow)
    handle = rig.owner.states[flow].live
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert result.reason == "dispatch_budget_refused"
    assert result.flow_committed and not result.flow_fenced
    assert rig.owner.commit_calls[0].reservation is handle
    assert rig.owner.commit_calls[0].outcome is CommitOutcome.REFUSED_SCHEDULER
    assert_unsent_owner(rig.owner, flow, terminals=1)
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    assert rig.budget()["estimated_shared_us"] == 0
    assert rig.budget()["pending_reservations"] == 0
    assert rig.dispatch().outcome is None
    assert rig.calls == [] and len(rig.owner.commit_calls) == 1


@pytest.mark.parametrize("scope", list(LeaseScope))
def test_current_authority_denial_returns_flow_candidate_without_using_credit_or_retry(rig, scope):
    flow = rig.open()
    rig.enqueue(flow)
    @contextmanager
    def denied(_):
        yield None
    result = rig.dispatch(**{"authorize" if scope is LeaseScope.SERVICE else "broker": denied})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert result.flow_committed and not result.flow_fenced
    assert_unsent_owner(rig.owner, flow, terminals=1)
    assert rig.calls == [] and rig.budget()["estimated_shared_us"] == 0


@pytest.mark.parametrize("scope", list(LeaseScope))
def test_equal_copied_reservation_in_a_grant_is_not_exact_live_handle(rig, scope):
    flow = rig.open()
    rig.enqueue(flow)
    original = rig.owner.states[flow].live
    copied = replace(original)
    assert copied == original and copied is not original
    @contextmanager
    def copied_grant(request):
        yield DispatchGrant(replace(request, flow_reservation=copied), scope,
                            request.deadlines, 1, request.broker_epoch)
    result = rig.dispatch(**{"authorize" if scope is LeaseScope.SERVICE else "broker": copied_grant})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert result.flow_committed
    assert rig.owner.commit_calls[0].reservation is original
    assert_unsent_owner(rig.owner, flow, terminals=1)


def test_reused_host_queue_ordinal_cannot_select_equal_old_owner_handle():
    old = IntegrationRig()
    old_flow = old.open()
    old_ordinal = old.enqueue(old_flow)
    retired_handle = old.owner.states[old_flow].live
    old.scheduler.close()
    new = IntegrationRig()
    new.scheduler = Scheduler(local_peer=new.local, ledger=new.ledger,
        epoch=b"t" * 16, broker_epoch=b"b" * 16)
    new_flow = new.open()
    new_ordinal = new.enqueue(new_flow)
    current_handle = new.owner.states[new_flow].live
    assert old_ordinal == new_ordinal
    assert retired_handle == current_handle and retired_handle is not current_handle
    @contextmanager
    def wrong_handle(request):
        yield DispatchGrant(replace(request, flow_reservation=retired_handle),
                            LeaseScope.SERVICE, request.deadlines, 1, request.broker_epoch)
    result = new.dispatch(authorize=wrong_handle)
    assert not result.invoked and result.flow_committed
    assert new.owner.commit_calls[0].reservation is current_handle
    assert len(old.owner.commit_calls) == len(new.owner.commit_calls) == 1
    assert_unsent_owner(new.owner, new_flow, terminals=1)


@pytest.mark.parametrize("changed", ["owner_id", "revision"])
def test_stale_owner_generation_is_fenced_before_send_without_fabricated_completion(rig, changed):
    flow = rig.open()
    rig.enqueue(flow)
    if changed == "owner_id":
        rig.owner._owner_id = b"p" * 16
    else:
        rig.owner.states[flow].revision += 1
    result = rig.dispatch()
    assert not result.invoked and result.flow_fenced and not result.flow_committed
    assert rig.calls == rig.owner.commit_calls == []
    assert_unsent_owner(rig.owner, flow, live=1)
    assert rig.budget()["estimated_shared_us"] == 0
    rig.scheduler.close()
    assert rig.owner.commit_calls == []
    assert_unsent_owner(rig.owner, flow, live=1)


@pytest.mark.parametrize("bad", ["copied_binding", "truthy", "none"])
def test_owner_validation_requires_exact_current_binding_object(rig, bad):
    flow = rig.open()
    rig.enqueue(flow)
    factory = rig.owner.dispatch_lease
    def lease_with_bad_validation(handle):
        lease = factory(handle)
        lease.validate = lambda *args, **kwargs: (replace(handle.binding) if bad == "copied_binding"
                                                 else True if bad == "truthy" else None)
        return lease
    rig.owner.dispatch_lease = lease_with_bad_validation
    result = rig.dispatch()
    assert not result.invoked and result.flow_committed
    assert_unsent_owner(rig.owner, flow, terminals=1)
    assert rig.calls == []


def test_owner_lease_enter_must_return_the_exact_lease_instance(rig):
    flow = rig.open()
    rig.enqueue(flow)
    factory = rig.owner.dispatch_lease
    class WrongEnter(FlowDispatchLease):
        def __init__(self, original):
            self.original = original
        def __enter__(self):
            return self.original.__enter__()
        def __exit__(self, *args):
            return self.original.__exit__(*args)
        def validate(self, *args, **kwargs):
            return self.original.validate(*args, **kwargs)
        def commit(self, request):
            return self.original.commit(request)
    rig.owner.dispatch_lease = lambda handle: WrongEnter(factory(handle))
    result = rig.dispatch()
    assert not result.invoked and result.flow_fenced and not result.flow_committed
    assert rig.owner.commit_calls == rig.calls == []
    assert rig.owner.states[flow].credit == 8


@pytest.mark.parametrize("fault", ["before", "after", "truthy_ack"])
def test_ambiguous_owner_commit_after_send_is_fenced_and_never_repeated(rig, fault):
    flow = rig.open()
    rig.enqueue(flow)
    rig.owner.commit_fault = None if fault == "truthy_ack" else fault
    rig.owner.commit_return = True if fault == "truthy_ack" else None
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.ACCEPTED and result.invoked
    assert result.flow_fenced and not result.flow_committed
    assert len(rig.calls) == len(rig.owner.commit_calls) == 1
    assert rig.budget()["estimated_shared_us"] == 1_500_000
    assert rig.budget()["pending_reservations"] == 0
    assert rig.scheduler.snapshot()["fenced_flows"] == 1
    with pytest.raises(ContractError):
        rig.enqueue(flow)
    rig.scheduler.close()
    rig.scheduler.close()
    assert len(rig.owner.commit_calls) == 1
    assert len(rig.calls) == 1
    assert rig.budget()["estimated_shared_us"] == 1_500_000


@pytest.mark.parametrize("fault", ["before", "after", "truthy_ack"])
def test_ambiguous_unsent_return_is_fenced_without_rf_cost_or_repeat(rig, fault):
    flow = rig.open()
    rig.enqueue(flow)
    rig.owner.commit_fault = None if fault == "truthy_ack" else fault
    rig.owner.commit_return = True if fault == "truthy_ack" else None
    result = rig.dispatch(authorize=lambda _: None)
    assert not result.invoked and result.flow_fenced and not result.flow_committed
    assert len(rig.owner.commit_calls) == 1
    assert rig.calls == [] and rig.budget()["estimated_shared_us"] == 0
    assert rig.owner.states[flow].credit == 8
    rig.scheduler.close()
    assert len(rig.owner.commit_calls) == 1


def test_owner_alone_mints_retry_and_logical_credit_is_not_consumed_twice(rig):
    flow = rig.open()
    rig.enqueue(flow)
    first = rig.owner.states[flow].live
    assert rig.dispatch().flow_committed
    assert rig.enqueue(flow) is None
    assert len(rig.calls) == 1
    rig.now_us = 102_000_000
    rig.owner.retry(flow, rig.now_us)
    rig.enqueue(flow)
    second = rig.owner.states[flow].live
    assert second is not first
    assert second.binding.sequence == first.binding.sequence == 7
    assert second.binding.frame_id == first.binding.frame_id == 101
    assert second.binding.attempt_ordinal == 2
    assert rig.dispatch().flow_committed
    assert rig.owner.oracle(flow) == {"credit": 5, "next_sequence": 8, "rf_attempts": 2,
        "retry_deadline_us": 104_000_000, "live_handles": 0, "terminal_callbacks": 2}
    assert rig.budget()["estimated_shared_us"] == 3_000_000
    assert len(rig.calls) == len(rig.owner.commit_calls) == 2


def test_close_returns_each_unsent_owner_candidate_once_and_does_not_claim_drain(rig):
    first, second = rig.open(), rig.open(flow_id=2)
    rig.enqueue(first)
    rig.enqueue(second)
    handles = {id(rig.owner.states[f].live) for f in (first, second)}
    assert rig.scheduler.close() == 2
    assert rig.scheduler.close() == 0
    assert {id(r.reservation) for r in rig.owner.commit_calls} == handles
    assert len(rig.owner.commit_calls) == 2
    assert_unsent_owner(rig.owner, first, terminals=1)
    assert_unsent_owner(rig.owner, second, terminals=1)
    assert rig.calls == [] and rig.budget()["estimated_shared_us"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 0


def test_expiry_returns_unsent_handle_once_while_current_peer_progresses(rig):
    old = rig.open(deadlines=DeadlineSet(101, 900, 2100))
    current = rig.open(peer=rig.other)
    rig.enqueue(old)
    rig.enqueue(current)
    rig.now_us = 101_000_000
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.ACCEPTED and result.flow_committed
    assert rig.calls[0].request.flow == current
    assert_unsent_owner(rig.owner, old, terminals=1)
    assert len(rig.owner.commit_calls) == 2


@pytest.mark.parametrize("bound", ["application", "authority_epoch", "profile", "counter"])
def test_final_owner_validation_delay_is_rechecked_before_actual_carrier_invocation(rig, bound):
    deadlines = DeadlineSet(101 if bound == "application" else 1000, 900, 2100)
    profile = CarrierProfile(CarrierKind.BINARY, 128, 13, 16, b"p" * 32,
        2, 1, 8, .75, .25, .5, 90, 30 if bound == "profile" else 1000,
        ProfileBasis.SOURCE_FIXTURE)
    flow = rig.open(deadlines=deadlines, profile=profile)
    rig.enqueue(flow)
    delayed = []
    def delay_after_reservation():
        # This identifies the independent transaction boundary from the
        # ledger's public state, not the scheduler's private call count/order.
        if not delayed and rig.budget()["pending_reservations"] == 1:
            delayed.append(True)
            if bound == "authority_epoch":
                rig.now_epoch = 2100
            else:
                rig.now_us = {"application": 101_000_000, "profile": 120_000_000,
                              "counter": 110_000_000}[bound]
    rig.owner.on_validate = delay_after_reservation
    result = rig.dispatch()
    assert delayed == [True]
    assert not result.invoked, "A held owner callback may delay; current expiry/counter must be checked afterward"
    assert rig.calls == []
    assert result.flow_committed
    assert_unsent_owner(rig.owner, flow, terminals=1)
    assert rig.budget()["estimated_shared_us"] == 0
    assert rig.budget()["pending_reservations"] == 0


def test_cancelled_cleanup_fence_cannot_dispatch_remaining_queued_handle(rig):
    flow = rig.open()
    rig.enqueue(flow)
    def interrupted():
        raise asyncio.CancelledError("synthetic cancelled reconciliation")
    rig.owner.on_lease_enter = interrupted
    with pytest.raises(asyncio.CancelledError):
        rig.scheduler.close_flow(flow)
    assert rig.scheduler.snapshot()["fenced_flows"] == 1
    rig.owner.on_lease_enter = lambda: None
    result = rig.dispatch()
    assert not result.invoked, "Fenced work surviving interrupted cleanup must never be sent"
    assert rig.calls == []
    assert rig.owner.states[flow].credit == 8
    assert rig.owner.states[flow].rf_attempts == 0
    assert rig.budget()["estimated_shared_us"] == 0


@pytest.mark.parametrize("phase", ["on_service", "on_broker"])
def test_owner_cancellation_between_policy_callbacks_returns_candidate_without_rf(rig, phase):
    flow = rig.open()
    rig.enqueue(flow)
    setattr(rig, phase, lambda: setattr(rig.owner.states[flow], "cancelled", True))
    result = rig.dispatch()
    assert not result.invoked and result.flow_committed
    assert_unsent_owner(rig.owner, flow, terminals=1)
    assert rig.calls == [] and rig.budget()["estimated_shared_us"] == 0


def test_owner_lease_exit_reentry_is_refused_after_one_completed_attempt(rig):
    flow = rig.open()
    rig.enqueue(flow)
    def reenter():
        with pytest.raises(ContractError, match="^scheduler_reentry$"):
            rig.enqueue(flow)
        with pytest.raises(ContractError, match="^scheduler_reentry$"):
            rig.dispatch()
    rig.owner.on_lease_exit = reenter
    result = rig.dispatch()
    assert result.flow_committed and result.invoked
    assert len(rig.calls) == len(rig.owner.commit_calls) == 1
    assert rig.owner.states[flow].rf_attempts == 1


@pytest.mark.parametrize("phase", ["prepare", "validate", "commit"])
@pytest.mark.parametrize("operation", ["enqueue", "dispatch", "close"])
def test_owner_callback_reentry_cannot_duplicate_queue_send_or_completion(rig, phase, operation):
    flow = rig.open()
    def reenter():
        methods = {"enqueue": lambda: rig.enqueue(flow), "dispatch": rig.dispatch,
                   "close": rig.scheduler.close}
        with pytest.raises(ContractError, match="^scheduler_reentry$"):
            methods[operation]()
    if phase != "prepare":
        rig.enqueue(flow)
    setattr(rig.owner, "on_" + phase, reenter)
    if phase == "prepare":
        rig.enqueue(flow)
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.ACCEPTED and result.flow_committed
    assert len(rig.calls) == len(rig.owner.commit_calls) == 1
    assert rig.owner.states[flow].credit == 5


def test_cancelled_owned_candidate_is_returned_once_through_reconciliation_lease(rig):
    flow = rig.open()
    rig.enqueue(flow)
    handle = rig.owner.states[flow].live
    rig.owner.states[flow].cancelled = True
    result = rig.dispatch()
    assert not result.invoked and result.flow_committed and not result.flow_fenced
    assert rig.owner.commit_calls[0].reservation is handle
    assert rig.owner.commit_calls[0].outcome is CommitOutcome.REFUSED_SCHEDULER
    assert_unsent_owner(rig.owner, flow, terminals=1)
    assert rig.calls == [] and rig.budget()["estimated_shared_us"] == 0
    rig.scheduler.close()
    assert len(rig.owner.commit_calls) == 1


@pytest.mark.parametrize("changed", ["route", "profile"])
def test_post_enqueue_context_change_without_current_owner_lease_fences_without_replay(rig, changed):
    flow = rig.open()
    rig.enqueue(flow)
    old = rig.owner.states[flow].binding
    if changed == "route":
        route = replace(old.route, route_generation=2)
        replacement = replace(old, route=route, contract=replace(old.contract, route=route))
    else:
        replacement = replace(old, profile=replace(old.profile, initial_rto_seconds=3))
    # This independent owner cannot authorize a stale immutable handle after
    # its current route/profile changed. A scheduler cannot invent a return.
    rig.owner.states[flow].binding = replacement
    result = rig.dispatch()
    assert not result.invoked and result.flow_fenced and not result.flow_committed
    assert rig.owner.commit_calls == rig.calls == []
    assert_unsent_owner(rig.owner, flow, live=1)
    assert rig.budget()["estimated_shared_us"] == 0
    assert rig.budget()["pending_reservations"] == 0
    rig.scheduler.close()
    assert rig.owner.commit_calls == []
    assert_unsent_owner(rig.owner, flow, live=1)


def test_clean_owner_none_before_eligibility_keeps_registration_usable(rig):
    flow = rig.open()
    state = rig.owner.states[flow]
    state.binding = replace(state.binding, retry_not_before_us=101_000_000)
    assert rig.enqueue(flow) is None
    assert_unsent_owner(rig.owner, flow)
    assert rig.scheduler.snapshot()["fenced_flows"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    assert rig.owner.events == []
    rig.now_us = 101_000_000
    assert rig.enqueue(flow) is not None
    assert_unsent_owner(rig.owner, flow, live=1)
    result = rig.dispatch()
    assert result.invoked and result.flow_committed and not result.flow_fenced
    assert rig.owner.oracle(flow) == {"credit": 5, "next_sequence": 8, "rf_attempts": 1,
        "retry_deadline_us": 103_000_000, "live_handles": 0, "terminal_callbacks": 1}
