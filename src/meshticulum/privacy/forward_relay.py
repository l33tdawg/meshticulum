# SPDX-License-Identifier: Apache-2.0
"""One bounded opaque MPF2 exchange over already admitted adjacent carriers.

Descriptions are inert. Only actual caller-owned ServiceTransfer instances and
their existing application/dispatch leases may move bytes. This helper has no
endpoint signing/Noise keys, private opener, grant factory or native consent
provider. A consuming application must independently hold its original relay
owner and cumulative stage budget; closing/restarting this object cannot prove
that admission, retire a device, reconcile an unknown attempt or refund debt.

The five stages carry one initiator DATA record. Adjacent sessions must be fresh
and independently admitted. Their durable replay/attempt/byte journals remain
owned by the caller. Volatile stage ordering is not durable replay protection.
"""
from dataclasses import dataclass
from functools import wraps
import hashlib
import os
import threading

from ..bench.authority import RunAuthority
from ..bench.flow_runtime import BenchFlow
from ..bench.services import ServiceTransfer
from ..contracts.identity import DeadlineSet, PeerID, ServiceKind, SessionKey
from .forward_session import (ForwardContext, H1, H2, CONFIRM_I, CONFIRM_R,
                              DATA, HEADER, MAX_PACKET_BYTES, inspect_record)

SEQUENCE = (H1, H2, CONFIRM_I, CONFIRM_R, DATA)


class ForwardRelayError(ValueError):
    def __init__(self):
        super().__init__('forward_relay_unavailable')


def require(value):
    if not value:
        raise ForwardRelayError()


def _digest(value):
    return type(value) is bytes and len(value) == 32 and any(value)


def _peer(value):
    return PeerID(value.chain_id, value.public_key.hex())


@dataclass(frozen=True, slots=True)
class RelayDescription:
    """Public context and original local limits; never an authorization token."""
    context: ForwardContext
    relay_peer: PeerID
    deadlines: DeadlineSet
    max_packet_bytes: int = MAX_PACKET_BYTES

    def __post_init__(self):
        require(type(self.context) is ForwardContext and type(self.relay_peer) is PeerID
                and type(self.deadlines) is DeadlineSet
                and self.relay_peer not in (_peer(self.context.initiator), _peer(self.context.responder))
                and type(self.max_packet_bytes) is int
                and HEADER.size + 112 <= self.max_packet_bytes <= MAX_PACKET_BYTES
                and self.context.issued_epoch_us / 1e6 < self.deadlines.authority_expires_epoch
                <= self.context.expires_epoch_us / 1e6)


@dataclass(frozen=True, slots=True)
class RelayHop:
    """Exact adjacent carrier binding, with no keys or live permission."""
    carrier: str
    session: SessionKey
    route_binding_digest: bytes
    grant_digest: bytes

    def __post_init__(self):
        require(type(self.carrier) is str and self.carrier in ('radio', 'ip')
                and type(self.session) is SessionKey
                and _digest(self.route_binding_digest) and _digest(self.grant_digest))


@dataclass(frozen=True, slots=True)
class RelayStage:
    kind: int
    incoming: RelayHop
    outgoing: RelayHop
    packet_bytes: int
    packet_sha256: bytes

    def __post_init__(self):
        require(type(self.kind) is int and self.kind in SEQUENCE
                and type(self.incoming) is RelayHop and type(self.outgoing) is RelayHop
                and type(self.packet_bytes) is int and 77 <= self.packet_bytes <= MAX_PACKET_BYTES
                and _digest(self.packet_sha256)
                and self.incoming.session.session_id != self.outgoing.session.session_id
                and self.incoming.session.session_generation != self.outgoing.session.session_generation
                and self.incoming.grant_digest != self.outgoing.grant_digest)
        require((self.kind in (H1, H2) and self.packet_bytes == HEADER.size + 48)
                or (self.kind in (CONFIRM_I, CONFIRM_R) and self.packet_bytes == HEADER.size + 112)
                or (self.kind == DATA and self.packet_bytes > HEADER.size + 16 + 51))


def _hop(transfer):
    require(type(transfer) is ServiceTransfer and transfer.kind is ServiceKind.MESSAGE
            and transfer.format == 'MPF2.record' and not transfer.public32
            and transfer.mb1_descriptor_sha256 is None)
    flow, authority = transfer.flow, transfer.authority
    if type(flow) is BenchFlow:
        require(type(authority) is RunAuthority)
        carrier = 'radio'
    else:
        from ..ip.authority import IpAuthority
        from ..ip.flow import IpFlow
        require(type(flow) is IpFlow and type(authority) is IpAuthority)
        carrier = 'ip'
    require(flow.authority is authority and flow.cipher.grant is authority.grant
            and flow._service_adapter is transfer)
    grant = authority.grant
    return RelayHop(carrier, grant.session, grant.route_binding_digest, grant.digest)


def describe_stage(kind, incoming, outgoing):
    """Observe bindings only; native approval must precede carrier construction."""
    one, two = _hop(incoming), _hop(outgoing)
    require(incoming.descriptor == outgoing.descriptor)
    size, pin = incoming.descriptor
    require(type(pin) is str and len(pin) == 64)
    return RelayStage(kind, one, two, size, bytes.fromhex(pin))


@dataclass(frozen=True, slots=True, repr=False)
class RelayCloseDelegation:
    """One exact relay's local cleanup handoff, never a carrier permission.

    Only an instance retained by ``prepare_close_delegation`` is accepted.
    Constructing or copying this record grants no access.
    """
    _relay: object
    _owner: tuple
    _gate: object
    _methods: tuple

    def __repr__(self):
        return '<RelayCloseDelegation>'


def _cancel_binding(transfer):
    """Retain only exact local cancellation targets; no permission callback."""
    require(type(transfer) is ServiceTransfer)
    flow = transfer.flow
    objects = (transfer, flow, transfer.authority)
    targets = ((transfer, 'cancel'), (flow, 'close'))
    if type(flow) is BenchFlow:
        objects += (flow.owner, flow.scheduler, flow.link)
        targets += ((flow.owner, 'settle_sending'), (flow.owner, 'dispatch_lease'),
                    (flow.scheduler, 'close'))
    else:
        from ..ip.flow import IpFlow
        require(type(flow) is IpFlow)
        objects += (flow.flow,)
        targets += ((flow.flow, 'cancel'),)
    methods = tuple((target, name, type(target), getattr(type(target), name))
                    for target, name in targets)
    require(all(name not in vars(target) for target, name in targets))
    return objects, methods


def _operation(method):
    @wraps(method)
    def checked(self, *args, **kwargs):
        # Only an explicit idle cleanup delegation crosses the thread boundary.
        require(self._owner == (os.getpid(), threading.get_ident()))
        gate = self._gate
        require(gate is self._gate_ref and gate.acquire(blocking=False))
        entered = False
        try:
            require(not self._busy and not self._closed)
            self._busy = entered = True
            result = method(self, *args, **kwargs)
            require(not self._closed)
            return result
        except BaseException as error:
            self._uncertain = True
            self._retire()
            if not isinstance(error, Exception):
                raise
            raise ForwardRelayError() from None
        finally:
            if entered:
                self._busy = False
            gate.release()
    return checked


class OpaqueForwardRelay:
    """Borrow two held carriers per stage; buffer at most one opaque record.

``begin`` binds existing adapters before pumping. ``step`` performs one bounded
incoming/outgoing flow step and returns whether the outgoing sender has its
authenticated final ACK. ``finish_stage`` releases the borrowed references but
does not close devices, assert upstream final-ACK receipt or admit private data.
The owner must complete its own receive-ACK/retirement/disarm protocol.

The creator may prepare one cleanup delegation for a shutdown coordinator.
It grants only idle local cancellation in this process. The coordinator must
independently fence external carrier producers before consuming it; device,
authority and journal retirement remain the caller's responsibility.

Failure cancels local queues, fences this helper and preserves all carrier
journals. No automatic retry, replacement carrier or deadline renewal occurs.
    """
    def __init__(self, description, *, clock):
        require(type(description) is RelayDescription and callable(clock))
        self.description = self._description = description
        self.clock = self._clock = clock
        self._context = description.context.prologue
        self._limits = self._description_values()
        self._owner = (os.getpid(), threading.get_ident())
        self._gate = self._gate_ref = threading.RLock()
        self._close_delegation = None
        self._close_consumed = False
        self._last_clock = None
        self._busy = self._closed = self._uncertain = False
        self._position = 0
        self._active = self._packet = None
        self._bindings = None
        self._cancel_bindings = None
        self._seen_sessions = set()
        self._seen_generations = set()
        self._receipts = []
        self._live()

    def __repr__(self):
        return '<OpaqueForwardRelay>'

    def _description_values(self):
        value = self.description
        return (value.relay_peer.chain_id, value.relay_peer.agent_id,
                value.deadlines.application_deadline_monotonic,
                value.deadlines.authority_deadline_monotonic,
                value.deadlines.authority_expires_epoch, value.max_packet_bytes)

    def _description_current(self):
        require(not self._closed and not self._uncertain
                and self._owner == (os.getpid(), threading.get_ident())
                and self.description is self._description and self.clock is self._clock
                and self.description.context.prologue == self._context
                and self._description_values() == self._limits)

    def _live(self):
        self._description_current()
        now = self._clock()
        self._description_current()
        require(not self._closed and type(now) is tuple and len(now) == 2
                and all(type(value) is int and 0 <= value < 2**63 for value in now)
                and self.description.context.issued_epoch_us <= now[0]
                < self.description.context.expires_epoch_us
                and (self._last_clock is None
                     or all(a >= b for a, b in zip(now, self._last_clock))))
        self.description.deadlines.check(now[1] / 1e6, now[0] / 1e6)
        self._last_clock = now

    def _bound(self):
        self._live()
        require(self._active is not None)
        stage, incoming, outgoing = self._active
        require(self._bindings == (incoming, incoming.flow, incoming.authority,
                                  outgoing, outgoing.flow, outgoing.authority))
        require(_hop(incoming) == stage.incoming and _hop(outgoing) == stage.outgoing)
        context = self.description.context
        for transfer in (incoming, outgoing):
            transfer._check()
            authority = transfer.authority
            require(authority.local_peer == self.description.relay_peer
                    and authority.grant.issued_epoch_us == context.issued_epoch_us
                    and authority.application_expires_epoch_us == context.expires_epoch_us
                    and authority.deadlines == authority.deadlines.tightened(self.description.deadlines)
                    and transfer.descriptor == (stage.packet_bytes, stage.packet_sha256.hex()))
            self._live()
        require(incoming.flow.sends is False and outgoing.flow.sends is True)
        if self._packet is not None:
            self._packet_bound(stage, self._packet)
        return stage, incoming, outgoing

    def _packet_bound(self, stage, packet):
        require(type(packet) is bytes and len(packet) == stage.packet_bytes
                <= self.description.max_packet_bytes
                and hashlib.sha256(packet).digest() == stage.packet_sha256)
        kind, session_id, counter = inspect_record(packet)
        require(kind == stage.kind and session_id == self.description.context.session_id
                and counter == (0 if kind in (H1, H2) else 2 if kind == DATA else 1)
                and counter <= self.description.context.max_records)

    @_operation
    def begin(self, stage, incoming, outgoing):
        self._live()
        require(self._active is None and self._position < len(SEQUENCE)
                and type(stage) is RelayStage and stage.kind == SEQUENCE[self._position]
                and stage.packet_bytes <= self.description.max_packet_bytes
                and incoming is not outgoing)
        context = self.description.context
        source, target = ((_peer(context.initiator), _peer(context.responder))
                          if stage.kind in (H1, CONFIRM_I, DATA)
                          else (_peer(context.responder), _peer(context.initiator)))
        relay = self.description.relay_peer
        require((stage.incoming.session.initiator, stage.incoming.session.responder) == (source, relay)
                and (stage.outgoing.session.initiator, stage.outgoing.session.responder) == (relay, target)
                and type(incoming) is ServiceTransfer and type(outgoing) is ServiceTransfer
                and not outgoing.started and not outgoing.flow.flow.queued_bytes())
        sessions = (stage.incoming.session, stage.outgoing.session)
        require(all(s.session_id not in self._seen_sessions
                    and s.session_generation not in self._seen_generations for s in sessions))
        self._active = stage, incoming, outgoing
        self._bindings = (incoming, incoming.flow, incoming.authority,
                          outgoing, outgoing.flow, outgoing.authority)
        self._cancel_bindings = tuple(_cancel_binding(t) for t in (incoming, outgoing))
        self._bound()
        self._seen_sessions.update(s.session_id for s in sessions)
        self._seen_generations.update(s.session_generation for s in sessions)

    @_operation
    def step(self):
        stage, incoming, outgoing = self._bound()
        # Keep the incoming endpoint available for its ordinary duplicate/final
        # ACK handling while the second carrier progresses. No synthetic ACK.
        if type(incoming.flow) is BenchFlow or not incoming.flow.flow.complete:
            incoming.flow.step()
        else:
            # The IP peer may close after its final receipt. A completed
            # receiver flushes ACK work without consuming a later EOF.
            incoming.flow.finish_receive_ack()
        self._bound()
        if self._packet is None and incoming.flow.flow.complete:
            packet = incoming.received()  # exact service descriptor + held lease
            self._bound()
            self._packet_bound(stage, packet)
            self._packet = packet
            outgoing.send(packet)  # held existing lease, queues exactly once
            self._bound()
        if self._packet is not None:
            outgoing.flow.step()
            self._bound()
        return self._packet is not None and outgoing.flow.flow.complete is True

    @_operation
    def finish_stage(self):
        stage, incoming, outgoing = self._bound()
        require(self._packet is not None and incoming.flow.flow.complete is True
                and outgoing.flow.flow.complete is True
                and not incoming.flow.flow.failure and not outgoing.flow.flow.failure)
        receipt = dict(kind=stage.kind, context_sha256=self.description.context.digest.hex(),
                       packet_bytes=stage.packet_bytes, packet_sha256=stage.packet_sha256.hex(),
                       incoming_grant_sha256=stage.incoming.grant_digest.hex(),
                       outgoing_grant_sha256=stage.outgoing.grant_digest.hex(),
                       outgoing_final_ack_observed=True, private_admission=False,
                       native_retirement_proved=False)
        self._receipts.append(receipt)
        self._position += 1
        self._active = self._bindings = self._packet = None
        self._cancel_bindings = None
        return dict(receipt)

    def prepare_close_delegation(self):
        """Creator-only, once, without clock/authority calls or ownership changes."""
        require(type(self) is OpaqueForwardRelay
                and self._owner == (os.getpid(), threading.get_ident()))
        gate = self._gate
        require(gate is self._gate_ref and gate.acquire(blocking=False))
        try:
            require(not self._busy and self._close_delegation is None)
            self._description_current()
            names = ('prepare_close_delegation', 'close_delegated', 'close',
                     '_cancellation_idle', '_cancel_owned', '_retire')
            require(all(name not in vars(self) for name in names))
            methods = tuple((name, getattr(type(self), name)) for name in names)
            token = RelayCloseDelegation(self, self._owner, gate, methods)
            self._close_delegation = token
            return token
        finally:
            gate.release()

    def _cancellation_idle(self):
        require(not self._busy)
        for objects, _ in self._cancel_bindings or ():
            _, flow, authority, *extra = objects
            require(not authority.held)
            if type(flow) is BenchFlow:
                owner, scheduler, link = extra
                require(not scheduler._busy and not owner._lease_open and not link._in_send)

    def _cancel_owned(self, binding):
        objects, methods = binding
        transfer, flow, authority, *extra = objects
        current_objects, current_methods = _cancel_binding(transfer)
        require(len(objects) == len(current_objects)
                and all(a is b for a, b in zip(objects, current_objects))
                and methods == current_methods and flow.authority is authority
                and not authority.held)
        if type(flow) is BenchFlow:
            owner, scheduler, link = extra
            require(not scheduler._busy and not owner._lease_open and not link._in_send)
            if scheduler._unresolved or any(row.fenced for row in scheduler._flows.values()):
                self._uncertain = True
        # This is the retained ServiceTransfer.cancel, not a caller replacement.
        methods[0][3](transfer)
        require(transfer.cancelled is True and flow.closed is True)
        if type(flow) is BenchFlow:
            require(owner.no_send and scheduler._closed and not scheduler._busy
                    and not scheduler._flows and not scheduler._queues and not scheduler._unresolved)
        else:
            require(flow.flow._fenced and not flow.flow.queued_bytes())

    def close_delegated(self, token):
        """Consume one same-process idle handoff; True proves local cleanup only.

        Busy/wrong/spent handoffs are rejected before consumption. Once cleanup
        is accepted, any failure consumes the token and leaves uncertainty
        latched. No live authority, clock, journal or device method is invoked.
        """
        require(type(self) is OpaqueForwardRelay and type(token) is RelayCloseDelegation
                and token is self._close_delegation and token._relay is self
                and token._owner == self._owner and os.getpid() == self._owner[0]
                and token._gate is self._gate is self._gate_ref)
        gate = token._gate
        require(gate.acquire(blocking=False))
        try:
            require(not self._close_consumed
                    and all(name not in vars(self) and getattr(type(self), name) is method
                            for name, method in token._methods))
            self._cancellation_idle()
            self._close_consumed = True
            self._retire()
            return not self._uncertain
        finally:
            gate.release()

    def _retire(self):
        self._closed = True
        active, self._active = self._active, None
        self._packet = self._bindings = None
        bindings, self._cancel_bindings = self._cancel_bindings, None
        if active is not None:
            if bindings is None or len(bindings) != 2:
                self._uncertain = True
                return
            for binding in bindings:
                try:
                    self._cancel_owned(binding)
                except BaseException:
                    self._uncertain = True

    def close(self):
        require(self._owner == (os.getpid(), threading.get_ident()))
        gate = self._gate
        require(gate is self._gate_ref and gate.acquire(blocking=False))
        try:
            self._cancellation_idle()
            self._retire()
            return not self._uncertain
        finally:
            gate.release()

    def snapshot(self):
        require(self._owner == (os.getpid(), threading.get_ident()))
        gate = self._gate
        require(gate is self._gate_ref and gate.acquire(blocking=False))
        try:
            return dict(schema='meshticulum.opaque-forward-relay.v1',
                        completed_stages=self._position, closed=self._closed,
                        uncertain=self._uncertain, queued_records=int(self._packet is not None),
                        queued_bytes=len(self._packet) if self._packet is not None else 0,
                        receipts=[dict(value) for value in self._receipts],
                        private_admission=False, native_retirement_proved=False,
                        durable_replay_proved=False, refunds=0)
        finally:
            gate.release()
