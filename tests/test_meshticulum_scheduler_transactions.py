# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Independent synthetic MT04 fairness, bounds and one-send transactions.

No callback in this file authenticates a real peer or touches a device/network.
Expected queue counts, microsecond costs and fairness inequalities are literal.
The scheduler's private queue ordering is never used as the result oracle.
"""

from collections import Counter, defaultdict
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, replace
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
from meshticulum.scheduler.scheduler import (
    DispatchFrame, DispatchGrant, FrameRole, LeaseScope, QueueClass, Scheduler,
    SchedulerPolicy,
)


METRICS = {}


class Rig:
    def __init__(self, policy=None, budget=None, sample=True):
        self.local = PeerID("fixture-home", "a" * 64)
        self.peer = PeerID("fixture-uplink", "b" * 64)
        self.other = PeerID("fixture-other", "c" * 64)
        self.start_us = self.now_us = self.latest_us = 100_000_000
        self.now_epoch = 2000
        self.epoch = b"l" * 16
        self.ledger = Ledger(budget or BudgetPolicy(
            60_000_000, 30_000_000, 60_000_000, 10_000_000,
            quantization_us=1, max_reservations=1), self.epoch, self.start_us)
        if sample:
            self.ledger.observe(CounterSample(self.now_us, 10_000_000, self.epoch), self.now_us)
        self.scheduler = Scheduler(local_peer=self.local, ledger=self.ledger,
                                   epoch=b"s" * 16, broker_epoch=b"b" * 16,
                                   policy=policy or SchedulerPolicy())
        self.records = {}
        self.requests = []
        self.calls = []
        self.active = {LeaseScope.SERVICE: False, LeaseScope.BROKER: False}
        self.grant_changes = {LeaseScope.SERVICE: {}, LeaseScope.BROKER: {}}
        self.grant_request = {LeaseScope.SERVICE: lambda r: r, LeaseScope.BROKER: lambda r: r}
        self.on_service = self.on_service_enter = self.on_service_exit = lambda: None
        self.on_broker = self.on_broker_enter = self.on_broker_exit = lambda: None
        self.on_send = lambda: None
        self.on_clock = lambda n: None
        self.clock_calls = 0
        self.outcome = CommitOutcome.ACCEPTED

    def open(self, *, kind=ServiceKind.SAGE, peer=None, flow_id=1, limits=None,
             components=(.75, .25, .5), deadlines=None, profile=None):
        peer = peer or self.peer
        session = SessionKey(self.local, peer, b"i" * 16, b"g" * 16)
        flow = FlowKey(session, self.local, flow_id)
        profile = profile or CarrierProfile(
            CarrierKind.BINARY, 128, 13, 16, bytes([flow_id]) * 32,
            2, 1, 8, *components, 90, 1000, ProfileBasis.SOURCE_FIXTURE)
        route = RouteBinding(session, ServiceID(peer, kind),
                             "12345678-1234-4234-8234-123456789abc",
                             b"c" * 32, profile.profile_digest, 1)
        limits = limits or CapabilityLimits(64, 1024, 1024, 128, 16,
                                           4096, 6, 8, 2, 65535, 32, 32)
        deadlines = deadlines or DeadlineSet(1000, 900, 2100)
        traffic = TrafficClass.BULK if kind is ServiceKind.KNOWLEDGE else TrafficClass.INTERACTIVE
        contract = NegotiatedContract(1, frozenset(Feature), limits, route,
                                      deadlines, traffic, 1, b"n" * 16)
        self.scheduler.open_flow(flow, contract, profile, now_us=self.now_us, now_epoch=self.now_epoch)
        self.records[flow] = (contract, profile)
        return flow

    def enqueue(self, flow, payload=b"fixture", role=FrameRole.DATA):
        return self.scheduler.enqueue(flow, payload, role=role,
                                      now_us=self.now_us, now_epoch=self.now_epoch)

    def lease(self, request, scope):
        if scope is LeaseScope.SERVICE:
            self.requests.append(request)
        getattr(self, "on_service" if scope is LeaseScope.SERVICE else "on_broker")()
        @contextmanager
        def guard():
            self.active[scope] = True
            try:
                getattr(self, "on_service_enter" if scope is LeaseScope.SERVICE else "on_broker_enter")()
                grant = DispatchGrant(self.grant_request[scope](request), scope,
                                      request.deadlines, 1, self.scheduler.broker_epoch)
                yield replace(grant, **self.grant_changes[scope])
            finally:
                try:
                    getattr(self, "on_service_exit" if scope is LeaseScope.SERVICE else "on_broker_exit")()
                finally:
                    self.active[scope] = False
        return guard()

    def service(self, request):
        return self.lease(request, LeaseScope.SERVICE)

    def broker(self, request):
        assert self.active[LeaseScope.SERVICE]
        return self.lease(request, LeaseScope.BROKER)

    def send(self, frame):
        assert type(frame) is DispatchFrame
        assert all(self.active.values()), "Both fresh host leases must cover the carrier invocation"
        self.calls.append(frame)
        self.on_send()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def clock(self):
        self.clock_calls += 1
        self.on_clock(self.clock_calls)
        if type(self.now_us) is int and self.now_us >= self.latest_us:
            self.latest_us = self.now_us
        return self.now_us

    def dispatch(self, **changes):
        args = dict(authorize=self.service, broker=self.broker, send=self.send,
                    monotonic_us=self.clock, epoch_clock=lambda: self.now_epoch)
        args.update(changes)
        return self.scheduler.dispatch_one(**args)

    def state(self):
        return self.ledger.snapshot(self.latest_us)


@pytest.fixture
def rig():
    return Rig()


def assert_not_spent(rig):
    state = rig.state()
    assert state["estimated_own_us"] == state["estimated_shared_us"] == 0
    assert state["pending_reservations"] == 0
    assert rig.calls == []


def test_one_send_is_covered_by_both_exact_fresh_leases_and_full_cost(rig):
    flow = rig.open()
    frame_id = rig.enqueue(flow, b"private-fixture")
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.ACCEPTED and result.invoked
    assert result.post_send_checks_passed is True
    assert len(rig.calls) == 1
    request = rig.calls[0].request
    contract, profile = rig.records[flow]
    assert request.flow == flow and request.route == contract.route
    assert request.frame_id == frame_id
    assert request.payload_digest == hashlib.sha256(b"private-fixture").digest()
    assert request.payload_bytes == 15
    assert request.profile == profile
    assert request.deadlines == contract.deadlines
    assert request.role is FrameRole.DATA and request.queue_class is QueueClass.INTERACTIVE
    assert request.cost == Cost(750_000, 1_500_000)
    assert len(request.nonce) == 16 and any(request.nonce)
    assert request.scheduler_epoch == b"s" * 16 and request.broker_epoch == b"b" * 16
    assert rig.state()["estimated_own_us"] == 750_000
    assert rig.state()["estimated_shared_us"] == 1_500_000
    assert rig.state()["pending_reservations"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    assert rig.dispatch().outcome is None
    assert len(rig.calls) == 1


@pytest.mark.parametrize("adapter", ["authorize", "broker", "send", "monotonic_us", "epoch_clock"])
def test_missing_dispatch_adapter_never_allocates_or_sends(rig, adapter):
    flow = rig.open()
    rig.enqueue(flow)
    result = rig.dispatch(**{adapter: None})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert rig.scheduler.snapshot()["queued_frames"] == 1
    assert_not_spent(rig)


@pytest.mark.parametrize("scope", list(LeaseScope))
@pytest.mark.parametrize("value", [None, False, True, "approved", {"messaging_allowed": True}])
def test_truthy_permission_is_never_a_current_policy_lease(rig, scope, value):
    flow = rig.open()
    rig.enqueue(flow)
    name = "authorize" if scope is LeaseScope.SERVICE else "broker"
    result = rig.dispatch(**{name: lambda _: value})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)
    assert rig.scheduler.snapshot()["queued_frames"] == 0


@pytest.mark.parametrize("scope", list(LeaseScope))
@pytest.mark.parametrize("fault", ["truthy_grant", "wrong_scope", "wrong_broker_epoch", "wrong_nonce"])
def test_each_policy_lease_requires_exact_scope_epoch_and_nonce_request(rig, scope, fault):
    flow = rig.open()
    rig.enqueue(flow)
    @contextmanager
    def lease(request):
        if fault == "truthy_grant":
            yield True
        else:
            yield DispatchGrant(
                replace(request, nonce=b"x" * 16) if fault == "wrong_nonce" else request,
                (LeaseScope.BROKER if scope is LeaseScope.SERVICE else LeaseScope.SERVICE)
                if fault == "wrong_scope" else scope,
                request.deadlines, 1,
                b"x" * 16 if fault == "wrong_broker_epoch" else request.broker_epoch)
    name = "authorize" if scope is LeaseScope.SERVICE else "broker"
    result = rig.dispatch(**{name: lease})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


@pytest.mark.parametrize("scope", list(LeaseScope))
@pytest.mark.parametrize("field", ["scheduler_epoch", "broker_epoch", "flow", "route", "frame_id",
                                  "payload_digest", "payload_bytes", "profile", "deadlines"])
def test_grants_for_one_field_changed_dispatch_cannot_authorize_send(rig, scope, field):
    flow = rig.open()
    rig.enqueue(flow)
    def changed(request):
        values = {
            "scheduler_epoch": b"x" * 16, "broker_epoch": b"x" * 16,
            "flow": replace(flow, flow_id=2),
            "route": replace(request.route, route_generation=2), "frame_id": request.frame_id + 1,
            "payload_digest": b"x" * 32, "payload_bytes": request.payload_bytes + 1,
            "profile": replace(request.profile, initial_rto_seconds=3),
            "deadlines": DeadlineSet(999, 899, 2099),
        }
        return replace(request, **{field: values[field]})
    rig.grant_request[scope] = changed
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


@pytest.mark.parametrize("scope", list(LeaseScope))
def test_cached_grant_is_refused_and_a_new_request_is_fresh(rig, scope):
    flow = rig.open()
    rig.enqueue(flow)
    assert rig.dispatch().outcome is CommitOutcome.ACCEPTED
    old = rig.requests[-1]
    cached = DispatchGrant(old, scope, old.deadlines, 1, old.broker_epoch)
    rig.enqueue(flow)
    @contextmanager
    def replay(_):
        yield cached
    name = "authorize" if scope is LeaseScope.SERVICE else "broker"
    result = rig.dispatch(**{name: replay})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert len(rig.calls) == 1
    # Capture the actual second SERVICE request even when replacing that callback.
    assert rig.state()["estimated_shared_us"] == 1_500_000
    rig.enqueue(flow)
    assert rig.dispatch().outcome is CommitOutcome.ACCEPTED
    assert rig.requests[-1].nonce != old.nonce


def test_fresh_policy_revocation_after_enqueue_is_not_historical_permission(rig):
    flow = rig.open()
    rig.enqueue(flow)
    @contextmanager
    def revoked(_):
        yield None
    result = rig.dispatch(authorize=revoked)
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER
    assert_not_spent(rig)
    assert rig.scheduler.snapshot()["queued_frames"] == 0


@pytest.mark.parametrize("phase", ["on_service", "on_service_enter", "on_broker", "on_broker_enter"])
@pytest.mark.parametrize("bound", ["application", "authority_monotonic", "authority_epoch", "profile"])
def test_callback_delay_cannot_cross_original_exclusive_bounds_before_send(rig, phase, bound):
    deadlines = DeadlineSet(120 if bound == "application" else 1000,
                            120 if bound == "authority_monotonic" else 900,
                            2020 if bound == "authority_epoch" else 2100)
    profile = CarrierProfile(CarrierKind.BINARY, 128, 13, 16, b"p" * 32,
                             2, 1, 8, .75, .25, .5, 90,
                             30 if bound == "profile" else 1000, ProfileBasis.SOURCE_FIXTURE)
    flow = rig.open(deadlines=deadlines, profile=profile)
    rig.enqueue(flow)
    setattr(rig, phase, lambda: setattr(rig, "now_epoch" if bound == "authority_epoch" else "now_us",
                                     2020 if bound == "authority_epoch" else 120_000_000))
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


@pytest.mark.parametrize("phase", ["on_service_enter", "on_broker_enter"])
@pytest.mark.parametrize("clock", ["monotonic", "epoch"])
def test_clock_rollback_during_current_leases_does_not_send(rig, phase, clock):
    flow = rig.open()
    rig.enqueue(flow)
    setattr(rig, phase, lambda: setattr(rig, "now_us" if clock == "monotonic" else "now_epoch",
                                     99_000_000 if clock == "monotonic" else 1999))
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


@pytest.mark.parametrize("clock,value", [
    ("monotonic_us", True), ("monotonic_us", 100_000_000.0),
    ("monotonic_us", -1), ("monotonic_us", 1 << 63),
    ("epoch_clock", True), ("epoch_clock", 0),
    ("epoch_clock", float("nan")), ("epoch_clock", float("inf")),
])
def test_bad_dispatch_clocks_refuse_without_queue_or_charge_mutation(rig, clock, value):
    flow = rig.open()
    rig.enqueue(flow)
    result = rig.dispatch(**{clock: lambda: value})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert rig.scheduler.snapshot()["queued_frames"] == 1
    assert_not_spent(rig)


@pytest.mark.parametrize("scope", list(LeaseScope))
@pytest.mark.parametrize("fault", ["denied", "expired"])
def test_suppressing_policy_lease_never_turns_denial_into_a_send(rig, scope, fault):
    flow = rig.open()
    rig.enqueue(flow)
    class Suppressor(AbstractContextManager):
        def __init__(self, request):
            self.request = request
        def __enter__(self):
            if fault == "denied":
                return True
            return DispatchGrant(self.request, scope, DeadlineSet(100, 900, 2100),
                                 1, self.request.broker_epoch)
        def __exit__(self, kind, *_):
            assert kind is ContractError
            return True
    name = "authorize" if scope is LeaseScope.SERVICE else "broker"
    result = rig.dispatch(**{name: Suppressor})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


@pytest.mark.parametrize("scope", list(LeaseScope))
@pytest.mark.parametrize("value", [True, 1, 0, "", "allow"])
def test_lease_exit_requires_none_or_exact_false_after_one_accepted_send(rig, scope, value):
    flow = rig.open()
    rig.enqueue(flow)
    class BadExit(AbstractContextManager):
        def __init__(self, request):
            self.request = request
        def __enter__(self):
            rig.active[scope] = True
            return DispatchGrant(self.request, scope, self.request.deadlines, 1,
                                 self.request.broker_epoch)
        def __exit__(self, *_):
            rig.active[scope] = False
            return value
    name = "authorize" if scope is LeaseScope.SERVICE else "broker"
    result = rig.dispatch(**{name: BadExit})
    assert result.outcome is CommitOutcome.ACCEPTED and result.invoked
    assert result.post_send_checks_passed is False
    assert len(rig.calls) == 1
    assert rig.state()["estimated_shared_us"] == 1_500_000
    assert rig.state()["pending_reservations"] == 0


def test_outer_service_lease_cannot_suppress_inner_broker_exit_failure(rig):
    flow = rig.open()
    rig.enqueue(flow)
    def broker_failed():
        raise RuntimeError("synthetic broker unlock failure")
    rig.on_broker_exit = broker_failed
    class SuppressingService(AbstractContextManager):
        def __init__(self, request):
            self.request = request
        def __enter__(self):
            rig.active[LeaseScope.SERVICE] = True
            return DispatchGrant(self.request, LeaseScope.SERVICE,
                                 self.request.deadlines, 1, self.request.broker_epoch)
        def __exit__(self, kind, *_):
            rig.active[LeaseScope.SERVICE] = False
            assert kind is RuntimeError
            return True
    result = rig.dispatch(authorize=SuppressingService)
    assert result.outcome is CommitOutcome.ACCEPTED and result.invoked
    assert result.post_send_checks_passed is False
    assert result.reason == "post_send_guard_failed"
    assert len(rig.calls) == 1
    assert rig.state()["estimated_shared_us"] == 1_500_000
    assert rig.state()["pending_reservations"] == 0


@pytest.mark.parametrize("scope", list(LeaseScope))
@pytest.mark.parametrize("field,value", [("revision", True), ("revision", 0),
                                         ("revision", 1 << 64), ("broker_epoch", bytes(16)),
                                         ("scope", "service"), ("deadlines", (1000, 900, 2100))])
def test_malformed_host_grant_fields_are_revalidated_before_send(rig, scope, field, value):
    flow = rig.open()
    rig.enqueue(flow)
    @contextmanager
    def malformed(request):
        grant = DispatchGrant(request, scope, request.deadlines, 1, request.broker_epoch)
        # Deliberately simulate a malformed trusted adapter result. This does
        # not alter the scheduler-owned request or assume a malicious verifier.
        object.__setattr__(grant, field, value)
        yield grant
    name = "authorize" if scope is LeaseScope.SERVICE else "broker"
    result = rig.dispatch(**{name: malformed})
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


@pytest.mark.parametrize("phase", ["on_service", "on_broker_enter", "on_send"])
@pytest.mark.parametrize("operation", ["dispatch", "enqueue", "close", "close_scheduler", "snapshot"])
def test_callback_reentry_fails_before_any_nested_scheduler_mutation(rig, phase, operation):
    flow = rig.open()
    rig.enqueue(flow)
    def reenter():
        choices = {"dispatch": rig.dispatch, "enqueue": lambda: rig.enqueue(flow),
                   "close": lambda: rig.scheduler.close_flow(flow),
                   "close_scheduler": rig.scheduler.close,
                   "snapshot": rig.scheduler.snapshot}
        with pytest.raises(ContractError, match="^scheduler_reentry$"):
            choices[operation]()
    setattr(rig, phase, reenter)
    assert rig.dispatch().outcome is CommitOutcome.ACCEPTED
    assert len(rig.calls) == 1
    assert rig.state()["estimated_shared_us"] == 1_500_000
    assert rig.state()["pending_reservations"] == 0
    assert rig.scheduler.snapshot()["flows"] == 1


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN,
                                     None, True, False, "confirmed_carrier_acceptance",
                                     CommitOutcome.REFUSED_SCHEDULER,
                                     RuntimeError("synthetic carrier exception")])
def test_invoked_ambiguity_is_charged_once_and_never_automatically_retried(rig, outcome):
    flow = rig.open()
    rig.enqueue(flow)
    rig.outcome = outcome
    result = rig.dispatch()
    expected = CommitOutcome.ACCEPTED if outcome is CommitOutcome.ACCEPTED else CommitOutcome.UNKNOWN
    assert result.outcome is expected and result.invoked
    assert rig.state()["estimated_own_us"] == 750_000
    assert rig.state()["estimated_shared_us"] == 1_500_000
    assert rig.state()["pending_reservations"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    assert rig.dispatch().outcome is None
    assert len(rig.calls) == 1


def test_confirmed_carrier_prequeue_refusal_refunds_but_requires_explicit_new_attempt(rig):
    flow = rig.open()
    first_id = rig.enqueue(flow)
    rig.outcome = CommitOutcome.REFUSED_CARRIER
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_CARRIER and result.invoked
    assert rig.state()["estimated_shared_us"] == 0
    assert rig.state()["pending_reservations"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    old_nonce = rig.calls[-1].request.nonce
    assert rig.dispatch().outcome is None and len(rig.calls) == 1
    new_id = rig.enqueue(flow)
    assert new_id != first_id
    rig.outcome = CommitOutcome.ACCEPTED
    assert rig.dispatch().outcome is CommitOutcome.ACCEPTED
    assert rig.calls[-1].request.nonce != old_nonce
    assert rig.state()["estimated_shared_us"] == 1_500_000


@pytest.mark.parametrize("phase", ["on_service", "on_service_enter", "on_broker", "on_broker_enter"])
def test_preinvoke_dependency_exception_releases_without_spending(rig, phase):
    flow = rig.open()
    rig.enqueue(flow)
    def unavailable():
        raise RuntimeError("synthetic dependency unavailable")
    setattr(rig, phase, unavailable)
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


@pytest.mark.parametrize("phase", ["on_send", "on_broker_exit", "on_service_exit"])
@pytest.mark.parametrize("fault", ["deadline", "exception", "clock_rollback"])
def test_postinvoke_guard_failure_does_not_refund_or_repeat_a_send(rig, phase, fault):
    flow = rig.open()
    rig.enqueue(flow)
    def fail():
        if fault == "deadline":
            rig.now_epoch = 2100
        elif fault == "clock_rollback":
            rig.now_epoch = 1999
        else:
            raise RuntimeError("synthetic post-invocation guard failure")
    setattr(rig, phase, fail)
    result = rig.dispatch()
    expected = (CommitOutcome.UNKNOWN if phase == "on_send" and fault == "exception"
                else CommitOutcome.ACCEPTED)
    assert result.outcome is expected and result.invoked
    assert result.post_send_checks_passed is (phase == "on_send" and fault == "exception")
    assert len(rig.calls) == 1
    assert rig.state()["estimated_shared_us"] == 1_500_000
    assert rig.state()["pending_reservations"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 0


@pytest.mark.parametrize("sample", ["missing", "stale", "reset", "wrong_epoch"])
def test_unusable_firmware_counter_refuses_and_keeps_unsent_budget_work(sample):
    rig = Rig(sample=sample != "missing")
    flow = rig.open()
    rig.enqueue(flow)
    if sample == "stale":
        rig.now_us += 10_000_000
    elif sample in ("reset", "wrong_epoch"):
        rig.now_us += 1
        with pytest.raises(ContractError):
            rig.ledger.observe(CounterSample(rig.now_us, 9_000_000 if sample == "reset" else 10_000_000,
                                            rig.epoch if sample == "reset" else b"x" * 16), rig.now_us)
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)
    assert rig.scheduler.snapshot()["queued_frames"] == 1


def test_counter_age_is_rechecked_after_reservation_at_final_presend_clock(rig):
    flow = rig.open()
    rig.enqueue(flow)
    def delayed_clock(n):
        if n == 4:
            rig.now_us = 110_000_000
    rig.on_clock = delayed_clock
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)


def test_counter_reset_after_reservation_fences_send_and_refunds_pending_cost(rig):
    flow = rig.open()
    rig.enqueue(flow)
    def new_reading(n):
        if n == 4:
            rig.now_us += 1
            with pytest.raises(ContractError, match="^counter_decreased$"):
                rig.ledger.observe(CounterSample(rig.now_us, 9_000_000, rig.epoch), rig.now_us)
    rig.on_clock = new_reading
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)
    assert rig.state()["fenced"] is True


def test_unsolicited_firmware_tx_after_reservation_invalidates_insufficient_budget():
    rig = Rig(budget=BudgetPolicy(60_000_000, 750_001, 1_500_001, 10_000_000,
                                 quantization_us=1, max_reservations=1))
    flow = rig.open()
    rig.enqueue(flow)
    def new_reading(n):
        if n == 4:
            rig.now_us += 1
            # One independently observed firmware TX microsecond exhausts the
            # previously exact own-TX boundary while the reservation is live.
            rig.ledger.observe(CounterSample(rig.now_us, 10_000_001, rig.epoch), rig.now_us)
    rig.on_clock = new_reading
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert_not_spent(rig)
    assert rig.state()["own_used_us"] == 2


@pytest.mark.parametrize("shared_limit,accepted", [(1_500_001, True), (1_500_000, False)])
def test_complete_three_component_cost_and_counter_quantization_are_budgeted(shared_limit, accepted):
    rig = Rig(budget=BudgetPolicy(60_000_000, 750_001, shared_limit, 10_000_000,
                                 quantization_us=1, max_reservations=1))
    flow = rig.open()
    rig.enqueue(flow)
    # Initial counter flooring guard consumes 1us. Full record consumes
    # 750000 forward + 250000 firmware ACK + 500000 reverse = 1500000us.
    result = rig.dispatch()
    assert result.invoked is accepted
    assert len(rig.calls) == int(accepted)
    assert rig.state()["estimated_shared_us"] == (1_500_000 if accepted else 0)
    assert rig.state()["pending_reservations"] == 0


def test_two_routes_on_one_medium_do_not_duplicate_budget_allowance():
    rig = Rig(budget=BudgetPolicy(60_000_000, 1_000_001, 1_500_001, 10_000_000,
                                 quantization_us=1, max_reservations=1))
    first = rig.open(components=(.5, .25, .25))
    second = rig.open(peer=rig.other, components=(.5, .25, .25))
    rig.enqueue(first)
    rig.enqueue(second)
    results = [rig.dispatch(), rig.dispatch()]
    assert sum(r.invoked for r in results) == 1
    assert len(rig.calls) == 1
    assert rig.state()["estimated_own_us"] == 500_000
    assert rig.state()["estimated_shared_us"] == 1_000_000
    assert rig.state()["pending_reservations"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 1


def small_policy(**changes):
    values = dict(max_queued_frames=8, max_queued_bytes=64, max_peer_queued_bytes=64,
                  max_flow_queued_frames=8, max_control_queued_frames=4,
                  max_control_queued_bytes=32)
    values.update(changes)
    return SchedulerPolicy(**values)


@pytest.mark.parametrize("boundary", ["flow_frames", "flow_bytes", "peer_bytes", "global_bytes",
                                     "global_frames", "control_bytes", "control_frames"])
def test_independent_exact_queue_ceiling_then_one_more_is_atomic(boundary):
    policy = small_policy(**{
        "flow_frames": {"max_flow_queued_frames": 2},
        "flow_bytes": {}, "peer_bytes": {"max_peer_queued_bytes": 16},
        "global_bytes": {"max_queued_bytes": 32, "max_peer_queued_bytes": 32},
        "global_frames": {"max_queued_frames": 4, "max_control_queued_frames": 4},
        "control_bytes": {"max_control_queued_bytes": 16},
        "control_frames": {"max_control_queued_frames": 2},
    }[boundary])
    rig = Rig(policy)
    limits = CapabilityLimits(8, 128, 128, 64, 16, 16 if boundary == "flow_bytes" else 4096,
                              6, 8, 2, 65535, 32, 32)
    first = rig.open(limits=limits)
    second = rig.open(peer=rig.other, limits=limits)
    role = FrameRole.CONTROL if boundary.startswith("control") else FrameRole.DATA
    count = 4 if boundary.startswith("global") else 2
    for n in range(count):
        rig.enqueue(second if boundary.startswith("global") and n >= 2 else first, b"12345678", role)
    before = rig.scheduler.snapshot()
    assert before["queued_frames"] == count
    assert before["queued_bytes"] == count * 8
    with pytest.raises(ContractError, match="^scheduler_queue_limit$"):
        rig.enqueue(first, b"x", role)
    assert rig.scheduler.snapshot() == before
    assert_not_spent(rig)


def test_negotiated_control_record_cap_cannot_be_bypassed_by_data_payload_cap(rig):
    flow = rig.open()
    rig.enqueue(flow, b"c" * 32, FrameRole.CONTROL)
    before = rig.scheduler.snapshot()
    with pytest.raises(ContractError):
        rig.enqueue(flow, b"c" * 33, FrameRole.CONTROL)
    assert rig.scheduler.snapshot() == before
    # The same size is valid ordinary data, with locally interactive priority.
    rig.enqueue(flow, b"d" * 33)


def test_peer_byte_quota_covers_both_flows_instead_of_resetting_per_flow():
    rig = Rig(small_policy(max_peer_queued_bytes=16))
    first, second = rig.open(), rig.open(flow_id=2)
    rig.enqueue(first, b"a" * 8)
    rig.enqueue(second, b"b" * 8)
    before = rig.scheduler.snapshot()
    with pytest.raises(ContractError, match="^scheduler_queue_limit$"):
        rig.enqueue(second, b"x")
    assert rig.scheduler.snapshot() == before
    assert rig.scheduler.close_flow(first) == 1
    rig.enqueue(second, b"x")
    assert rig.scheduler.snapshot()["queued_bytes"] == 9


@pytest.mark.parametrize("limit", ["peer", "global"])
def test_flow_count_admission_has_independent_peer_and_global_caps(limit):
    rig = Rig(SchedulerPolicy(max_flows=2 if limit == "global" else 8,
                              max_flows_per_peer=1 if limit == "peer" else 2))
    rig.open()
    if limit == "global":
        rig.open(flow_id=2)
        with pytest.raises(ContractError, match="^scheduler_flow_limit$"):
            rig.open(peer=rig.other)
        assert rig.scheduler.snapshot()["flows"] == 2
    else:
        with pytest.raises(ContractError, match="^scheduler_flow_limit$"):
            rig.open(flow_id=2)
        rig.open(peer=rig.other)
        assert rig.scheduler.snapshot()["flows"] == 2


@pytest.mark.parametrize("payload,role", [
    (bytearray(b"a"), FrameRole.DATA), (memoryview(b"a"), FrameRole.DATA),
    (b"a", "control"), (b"a", True), (b"", FrameRole.DATA), (b"a" * 65, FrameRole.DATA),
])
def test_remote_labels_or_mutable_payloads_do_not_allocate_scheduler_work(rig, payload, role):
    flow = rig.open()
    before = rig.scheduler.snapshot()
    with pytest.raises(ContractError):
        rig.enqueue(flow, payload, role)
    assert rig.scheduler.snapshot() == before
    assert_not_spent(rig)


def test_queue_class_is_derived_from_local_service_and_explicit_host_control():
    rig = Rig()
    knowledge = rig.open(kind=ServiceKind.KNOWLEDGE)
    sage = rig.open(peer=rig.other)
    rig.enqueue(knowledge, b"bulk")
    rig.enqueue(sage, b"interactive")
    rig.enqueue(sage, b"trusted-host-control", FrameRole.CONTROL)
    for _ in range(3):
        assert rig.dispatch().invoked
    seen = {(frame.request.role, frame.request.route.service.kind): frame.request.queue_class for frame in rig.calls}
    assert seen == {(FrameRole.DATA, ServiceKind.KNOWLEDGE): QueueClass.BULK,
                    (FrameRole.DATA, ServiceKind.SAGE): QueueClass.INTERACTIVE,
                    (FrameRole.CONTROL, ServiceKind.SAGE): QueueClass.CONTROL}


def test_cancel_releases_resources_and_retires_full_flow_without_reuse(rig):
    first = rig.open()
    second = rig.open(flow_id=2)
    rig.enqueue(first, b"a" * 8)
    rig.enqueue(first, b"c" * 8, FrameRole.CONTROL)
    rig.enqueue(second, b"b" * 8)
    assert rig.scheduler.close_flow(first) == 2
    state = rig.scheduler.snapshot()
    assert state["queued_frames"] == 1 and state["queued_bytes"] == 8
    assert state["control_queued_frames"] == state["control_queued_bytes"] == 0
    assert state["retired_flows"] == 1
    with pytest.raises(ContractError, match="^scheduler_flow_reused$"):
        contract, profile = rig.records[first]
        rig.scheduler.open_flow(first, contract, profile, now_us=rig.now_us, now_epoch=rig.now_epoch)
    with pytest.raises(ContractError, match="^scheduler_flow_unavailable$"):
        rig.enqueue(first)
    assert rig.dispatch().invoked
    assert rig.calls[0].request.flow == second
    assert rig.scheduler.close_flow(second) == 0
    assert rig.scheduler.snapshot()["queued_frames"] == rig.scheduler.snapshot()["queued_bytes"] == 0


def test_retirement_capacity_fails_closed_without_unbounded_tombstones():
    rig = Rig(SchedulerPolicy(max_flows=1, max_flows_per_peer=1, max_registrations=2))
    for flow_id in (1, 2):
        flow = rig.open(flow_id=flow_id)
        rig.enqueue(flow)
        rig.scheduler.close_flow(flow)
    assert rig.scheduler.snapshot()["retired_flows"] == 2
    with pytest.raises(ContractError, match="^scheduler_registration_exhausted$"):
        rig.open(flow_id=3)
    assert rig.scheduler.snapshot()["flows"] == 0
    assert rig.scheduler.snapshot()["queued_frames"] == 0


def test_terminal_close_drops_all_queues_is_idempotent_and_fences_future_actions(rig):
    first, second = rig.open(), rig.open(flow_id=2)
    rig.enqueue(first, b"data-a")
    rig.enqueue(first, b"control-a", FrameRole.CONTROL)
    rig.enqueue(second, b"data-b")
    assert rig.scheduler.snapshot()["queued_frames"] == 3
    assert rig.scheduler.close() == 3
    after = rig.scheduler.snapshot()
    assert after["closed"] is True
    assert after["flows"] == 0 and after["retired_flows"] == 2
    assert after["queued_frames"] == after["queued_bytes"] == 0
    assert after["control_queued_frames"] == after["control_queued_bytes"] == 0
    assert rig.scheduler.close() == 0
    assert rig.scheduler.snapshot() == after
    for action in [lambda: rig.open(flow_id=3), lambda: rig.enqueue(first), rig.dispatch]:
        with pytest.raises(ContractError, match="^scheduler_closed$"):
            action()
    assert rig.scheduler.snapshot() == after
    assert_not_spent(rig)


def test_expired_old_route_is_discarded_without_blocking_current_peer(rig):
    old = rig.open(deadlines=DeadlineSet(101, 900, 2100))
    current = rig.open(peer=rig.other)
    rig.enqueue(old)
    rig.enqueue(current)
    rig.now_us = 101_000_000
    assert rig.dispatch().outcome is CommitOutcome.ACCEPTED
    assert rig.calls[0].request.flow == current
    state = rig.scheduler.snapshot()
    assert state["flows"] == 1 and state["retired_flows"] == 1
    assert state["events"]["expired"] == 1


def fairness_rig():
    return Rig(SchedulerPolicy(quantum_us=10_000, max_frame_cost_us=40_000))


def test_sustained_local_class_weights_give_bulk_positive_bounded_progress():
    rig = fairness_rig()
    interactive = rig.open(components=(.0025, .0025, .005))
    bulk = rig.open(peer=rig.other, kind=ServiceKind.KNOWLEDGE, components=(.0025, .0025, .005))
    for flow, role in [(interactive, FrameRole.DATA), (interactive, FrameRole.CONTROL), (bulk, FrameRole.DATA)]:
        for _ in range(8):
            rig.enqueue(flow, b"x", role)
    for _ in range(60):
        result = rig.dispatch()
        assert result.invoked
        frame = rig.calls[-1]
        rig.enqueue(frame.request.flow, b"x", frame.request.role)
    counts = Counter(frame.request.queue_class for frame in rig.calls)
    # Independently fixed1:4:1 weighting, equal10ms costs, nonempty queues.
    # Allow a complete bounded cycle transient rather than assert queue order.
    assert 5 <= counts[QueueClass.BULK] <= 15
    assert 5 <= counts[QueueClass.CONTROL] <= 15
    assert 30 <= counts[QueueClass.INTERACTIVE] <= 50
    assert rig.state()["estimated_shared_us"] == 600_000
    METRICS["sustained_classes"] = {
        "dispatch_opportunities": 60, "backlogged_classes": 3,
        "literal_full_cost_us_per_frame": 10_000,
        "observed_dispatches": {c.value: counts[c] for c in QueueClass},
        "estimated_shared_us": rig.state()["estimated_shared_us"],
        "oracle": "control/bulk5..15; interactive30..50; no exact queue order requirement",
    }


def test_peer_aggregate_airtime_is_fair_with_two_variable_cost_flows_vs_one():
    rig = fairness_rig()
    cheap = rig.open(flow_id=1, components=(.0025, .0025, .005))
    expensive = rig.open(flow_id=2, components=(.01, .01, .02))
    other = rig.open(peer=rig.other, components=(.005, .005, .01))
    literal_costs = {cheap: 10_000, expensive: 40_000, other: 20_000}
    for flow in literal_costs:
        for _ in range(4):
            rig.enqueue(flow, b"x")
    for _ in range(120):
        assert rig.dispatch().invoked
        rig.enqueue(rig.calls[-1].request.flow, b"x")
    totals, flow_counts = defaultdict(int), Counter()
    for frame in rig.calls:
        flow = frame.request.flow
        totals[rig.records[flow][0].route.service.provider] += literal_costs[flow]
        flow_counts[flow] += 1
    # Four maximum-sized frames cover bounded startup/final-round skew.
    assert abs(totals[rig.peer] - totals[rig.other]) <= 160_000
    assert totals[rig.peer] > 500_000 and totals[rig.other] > 500_000
    assert flow_counts[cheap] > 0 and flow_counts[expensive] > 0
    # Within-peer flow RR promises opportunities, not equal flow airtime.
    assert abs(flow_counts[cheap] - flow_counts[expensive]) <= 1
    METRICS["variable_cost_peer_aggregate"] = {
        "dispatch_opportunities": 120,
        "peer_A_flows": 2, "peer_B_flows": 1,
        "literal_full_cost_us": {"peer_A_cheap": 10_000, "peer_A_expensive": 40_000, "peer_B": 20_000},
        "observed_flow_dispatches": {"peer_A_cheap": flow_counts[cheap], "peer_A_expensive": flow_counts[expensive], "peer_B": flow_counts[other]},
        "observed_peer_airtime_us": {"peer_A": totals[rig.peer], "peer_B": totals[rig.other]},
        "oracle": "Each aggregate>500000us; absolute skew<=160000us; both A flows positive and counts differ<=1. No equal per-flow airtime promise.",
    }


def test_equal_peer_airtime_is_not_multiplied_by_opening_two_flows():
    rig = fairness_rig()
    first = rig.open(flow_id=1, components=(.0025, .0025, .005))
    second = rig.open(flow_id=2, components=(.0025, .0025, .005))
    other = rig.open(peer=rig.other, components=(.0025, .0025, .005))
    for flow in (first, second, other):
        for _ in range(4):
            rig.enqueue(flow, b"x")
    for _ in range(80):
        assert rig.dispatch().invoked
        rig.enqueue(rig.calls[-1].request.flow, b"x")
    counts = Counter(rig.records[frame.request.flow][0].route.service.provider for frame in rig.calls)
    assert 32 <= counts[rig.peer] <= 48
    assert 32 <= counts[rig.other] <= 48
    assert {frame.request.flow for frame in rig.calls} == {first, second, other}
    METRICS["equal_cost_unequal_flow_counts"] = {
        "dispatch_opportunities": 80, "peer_A_flows": 2, "peer_B_flows": 1,
        "literal_full_cost_us_per_frame": 10_000,
        "observed_peer_dispatches": {"peer_A": counts[rig.peer], "peer_B": counts[rig.other]},
        "oracle": "Each aggregate32..48 of80 opportunities; all three flows progress",
    }


def test_denied_control_flood_yields_to_eligible_interactive_and_bulk():
    rig = fairness_rig()
    interactive = rig.open(components=(.0025, .0025, .005))
    bulk = rig.open(peer=rig.other, kind=ServiceKind.KNOWLEDGE, components=(.0025, .0025, .005))
    for _ in range(4):
        rig.enqueue(interactive, b"c", FrameRole.CONTROL)
        rig.enqueue(interactive, b"i")
        rig.enqueue(bulk, b"b")
    @contextmanager
    def service(request):
        rig.requests.append(request)
        if request.role is FrameRole.CONTROL:
            yield None
        else:
            with rig.service(request) as grant:
                yield grant
    for _ in range(30):
        before = len(rig.requests)
        result = rig.dispatch(authorize=service)
        request = rig.requests[before]
        rig.enqueue(request.flow, b"x", request.role)
        if request.role is FrameRole.CONTROL:
            assert not result.invoked
    counts = Counter(frame.request.queue_class for frame in rig.calls)
    assert counts[QueueClass.INTERACTIVE] >= 5
    assert counts[QueueClass.BULK] >= 2
    assert counts[QueueClass.CONTROL] == 0
    assert rig.state()["pending_reservations"] == 0
    METRICS["denied_control_flood"] = {
        "dispatch_opportunities": 30,
        "observed_carrier_dispatches": {c.value: counts[c] for c in QueueClass},
        "denied_control_requests": sum(r.role is FrameRole.CONTROL for r in rig.requests),
        "oracle": "Interactive>=5,bulk>=2,control0; denied records do not monopolize cursor",
    }


def test_permanently_unaffordable_peer_does_not_block_a_cheaper_eligible_peer():
    rig = Rig(budget=BudgetPolicy(60_000_000, 1_000_001, 1_000_001, 10_000_000,
                                 quantization_us=1, max_reservations=1))
    expensive = rig.open(components=(.75, .25, .5))
    cheap = rig.open(peer=rig.other, components=(.25, .125, .125))
    rig.enqueue(expensive)
    rig.enqueue(cheap)
    for _ in range(6):
        rig.dispatch()
    assert len(rig.calls) == 1
    assert rig.calls[0].request.flow == cheap
    assert rig.state()["estimated_shared_us"] == 500_000
    assert rig.scheduler.snapshot()["queued_frames"] == 1
    METRICS["unaffordable_peer"] = {
        "dispatch_opportunities": 6, "initial_shared_remaining_us": 1_000_000,
        "expensive_full_cost_us": 1_500_000, "cheap_full_cost_us": 500_000,
        "observed_carrier_dispatches": 1, "estimated_shared_us": 500_000,
        "remaining_queued_frames": 1,
        "oracle": "Exactly cheap peer sends once; expensive budget-blocked work remains queued",
    }


@pytest.mark.parametrize("field", list(SchedulerPolicy.__dataclass_fields__))
@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_local_scheduler_limits_require_exact_nonboolean_integer_policy(field, value):
    with pytest.raises(ContractError, match="^invalid_scheduler_policy$"):
        replace(SchedulerPolicy(), **{field: value})


def test_bounded_diagnostics_exclude_payload_and_dispatch_record_repr(rig, caplog, capsys):
    flow = rig.open()
    canary = b"private-fixture-canary"
    rig.enqueue(flow, canary)
    assert canary.decode() not in repr(rig.scheduler.snapshot())
    rig.outcome = RuntimeError("secret-fixture-canary")
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.UNKNOWN
    assert canary.decode() not in repr(rig.calls[0])
    assert "secret-fixture-canary" not in repr(result)
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
