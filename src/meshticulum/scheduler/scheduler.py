# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Bounded airtime scheduling and one-send lease transactions.

This single-threaded model has no device, socket, permission store or broker
implementation. Trusted host callbacks supply current service and broker read
leases and a carrier queue result. It must live above the exclusive serial
owner; a callback's ACCEPTED result is an assertion, not physical evidence.
Queued capability records and compact references never authorize dispatch.
"""

from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import Enum
import hashlib
import secrets

from ..contracts.carrier import CarrierProfile
from ..contracts.errors import ContractError, integer, octets, seconds
from ..contracts.identity import DeadlineSet, FlowKey, PeerID, RouteBinding
from ..contracts.negotiation import CommitOutcome, NegotiatedContract
from .budget import Cost, Ledger
from .flow_attempt import (CommitRequest, FlowAttemptPort, FlowDispatchLease,
                           Reservation as FlowReservation, profile_cost)


MAX_US = (1 << 63) - 1


class FrameRole(str, Enum):
    DATA = "data"
    CONTROL = "control"


class QueueClass(str, Enum):
    CONTROL = "control"
    INTERACTIVE = "interactive"
    BULK = "bulk"


class LeaseScope(str, Enum):
    SERVICE = "service"
    BROKER = "broker"


def _nonce(value):
    octets(value, 16, "invalid_scheduler_nonce")
    if not any(value):
        raise ContractError("invalid_scheduler_nonce")


@dataclass(frozen=True)
class SchedulerPolicy:
    quantum_us: int = 1000000
    max_frame_cost_us: int = 60000000
    max_flows: int = 8
    max_flows_per_peer: int = 2
    max_registrations: int = 256
    max_queued_frames: int = 256
    max_queued_bytes: int = 1048576
    max_peer_queued_bytes: int = 262144
    max_flow_queued_frames: int = 64
    max_control_queued_frames: int = 16
    max_control_queued_bytes: int = 4096
    control_rate_us_per_second: int = 100000
    control_burst_us: int = 2000000

    def __post_init__(self):
        bounds = {"quantum_us": (1, 60000000), "max_frame_cost_us": (1, 120000000),
                  "max_flows": (1, 8), "max_flows_per_peer": (1, 2),
                  "max_registrations": (1, 4096), "max_queued_frames": (1, 1024),
                  "max_queued_bytes": (1, 4194304), "max_peer_queued_bytes": (1, 1048576),
                  "max_flow_queued_frames": (1, 256), "max_control_queued_frames": (1, 64),
                  "max_control_queued_bytes": (1, 65536),
                  "control_rate_us_per_second": (1, 1000000),
                  "control_burst_us": (1, 120000000)}
        for field, (low, high) in bounds.items():
            integer(getattr(self, field), low, high, "invalid_scheduler_policy")
        if (self.max_frame_cost_us > 128 * self.quantum_us
                or self.max_flows_per_peer > self.max_flows
                or self.max_flows > self.max_registrations
                or self.max_control_queued_frames > self.max_queued_frames
                or self.max_control_queued_bytes > self.max_queued_bytes
                or self.max_peer_queued_bytes > self.max_queued_bytes):
            raise ContractError("inconsistent_scheduler_policy")


@dataclass(frozen=True)
class DispatchRequest:
    scheduler_epoch: bytes
    broker_epoch: bytes
    nonce: bytes
    flow: FlowKey
    route: RouteBinding
    frame_id: int
    payload_digest: bytes
    payload_bytes: int
    role: FrameRole
    queue_class: QueueClass
    profile: CarrierProfile
    cost: Cost
    deadlines: DeadlineSet
    flow_reservation: FlowReservation | None = None

    def __post_init__(self):
        for value in (self.scheduler_epoch, self.broker_epoch, self.nonce):
            _nonce(value)
        if (type(self.flow) is not FlowKey or type(self.route) is not RouteBinding
                or self.flow.session != self.route.session or type(self.role) is not FrameRole
                or type(self.queue_class) is not QueueClass
                or type(self.profile) is not CarrierProfile or type(self.cost) is not Cost
                or type(self.deadlines) is not DeadlineSet
                or self.profile.profile_digest != self.route.profile_digest
                or self.cost != profile_cost(self.profile)):
            raise ContractError("invalid_dispatch_request")
        integer(self.frame_id, 1, (1 << 64) - 1, "invalid_dispatch_request")
        integer(self.payload_bytes, 1, self.profile.payload_capacity, "invalid_dispatch_request")
        octets(self.payload_digest, 32, "invalid_dispatch_request")
        expected = (QueueClass.CONTROL if self.role is FrameRole.CONTROL else
                    QueueClass.BULK if self.route.service.kind.value == "knowledge" else
                    QueueClass.INTERACTIVE)
        if self.queue_class is not expected:
            raise ContractError("invalid_dispatch_class")
        if self.flow_reservation is not None:
            if type(self.flow_reservation) is not FlowReservation:
                raise ContractError("invalid_dispatch_flow_reservation")
            binding = self.flow_reservation.binding
            if (binding.flow != self.flow or binding.route != self.route
                    or binding.profile != self.profile or binding.cost != self.cost
                    or binding.payload_digest != self.payload_digest
                    or binding.payload_bytes != self.payload_bytes
                    or binding.contract.deadlines != self.deadlines):
                raise ContractError("invalid_dispatch_flow_reservation")


@dataclass(frozen=True)
class DispatchGrant:
    """Inert host result; only the trusted callback's held lease has authority."""
    request: DispatchRequest
    scope: LeaseScope
    deadlines: DeadlineSet
    revision: int
    broker_epoch: bytes

    def __post_init__(self):
        if (type(self.request) is not DispatchRequest or type(self.scope) is not LeaseScope
                or type(self.deadlines) is not DeadlineSet):
            raise ContractError("invalid_dispatch_grant")
        integer(self.revision, 1, (1 << 64) - 1, "invalid_dispatch_grant")
        _nonce(self.broker_epoch)


@dataclass(frozen=True, repr=False)
class DispatchFrame:
    request: DispatchRequest
    payload: bytes

    def __post_init__(self):
        if (type(self.request) is not DispatchRequest or type(self.payload) is not bytes
                or len(self.payload) != self.request.payload_bytes
                or hashlib.sha256(self.payload).digest() != self.request.payload_digest):
            raise ContractError("invalid_dispatch_frame")


@dataclass(frozen=True)
class DispatchResult:
    outcome: CommitOutcome | None
    reason: str
    frame_id: int | None = None
    invoked: bool = False
    post_send_checks_passed: bool | None = None
    flow_committed: bool = False
    flow_fenced: bool = False


@dataclass(repr=False)
class _Frame:
    frame_id: int
    payload: bytes
    role: FrameRole
    queue_class: QueueClass
    flow_reservation: FlowReservation | None = None


@dataclass(repr=False)
class _Flow:
    contract: NegotiatedContract
    profile: CarrierProfile
    peer: PeerID
    cost: Cost
    attempt_owner: FlowAttemptPort | None = None
    owner_id: bytes | None = None
    fenced: bool = False


class _HeldLease(AbstractContextManager):
    """A policy read lease cannot suppress a failed dispatch check."""

    def __init__(self, lease):
        self._lease = lease

    def __enter__(self):
        return self._lease.__enter__()

    def __exit__(self, kind, value, traceback):
        result = self._lease.__exit__(kind, value, traceback)
        if result is not None and result is not False:
            raise ContractError("invalid_dispatch_lease_exit")
        return False


class Scheduler:
    """One host event-loop owner. Every attempted dispatch gets fresh leases.

    Class airtime DRR weights are control:interactive:bulk = 1:4:1. Peers
    share each class by airtime DRR; flows rotate within each peer. Idle
    classes/peers lose saved deficits. There is no automatic retry or live
    session restoration. Callback reentry is refused before queue mutation.
    """
    _classes = (QueueClass.CONTROL, QueueClass.INTERACTIVE, QueueClass.BULK)
    _weights = {QueueClass.CONTROL: 1, QueueClass.INTERACTIVE: 4, QueueClass.BULK: 1}

    def __init__(self, *, local_peer, ledger, epoch, broker_epoch, policy=SchedulerPolicy()):
        if (type(local_peer) is not PeerID or type(ledger) is not Ledger
                or type(policy) is not SchedulerPolicy):
            raise ContractError("invalid_scheduler_context")
        _nonce(epoch)
        _nonce(broker_epoch)
        self.local_peer, self.ledger, self.epoch = local_peer, ledger, epoch
        self.broker_epoch, self.policy = broker_epoch, policy
        self._flows, self._queues, self._retired = {}, {}, set()
        # Bounded by max_registrations. Unknown owner finalization retains the
        # exact handle and port; a new epoch is not permission to repeat it.
        self._unresolved = {}
        self._next_id, self._last_us, self._last_epoch = 1, None, None
        self._busy = False
        self._closed = False
        self._class_index, self._class_active = 0, False
        self._class_deficit = {c: 0 for c in self._classes}
        self._peers = {c: deque() for c in self._classes}
        self._peer_active, self._peer_deficit, self._flow_order = {}, {}, {}
        self._control_tokens = policy.control_burst_us
        self._control_last_us, self._control_remainder = None, 0
        self._events = dict(enqueued=0, dropped=0, expired=0, refused=0, accepted=0, unknown=0)

    def _guard(self, *, allow_closed=False):
        if self._busy:
            raise ContractError("scheduler_reentry")
        if self._closed and not allow_closed:
            raise ContractError("scheduler_closed")

    def _time(self, now_us, now_epoch):
        integer(now_us, 0, MAX_US, "invalid_scheduler_clock")
        seconds(now_epoch, minimum=1, code="invalid_scheduler_epoch_clock")
        if ((self._last_us is not None and now_us < self._last_us)
                or (self._last_epoch is not None and now_epoch < self._last_epoch)):
            raise ContractError("scheduler_clock_regressed")
        self._last_us, self._last_epoch = now_us, now_epoch
        if self._control_last_us is not None:
            whole, self._control_remainder = divmod(
                (now_us - self._control_last_us) * self.policy.control_rate_us_per_second
                + self._control_remainder, 1000000)
            self._control_tokens = min(self.policy.control_burst_us, self._control_tokens + whole)
        self._control_last_us = now_us
        return now_us, now_epoch

    def _check(self, flow, now_us, now_epoch, deadlines=None):
        record = self._flows[flow]
        record.contract.deadlines.check(now_us / 1000000, now_epoch)
        record.profile.check_fresh(now_us / 1000000)
        if deadlines is not None:
            deadlines.check(now_us / 1000000, now_epoch)

    def open_flow(self, flow, contract, profile, *, now_us, now_epoch, attempt_owner=None):
        self._guard()
        self._time(now_us, now_epoch)
        if (type(flow) is not FlowKey or type(contract) is not NegotiatedContract
                or type(profile) is not CarrierProfile or flow.session != contract.route.session
                or profile.profile_digest != contract.route.profile_digest
                or self.local_peer not in (flow.session.initiator, flow.session.responder)):
            raise ContractError("invalid_scheduler_flow")
        if flow in self._flows or flow in self._retired:
            raise ContractError("scheduler_flow_reused")
        if len(self._flows) + len(self._retired) >= self.policy.max_registrations:
            raise ContractError("scheduler_registration_exhausted")
        peer = (flow.session.responder if self.local_peer == flow.session.initiator
                else flow.session.initiator)
        peers = [f for f in self._flows.values() if f.peer == peer]
        global_limit = min(self.policy.max_flows, contract.limits.max_flows,
                           *(f.contract.limits.max_flows for f in self._flows.values()))
        peer_limit = min(self.policy.max_flows_per_peer, contract.limits.max_flows_per_peer,
                         *(f.contract.limits.max_flows_per_peer for f in peers))
        if len(self._flows) >= global_limit or len(peers) >= peer_limit:
            raise ContractError("scheduler_flow_limit")
        cost = profile_cost(profile)
        if cost.shared_medium_us > self.policy.max_frame_cost_us:
            raise ContractError("scheduler_frame_cost_limit")
        contract.deadlines.check(now_us / 1000000, now_epoch)
        profile.check_fresh(now_us / 1000000)
        owner_id = None
        if attempt_owner is not None:
            if not isinstance(attempt_owner, FlowAttemptPort):
                raise ContractError("invalid_flow_attempt_owner")
            # Registration is a host callback too: prevent queue reentry.
            self._busy = True
            try:
                owner_id = attempt_owner.owner_id
                _nonce(owner_id)
            finally:
                self._busy = False
        self._flows[flow] = _Flow(contract, profile, peer, cost, attempt_owner, owner_id)

    def _counts(self, *, flow=None, peer=None, control=False):
        frames, size = 0, 0
        for (key, cls), queue in self._queues.items():
            if ((flow is None or key == flow) and (peer is None or self._flows[key].peer == peer)
                    and (not control or cls is QueueClass.CONTROL)):
                frames += len(queue)
                size += sum(len(frame.payload) for frame in queue)
        return frames, size

    def enqueue(self, flow, payload, *, role=FrameRole.DATA, now_us, now_epoch):
        self._guard()
        self._time(now_us, now_epoch)
        if type(flow) is FlowKey and flow in self._flows and self._flows[flow].attempt_owner is not None:
            raise ContractError("flow_owned_enqueue_required")
        return self._enqueue(flow, payload, role=role, now_us=now_us, now_epoch=now_epoch)

    def _enqueue(self, flow, payload, *, role, now_us, now_epoch, flow_reservation=None):
        if type(flow) is not FlowKey or flow not in self._flows:
            raise ContractError("scheduler_flow_unavailable")
        if type(role) is not FrameRole or type(payload) is not bytes:
            raise ContractError("invalid_scheduler_frame")
        record, policy = self._flows[flow], self.policy
        if record.fenced:
            raise ContractError("scheduler_flow_fenced")
        self._check(flow, now_us, now_epoch)
        if not 1 <= len(payload) <= min(record.contract.limits.max_payload_bytes,
                                       record.profile.payload_capacity):
            raise ContractError("scheduler_payload_limit")
        cls = (QueueClass.CONTROL if role is FrameRole.CONTROL else
               QueueClass(record.contract.traffic_class.value))
        if cls is QueueClass.CONTROL and len(payload) > record.contract.limits.max_control_bytes:
            raise ContractError("scheduler_control_payload_limit")
        if cls is QueueClass.CONTROL and record.cost.shared_medium_us > policy.control_burst_us:
            raise ContractError("scheduler_control_cost_limit")
        total_frames, total_bytes = self._counts()
        flow_frames, flow_bytes = self._counts(flow=flow)
        _, peer_bytes = self._counts(peer=record.peer)
        control_frames, control_bytes = self._counts(control=True)
        if (total_frames >= policy.max_queued_frames
                or total_bytes + len(payload) > policy.max_queued_bytes
                or flow_frames >= policy.max_flow_queued_frames
                or flow_bytes + len(payload) > record.contract.limits.max_queued_bytes
                or peer_bytes + len(payload) > policy.max_peer_queued_bytes
                or (cls is QueueClass.CONTROL and
                    (control_frames >= policy.max_control_queued_frames
                     or control_bytes + len(payload) > policy.max_control_queued_bytes))):
            raise ContractError("scheduler_queue_limit")
        if self._next_id > (1 << 64) - 1:
            raise ContractError("scheduler_frame_id_exhausted")
        frame = _Frame(self._next_id, payload, role, cls, flow_reservation)
        self._next_id += 1
        queue = self._queues.setdefault((flow, cls), deque())
        queue.append(frame)
        peer_key = (cls, record.peer)
        if record.peer not in self._peers[cls]:
            self._peers[cls].append(record.peer)
            self._peer_active[peer_key], self._peer_deficit[peer_key] = False, 0
        order = self._flow_order.setdefault(peer_key, deque())
        if flow not in order:
            order.append(flow)
        self._events["enqueued"] += 1
        return frame.frame_id

    def _owner_current(self, record):
        if record.attempt_owner.owner_id != record.owner_id:
            raise ContractError("flow_attempt_owner_changed")

    def _binding(self, flow, handle, now_us, now_epoch, deadlines=None):
        record = self._flows[flow]
        if record.fenced:
            raise ContractError("scheduler_flow_fenced")
        if type(handle) is not FlowReservation or handle.owner_id != record.owner_id:
            raise ContractError("flow_attempt_handle_refused")
        handle.__post_init__()
        if handle.reserved_at_us > now_us:
            raise ContractError("flow_attempt_reserved_in_future")
        binding = handle.binding
        binding.__post_init__()
        if (binding.flow != flow or binding.route != record.contract.route
                or binding.contract != record.contract or binding.profile != record.profile
                or binding.cost != record.cost):
            raise ContractError("flow_attempt_binding_refused")
        binding.check(now_us=now_us, now_epoch=now_epoch, effective_deadlines=deadlines)
        return binding

    def _fence(self, flow, handle):
        record = self._flows[flow]
        record.fenced = True
        if type(handle) is FlowReservation:
            self._unresolved[flow] = (record.attempt_owner, handle)

    def _reconcile_unsent(self, flow, handle):
        """One completion call, even for a known cancelled/expired handle.

        This does not validate sending. Any ambiguity fences and retains the
        handle; neither close nor a second callback can retry finalization.
        """
        record = self._flows[flow]
        if record.fenced:
            return False
        try:
            self._owner_current(record)
            lease = record.attempt_owner.dispatch_lease(handle)
            if not isinstance(lease, FlowDispatchLease):
                raise ContractError("flow_attempt_lease_refused")
            with _HeldLease(lease) as held:
                if held is not lease:
                    raise ContractError("flow_attempt_lease_refused")
                request = CommitRequest(handle, CommitOutcome.REFUSED_SCHEDULER,
                                        self._last_us, handle.binding.contract.deadlines)
                if held.commit(request) is not None:
                    raise ContractError("flow_attempt_commit_refused")
            return True
        except Exception:
            self._fence(flow, handle)
            return False
        except BaseException:
            self._fence(flow, handle)
            raise

    def enqueue_from_flow(self, flow, *, now_us, now_epoch, role=FrameRole.DATA):
        """Atomically prepare one owner attempt; no credit or retry allocation here."""
        self._guard()
        self._time(now_us, now_epoch)
        if type(flow) is not FlowKey or flow not in self._flows:
            raise ContractError("scheduler_flow_unavailable")
        record = self._flows[flow]
        if record.attempt_owner is None:
            raise ContractError("flow_attempt_owner_required")
        if record.fenced:
            raise ContractError("scheduler_flow_fenced")
        if self._counts(flow=flow)[0]:
            raise ContractError("flow_attempt_already_queued")
        if type(role) is not FrameRole:
            raise ContractError("invalid_scheduler_frame")
        self._check(flow, now_us, now_epoch)
        handle = None
        self._busy = True
        try:
            self._owner_current(record)
            handle = record.attempt_owner.prepare(flow, now_us=now_us, now_epoch=now_epoch)
            if handle is None:
                return None
            binding = self._binding(flow, handle, now_us, now_epoch)
            return self._enqueue(flow, binding.payload, role=role, now_us=now_us,
                                 now_epoch=now_epoch, flow_reservation=handle)
        except Exception:
            if type(handle) is FlowReservation:
                self._reconcile_unsent(flow, handle)
            else:
                # prepare may have allocated before failing. No safe second
                # prepare, inferred refund or synthesized handle is possible.
                self._fence(flow, handle)
            raise
        except BaseException:
            self._fence(flow, handle)
            raise
        finally:
            self._busy = False

    def _clean(self, flow, cls):
        queue = self._queues.get((flow, cls))
        if queue:
            return
        self._queues.pop((flow, cls), None)
        peer = self._flows[flow].peer
        key = (cls, peer)
        order = self._flow_order.get(key, deque())
        if flow in order:
            order.remove(flow)
        if not order:
            self._flow_order.pop(key, None)
            self._peer_active.pop(key, None)
            self._peer_deficit.pop(key, None)
            if peer in self._peers[cls]:
                self._peers[cls].remove(peer)
        if not self._peers[cls]:
            self._class_deficit[cls] = 0

    def _pop(self, flow, cls):
        frame = self._queues[(flow, cls)].popleft()
        self._clean(flow, cls)
        return frame

    def close_flow(self, flow):
        self._guard()
        if type(flow) is not FlowKey or flow not in self._flows:
            raise ContractError("scheduler_flow_unavailable")
        count = 0
        self._busy = True
        try:
            for cls in self._classes:
                queue = self._queues.get((flow, cls), ())
                for frame in queue:
                    if frame.flow_reservation is not None:
                        self._reconcile_unsent(flow, frame.flow_reservation)
                count += len(queue)
                self._queues.pop((flow, cls), None)
                self._clean(flow, cls)
        finally:
            self._busy = False
        del self._flows[flow]
        self._retired.add(flow)
        self._events["dropped"] += count
        return count

    def close(self):
        """Retire all host queues; this is not a carrier drain receipt."""
        self._guard(allow_closed=True)
        if self._closed:
            return 0
        count = sum(self.close_flow(flow) for flow in list(self._flows))
        self._closed = True
        return count

    def _expire(self, now_us, now_epoch):
        for flow in list(self._flows):
            try:
                self._check(flow, now_us, now_epoch)
            except ContractError:
                count = self.close_flow(flow)
                self._events["expired"] += count

    def _advance_class(self):
        self._class_index = (self._class_index + 1) % len(self._classes)
        self._class_active = False

    def _choose(self):
        """Bounded DRR scan. No budget or authority is consumed here."""
        p = self.policy
        rounds = len(self._classes) * (p.max_frame_cost_us // p.quantum_us + 2)
        for _ in range(rounds):
            cls = self._classes[self._class_index]
            if not self._peers[cls]:
                self._class_deficit[cls] = 0
                self._advance_class()
                continue
            if not self._class_active:
                self._class_deficit[cls] = min(p.max_frame_cost_us + p.quantum_us * self._weights[cls],
                                              self._class_deficit[cls] + p.quantum_us * self._weights[cls])
                self._class_active = True
            peers = self._peers[cls]
            for _ in range(len(peers) * (p.max_frame_cost_us // p.quantum_us + 2)):
                peer = peers[0]
                key = (cls, peer)
                order = self._flow_order[key]
                if not self._peer_active[key]:
                    self._peer_deficit[key] = min(p.max_frame_cost_us + p.quantum_us,
                                                  self._peer_deficit[key] + p.quantum_us)
                    self._peer_active[key] = True
                # Keep a costly head flow in place while its peer earns
                # deficit. Skipping it for a cheap sibling would starve it.
                flow = order[0]
                cost = self._flows[flow].cost.shared_medium_us
                if (cost <= self._class_deficit[cls] and cost <= self._peer_deficit[key]
                        and (cls is not QueueClass.CONTROL or cost <= self._control_tokens)):
                    return flow, self._queues[(flow, cls)][0]
                # A peer's remainder persists; a new round adds one quantum.
                self._peer_active[key] = False
                peers.rotate(-1)
                if not any(self._flows[self._flow_order[(cls, peer)][0]].cost.shared_medium_us
                           <= self._class_deficit[cls] for peer in peers):
                    break
            self._advance_class()
        return None

    def _rotate_flow(self, flow, cls):
        peer = self._flows[flow].peer
        order = self._flow_order.get((cls, peer))
        if order and flow in order:
            while order[0] != flow:
                order.rotate(-1)
            order.rotate(-1)

    def _charge_fairness(self, flow, frame):
        cost = self._flows[flow].cost.shared_medium_us
        cls, peer = frame.queue_class, self._flows[flow].peer
        self._class_deficit[cls] -= cost
        self._peer_deficit[(cls, peer)] -= cost
        if cls is QueueClass.CONTROL:
            self._control_tokens -= cost

    def _yield_peer(self, cls, peer):
        peers = self._peers[cls]
        if peer in peers:
            while peers[0] != peer:
                peers.rotate(-1)
            peers.rotate(-1)
            self._peer_active[(cls, peer)] = False

    def _grant(self, grant, request, scope):
        if type(grant) is not DispatchGrant:
            raise ContractError("dispatch_authority_refused")
        DispatchGrant(grant.request, grant.scope, grant.deadlines, grant.revision, grant.broker_epoch)
        if (grant.request != request
                or grant.request.flow_reservation is not request.flow_reservation
                or grant.scope is not scope or grant.broker_epoch != self.broker_epoch
                or grant.deadlines != request.deadlines.tightened(grant.deadlines)):
            raise ContractError("dispatch_authority_refused")
        return grant.deadlines

    def _validate_owned(self, flow, handle, lease, now_us, now_epoch, deadlines):
        self._owner_current(self._flows[flow])
        binding = self._binding(flow, handle, now_us, now_epoch, deadlines)
        if lease.validate(handle, now_us=now_us, now_epoch=now_epoch) is not binding:
            raise ContractError("flow_attempt_current_binding_refused")
        # A host callback can change state. Never rely only on its earlier
        # inert return; its held lease must serialize lifecycle writes.
        self._owner_current(self._flows[flow])
        self._binding(flow, handle, now_us, now_epoch, deadlines)

    def _dispatch_owned(self, flow, frame, *, authorize, broker, send,
                        monotonic_us, epoch_clock):
        """One live owner handle, one carrier call and one completion attempt.

        Airtime settlement and owner completion are distinct irreversible
        operations. Ambiguity fences the flow and retains the handle; it never
        triggers a second commit or an inferred refund. Policy lease exits
        precede accounting while the flow lease remains held throughout.
        """
        record, handle = self._flows[flow], frame.flow_reservation
        try:
            if record.fenced:
                raise ContractError("scheduler_flow_fenced")
            request = DispatchRequest(self.epoch, self.broker_epoch, secrets.token_bytes(16), flow,
                                      record.contract.route, frame.frame_id,
                                      hashlib.sha256(frame.payload).digest(), len(frame.payload),
                                      frame.role, frame.queue_class, record.profile, record.cost,
                                      record.contract.deadlines, handle)
        except Exception:
            self._fence(flow, handle)
            self._pop(flow, frame.queue_class)
            self._yield_peer(frame.queue_class, record.peer)
            self._advance_class()
            self._events["refused"] += 1
            return DispatchResult(CommitOutcome.REFUSED_SCHEDULER, "flow_attempt_binding_refused",
                                  frame.frame_id, flow_fenced=True)
        reservation, invoked = None, False
        settlement_started, commit_started, committed = False, False, False
        outcome, reason = CommitOutcome.REFUSED_SCHEDULER, "flow_attempt_lease_refused"
        deadlines, guards_ok, valid_after = request.deadlines, False, False
        self._busy = True
        try:
            self._owner_current(record)
            lease = record.attempt_owner.dispatch_lease(handle)
            if not isinstance(lease, FlowDispatchLease):
                raise ContractError("flow_attempt_lease_refused")
            with _HeldLease(lease) as held:
                if held is not lease:
                    raise ContractError("flow_attempt_lease_refused")
                try:
                    now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                    self._validate_owned(flow, handle, held, now_us, now_epoch, deadlines)
                    reason = "service_authority_refused"
                    service_lease = authorize(request)
                    if not isinstance(service_lease, AbstractContextManager):
                        raise ContractError("dispatch_authority_refused")
                    with _HeldLease(service_lease) as service_grant:
                        deadlines = deadlines.tightened(self._grant(service_grant, request, LeaseScope.SERVICE))
                        now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                        self._check(flow, now_us, now_epoch, deadlines)
                        self._validate_owned(flow, handle, held, now_us, now_epoch, deadlines)
                        reason = "broker_authority_refused"
                        broker_lease = broker(request)
                        if not isinstance(broker_lease, AbstractContextManager):
                            raise ContractError("dispatch_authority_refused")
                        with _HeldLease(broker_lease) as broker_grant:
                            deadlines = deadlines.tightened(self._grant(broker_grant, request, LeaseScope.BROKER))
                            now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                            self._check(flow, now_us, now_epoch, deadlines)
                            self._validate_owned(flow, handle, held, now_us, now_epoch, deadlines)
                            reason = "dispatch_budget_refused"
                            reservation = self.ledger.reserve(record.cost, now_us=now_us)
                            reason = "dispatch_deadline_refused"
                            now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                            self._check(flow, now_us, now_epoch, deadlines)
                            self._validate_owned(flow, handle, held, now_us, now_epoch, deadlines)
                            # validate is a host callback and may consume time.
                            # Its held lease serializes flow state; repeat pure
                            # clock/deadline/profile checks after it returns.
                            now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                            self._check(flow, now_us, now_epoch, deadlines)
                            self._binding(flow, handle, now_us, now_epoch, deadlines)
                            self.ledger.validate(reservation, now_us=now_us)
                            invoked = True
                            outcome, reason = CommitOutcome.UNKNOWN, "carrier_outcome_unknown"
                            try:
                                result = send(DispatchFrame(request, frame.payload))
                                if type(result) is CommitOutcome and result in (
                                        CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN,
                                        CommitOutcome.REFUSED_CARRIER):
                                    outcome = result
                                    reason = "carrier_accepted" if result is CommitOutcome.ACCEPTED else result.value
                            except Exception:
                                pass
                            now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                            self._check(flow, now_us, now_epoch, deadlines)
                    now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                    self._check(flow, now_us, now_epoch, deadlines)
                    guards_ok = True
                except Exception:
                    if invoked:
                        reason = "post_send_guard_failed"
                finally:
                    try:
                        if reservation is not None:
                            settlement_started = True
                            self.ledger.settle(reservation, outcome if invoked else
                                               CommitOutcome.REFUSED_SCHEDULER, now_us=self._last_us)
                    except Exception:
                        self._fence(flow, handle)
                        reason = "airtime_settlement_uncertain"
                    finally:
                        commit_started = True
                        try:
                            completion = CommitRequest(handle, outcome if invoked else
                                                       CommitOutcome.REFUSED_SCHEDULER,
                                                       self._last_us, deadlines)
                            if held.commit(completion) is not None:
                                raise ContractError("flow_attempt_commit_refused")
                            committed = True
                        except Exception:
                            self._fence(flow, handle)
                            reason = "flow_attempt_commit_uncertain"
            now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
            self._check(flow, now_us, now_epoch, deadlines)
            valid_after = guards_ok and not record.fenced
        except Exception:
            self._fence(flow, handle)
            reason = "flow_attempt_lease_uncertain" if commit_started else "flow_attempt_lease_refused"
        except BaseException:
            # Cancellation/interrupt during lease exit is still ambiguous
            # even if commit already returned. Preserve propagation while
            # stopping this flow; completion and charge are never repeated.
            self._fence(flow, handle)
            raise
        finally:
            try:
                if reservation is not None and not settlement_started:
                    settlement_started = True
                    self.ledger.settle(reservation, outcome if invoked else
                                       CommitOutcome.REFUSED_SCHEDULER, now_us=self._last_us)
                if not committed:
                    self._fence(flow, handle)
                if invoked and outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN):
                    self._charge_fairness(flow, frame)
                self._rotate_flow(flow, frame.queue_class)
                self._pop(flow, frame.queue_class)
                if outcome in (CommitOutcome.REFUSED_SCHEDULER, CommitOutcome.REFUSED_CARRIER):
                    self._yield_peer(frame.queue_class, record.peer)
                    self._advance_class()
                self._events["accepted" if outcome is CommitOutcome.ACCEPTED else
                             "unknown" if outcome is CommitOutcome.UNKNOWN else "refused"] += 1
            finally:
                self._busy = False
        return DispatchResult(outcome, reason, frame.frame_id, invoked,
                              valid_after if invoked else None, committed, record.fenced)

    def dispatch_one(self, *, authorize=None, broker=None, send=None,
                     monotonic_us=None, epoch_clock=None):
        """Exactly one carrier invocation, under both fresh host read leases.

        send(DispatchFrame) returns ACCEPTED, REFUSED_CARRIER or UNKNOWN.
        Exceptions/invalid results after invoking it are UNKNOWN and charged.
        Pre-send budget pressure retains the queued frame; authority refusals
        drop it. A returned outcome never restores live service permission.
        """
        self._guard()
        if not all(callable(x) for x in (authorize, broker, send, monotonic_us, epoch_clock)):
            return DispatchResult(CommitOutcome.REFUSED_SCHEDULER, "missing_dispatch_adapter")
        try:
            now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
            self._expire(now_us, now_epoch)
        except Exception:
            return DispatchResult(CommitOutcome.REFUSED_SCHEDULER, "dispatch_clock_refused")
        chosen = self._choose()
        if chosen is None:
            return DispatchResult(None, "no_eligible_frame")
        flow, frame = chosen
        record = self._flows[flow]
        if frame.flow_reservation is not None:
            return self._dispatch_owned(flow, frame, authorize=authorize, broker=broker,
                                        send=send, monotonic_us=monotonic_us,
                                        epoch_clock=epoch_clock)
        request = DispatchRequest(self.epoch, self.broker_epoch, secrets.token_bytes(16), flow,
                                  record.contract.route, frame.frame_id,
                                  hashlib.sha256(frame.payload).digest(), len(frame.payload),
                                  frame.role, frame.queue_class, record.profile, record.cost,
                                  record.contract.deadlines)
        reservation, invoked, keep = None, False, False
        outcome, reason, valid_after = CommitOutcome.REFUSED_SCHEDULER, "service_authority_refused", False
        deadlines = request.deadlines
        self._busy = True
        try:
            service_lease = authorize(request)
            if not isinstance(service_lease, AbstractContextManager):
                raise ContractError("dispatch_authority_refused")
            with _HeldLease(service_lease) as service_grant:
                deadlines = deadlines.tightened(self._grant(service_grant, request, LeaseScope.SERVICE))
                now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                self._check(flow, now_us, now_epoch, deadlines)
                reason = "broker_authority_refused"
                broker_lease = broker(request)
                if not isinstance(broker_lease, AbstractContextManager):
                    raise ContractError("dispatch_authority_refused")
                with _HeldLease(broker_lease) as broker_grant:
                    deadlines = deadlines.tightened(self._grant(broker_grant, request, LeaseScope.BROKER))
                    now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                    self._check(flow, now_us, now_epoch, deadlines)
                    reason = "dispatch_budget_refused"
                    try:
                        reservation = self.ledger.reserve(record.cost, now_us=now_us)
                    except ContractError:
                        keep = True
                        raise
                    reason = "dispatch_deadline_refused"
                    now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                    self._check(flow, now_us, now_epoch, deadlines)
                    self.ledger.validate(reservation, now_us=now_us)
                    invoked = True
                    outcome, reason = CommitOutcome.UNKNOWN, "carrier_outcome_unknown"
                    try:
                        carrier_result = send(DispatchFrame(request, frame.payload))
                        if type(carrier_result) is CommitOutcome and carrier_result in (
                                CommitOutcome.ACCEPTED, CommitOutcome.REFUSED_CARRIER, CommitOutcome.UNKNOWN):
                            outcome = carrier_result
                            reason = "carrier_accepted" if outcome is CommitOutcome.ACCEPTED else outcome.value
                    except Exception:
                        pass
                    now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
                    self._check(flow, now_us, now_epoch, deadlines)
            # Exit time and exceptions cannot undo a carrier outcome or charge.
            now_us, now_epoch = self._time(monotonic_us(), epoch_clock())
            self._check(flow, now_us, now_epoch, deadlines)
            valid_after = True
        except Exception:
            if invoked:
                reason = "post_send_guard_failed"
        finally:
            try:
                if reservation is not None:
                    self.ledger.settle(reservation, outcome if invoked else CommitOutcome.REFUSED_SCHEDULER,
                                       now_us=self._last_us)
                if invoked and outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN):
                    self._charge_fairness(flow, frame)
                self._rotate_flow(flow, frame.queue_class)
                if not keep:
                    self._pop(flow, frame.queue_class)
                if keep or outcome in (CommitOutcome.REFUSED_SCHEDULER, CommitOutcome.REFUSED_CARRIER):
                    self._yield_peer(frame.queue_class, record.peer)
                    self._advance_class()
                self._events["accepted" if outcome is CommitOutcome.ACCEPTED else
                             "unknown" if outcome is CommitOutcome.UNKNOWN else "refused"] += 1
            finally:
                self._busy = False
        return DispatchResult(outcome, reason, frame.frame_id, invoked,
                              valid_after if invoked else None)

    def snapshot(self):
        """Bounded aggregate diagnostics, excluding payloads and identities."""
        self._guard(allow_closed=True)
        frames, size = self._counts()
        control_frames, control_bytes = self._counts(control=True)
        return {"closed": self._closed, "flows": len(self._flows), "retired_flows": len(self._retired),
                "fenced_flows": sum(record.fenced for record in self._flows.values()),
                "unresolved_attempts": len(self._unresolved),
                "queued_frames": frames, "queued_bytes": size,
                "control_queued_frames": control_frames, "control_queued_bytes": control_bytes,
                "control_tokens_us": self._control_tokens,
                "classes": {c.value: self._class_deficit[c] for c in self._classes},
                "events": dict(self._events)}
