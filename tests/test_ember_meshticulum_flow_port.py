# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Acceptance controls for the finite MT05 owner bound to the frozen MT04 scheduler.

Covers controls C10, C11 and C12 of
`work/meshticulum-plan-audit-20261004/credit-contract-review/ACCEPTANCE.json` plus
the control-role and size-accounting clauses of `CONTROL_ROLE_ADDENDUM.json`.

Synthetic virtual clocks, a mocked carrier and a real frozen `Scheduler`/`Ledger`.
No radio, device, socket, broker, serial port or runtime import. A carrier
ACCEPTED result is an assertion by a mock, never physical evidence.

Run from the repository root:

    PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_meshticulum_flow_port.py -q
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]

from meshticulum.contracts.carrier import (  # noqa: E402
    CarrierKind, CarrierProfile, ProfileBasis)
from meshticulum.contracts.errors import ContractError  # noqa: E402
from meshticulum.contracts.identity import (  # noqa: E402
    DeadlineSet, FlowKey, PeerID, RouteBinding, ServiceID, ServiceKind, SessionKey)
from meshticulum.contracts.negotiation import (  # noqa: E402
    CapabilityLimits, CommitOutcome, Feature, NegotiatedContract, TrafficClass)
from meshticulum.scheduler.budget import (  # noqa: E402
    BudgetPolicy, Cost, CounterSample, Ledger)
from meshticulum.scheduler.flow_attempt import (  # noqa: E402
    AttemptBinding, CommitRequest, Reservation, profile_cost)
from meshticulum.scheduler.scheduler import (  # noqa: E402
    DispatchGrant, FrameRole, LeaseScope, Scheduler)
from meshticulum.flow.carrier import (  # noqa: E402
    LinkProfile, SimLink)
from meshticulum.flow.flow import (  # noqa: E402
    ACK_BYTES, DEFERRED, KIND_ACK, FlowError, FlowLimits, FlowState, ReliableFlow,
    StartupGrant, build_ack_frame, build_ack_from_wire, build_data_frame, parse_frame,
    remote_startup_credit)
from meshticulum.flow.flow_port import (  # noqa: E402
    CONTROL_KIND, DATA_KIND, FlowPortLink, LanePolicy, MeshticulumFlowOwner,
    admitted_frame_bound, frame_grant_from_bytes)
from meshticulum.flow.mux import run  # noqa: E402

# The MT05 quantum has to leave room for its own 12-byte frame encoding inside
# the admitted bound, which the fixture sets to 64 bytes.
QUANTUM = 48
NOW_US = 100_000_000
NOW_EPOCH = 2000


def contracts(*, flow_id=1, kind=ServiceKind.SAGE, max_payload_bytes=64,
              max_control_bytes=32, initial_credit_bytes=128, max_reassembly_frames=16,
              profile_payload_capacity=None, window_bytes=1024, max_unconsumed_bytes=1024):
    local = PeerID("fixture-home", "a" * 64)
    peer = PeerID("fixture-peer", "b" * 64)
    session = SessionKey(local, peer, b"i" * 16, b"g" * 16)
    flow = FlowKey(session, local, flow_id)
    mtu = profile_payload_capacity if profile_payload_capacity is not None else 128
    profile = CarrierProfile(CarrierKind.BINARY, mtu, 13, 16, bytes([flow_id]) * 32,
                             2, 1, 8, .75, .25, .5, 90, 1000, ProfileBasis.SOURCE_FIXTURE)
    route = RouteBinding(session, ServiceID(peer, kind),
                         "12345678-1234-4234-8234-123456789abc", b"c" * 32,
                         profile.profile_digest, 1)
    limits = CapabilityLimits(max_payload_bytes, window_bytes, max_unconsumed_bytes,
                              initial_credit_bytes,
                              max_reassembly_frames, 4096, 6, 8, 2, 65535,
                              max_control_bytes, 32)
    contract = NegotiatedContract(1, frozenset(Feature), limits, route,
                                  DeadlineSet(1000, 900, 2100),
                                  TrafficClass.BULK if kind is ServiceKind.KNOWLEDGE
                                  else TrafficClass.INTERACTIVE, 1, b"n" * 16)
    return local, peer, flow, profile, contract


class Port:
    """A real scheduler transaction seat for one flow, with the host callbacks."""

    def __init__(self, *, flow_id=1, kind=ServiceKind.SAGE, no_send=False, **contract_kwargs):
        self.local, self.peer, self.flow, self.profile, self.contract = contracts(
            flow_id=flow_id, kind=kind, **contract_kwargs)
        self.now_us = NOW_US
        self.now_epoch = NOW_EPOCH
        self.ledger = Ledger(BudgetPolicy(60_000_000, 30_000_000, 60_000_000, 10_000_000,
                                          quantization_us=1, max_reservations=1),
                             b"l" * 16, self.now_us)
        self.ledger.observe(CounterSample(self.now_us, 10_000_000, b"l" * 16), self.now_us)
        self.scheduler = Scheduler(local_peer=self.local, ledger=self.ledger,
                                   epoch=b"s" * 16, broker_epoch=b"b" * 16)
        self.owner = MeshticulumFlowOwner(owner_id=b"o" * 16, flow=self.flow,
                                          contract=self.contract, profile=self.profile,
                                          no_send=no_send)
        self.scheduler.open_flow(self.flow, self.contract, self.profile,
                                 attempt_owner=self.owner, now_us=self.now_us,
                                 now_epoch=self.now_epoch)
        self.enqueued: list[tuple[int, FrameRole]] = []
        self.requests: list = []
        self.carrier_calls: list = []
        self.active = {LeaseScope.SERVICE: False, LeaseScope.BROKER: False}
        self.outcome = CommitOutcome.ACCEPTED
        self.on_send = lambda: None
        self._wrap_enqueue()

    def _wrap_enqueue(self):
        inner = self.scheduler.enqueue_from_flow

        def recording(flow, *, now_us, now_epoch, role=FrameRole.DATA):
            result = inner(flow, now_us=now_us, now_epoch=now_epoch, role=role)
            if result is not None:
                self.enqueued.append((result, role))
            return result

        self.scheduler.enqueue_from_flow = recording

    def clock(self):
        return self.now_us

    def lease(self, request, scope):
        self.requests.append(request)

        @contextmanager
        def guard():
            self.active[scope] = True
            try:
                yield DispatchGrant(request, scope, request.deadlines, 1, request.broker_epoch)
            finally:
                self.active[scope] = False

        return guard()

    def authorize(self, request):
        return self.lease(request, LeaseScope.SERVICE)

    def broker(self, request):
        return self.lease(request, LeaseScope.BROKER)

    def carrier(self, frame):
        assert all(self.active.values())
        self.carrier_calls.append(frame)
        self.on_send()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def link(self, *, incarnation_id=1):
        return FlowPortLink(scheduler=self.scheduler, owner=self.owner, flow=self.flow,
                            authorize=self.authorize, broker=self.broker,
                            carrier=self.carrier, monotonic_us=self.clock,
                            epoch_clock=lambda: self.now_epoch,
                            incarnation_id=incarnation_id)

    def dispatch(self, **changes):
        args = dict(authorize=self.authorize, broker=self.broker, send=self.carrier,
                    monotonic_us=self.clock, epoch_clock=lambda: self.now_epoch)
        args.update(changes)
        return self.scheduler.dispatch_one(**args)


def transfer(*, frames=2, port=None, quantum=QUANTUM):
    """A port seat plus a real sender driven through it. Returns everything."""
    port = port or Port()
    limits = FlowLimits(frame_payload_bytes=quantum, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=8, max_delivered_bytes=4096,
                        max_queued_bytes=8192)
    link = port.link()
    startup = StartupGrant(credit_frames=remote_startup_credit(limits))
    tx = ReliableFlow("a", link, limits, flow_id=1, generation=1, receive=False,
                      startup=startup)
    port.owner.accept_grant(port.contract.limits.initial_credit_bytes)
    payload = bytes(range(quantum)) * frames
    tx.send(payload)
    return port, link, tx, payload, limits


def pump(port, link, tx, *, seconds=2.0, step=0.01):
    end = link.now + seconds
    while link.now < end:
        link.advance(step)
        port.now_us = NOW_US + int(link.now * 1_000_000)
        tx.pump(link.now)
    return link.now



def queue_one(port, link, tx):
    """Let the real flow offer exactly one frame; the host dispatches it later.

    The frozen scheduler keeps enqueue and dispatch apart on purpose, so a host
    may hold a queued frame while authority, budget or credit is re-read. This
    drives the flow far enough to reserve a live handle and no further.
    """
    link.defer_to_host = True
    port.now_us = NOW_US
    try:
        tx.pump(link.now)
    finally:
        link.defer_to_host = False
    assert port.owner._live is not None, "the flow offered and the owner reserved"
    return port.owner._live


def cross(*, frames=2, quantum=QUANTUM, initial_credit_bytes=256):
    """A port seat on one side, a plain peer on the other, shuttling real frames.

    The peer is an ordinary `ReliableFlow` over the simulated carrier, so the
    reverse lane really carries acknowledgements and the port really classifies
    them as control.
    """
    # 256 bytes of remote free credit buys floor(256 / 64) = 4 frames, which is
    # the whole three-frame transfer. A 128-byte grant would buy only two, and
    # the owner would correctly refuse the third rather than infer room.
    port = Port(initial_credit_bytes=initial_credit_bytes)
    limits = FlowLimits(frame_payload_bytes=quantum, window_frames=4, initial_credit_frames=4,
                        max_reassembly_frames=8, max_delivered_bytes=4096,
                        max_queued_bytes=8192)
    link = port.link()
    startup = StartupGrant(credit_frames=remote_startup_credit(limits))
    # Both directions are live, so the port really carries an acknowledgement
    # lane as well as a data lane.
    tx = ReliableFlow("a", link, limits, flow_id=1, generation=1, sends=True, receive=True,
                      startup=startup)
    peer_link = SimLink(LinkProfile("f", 4000.0, one_way_delay_s=0.0),
                        LinkProfile("r", 4000.0, one_way_delay_s=0.0), seed=1)
    rx = ReliableFlow("b", peer_link, limits, flow_id=1, generation=1, sends=True,
                      receive=True, startup=startup)
    port.owner.accept_grant(initial_credit_bytes)
    payload = bytes(range(quantum)) * frames
    short = bytes(range(quantum))
    tx.send(payload)
    rx.send(short)
    return port, link, peer_link, tx, rx, (payload, short), limits


def cross_pump(port, link, peer_link, tx, rx, *, seconds=3.0, step=0.01):
    forwarded = 0
    end = link.now + seconds
    while link.now < end:
        link.advance(step)
        peer_link.advance(step)
        port.now_us = NOW_US + int(link.now * 1_000_000)
        tx.pump(link.now)
        while forwarded < len(link.sent):
            peer_link.send("a", link.sent[forwarded])
            forwarded += 1
        peer_link.advance(0.0)
        rx.pump(peer_link.now)
        for delivery in peer_link.inbound("a"):
            link.deliver(delivery.payload, kind=delivery.kind)
        if tx.complete and rx.complete:
            break
    return forwarded

# -- C10: every lane through the scheduler --------------------------------------


def test_the_port_carries_a_real_transfer_and_completes_it():
    port, link, peer_link, tx, rx, payloads, limits = cross(frames=3)
    payload, short = payloads
    # The negotiated starting room is floor(256 / 64) = 4 frames. What the owner
    # holds once the exchange has run is deliberately not that constant: each
    # accepted canonical ACK rebases the snapshot to the receiver's real
    # advertised credit (8 frames = 512 bytes, inside the negotiated 1024-byte
    # window). The frozen r6 seam never moved the owner here at all.
    assert port.owner.snapshot()["frame_grant"] == 4
    forwarded = cross_pump(port, link, peer_link, tx, rx)
    assert port.owner.snapshot()["frame_grant"] == 8
    assert port.carrier_calls, "the scheduler invoked the carrier"
    assert tx.complete and rx.complete, (tx.state, rx.state)
    assert rx.delivered == payload and tx.delivered == short
    roles = [role for _, role in port.enqueued]
    assert roles.count(FrameRole.DATA) == 3, roles
    assert roles.count(FrameRole.CONTROL) >= 1, roles
    assert forwarded >= 3


def test_data_and_control_lanes_are_distinguished_by_the_scheduler():
    port, link, tx, payload, limits = transfer(frames=1)
    pump(port, link, tx, seconds=1.0)
    roles = {role for _, role in port.enqueued}
    assert FrameRole.DATA in roles, "the data lane is a data role"
    # A one-frame transfer completes with an acknowledgement in the reverse
    # direction; that lane is only reachable through a fixed policy, so assert
    # the classification directly as well.
    assert port.owner.lanes.role_for("ack") is FrameRole.CONTROL
    assert port.owner.lanes.role_for("data") is FrameRole.DATA
    assert port.owner.lanes.role_for("anything-else") is FrameRole.DATA


def test_the_link_has_no_second_send_path():
    port, link, tx, payload, limits = transfer(frames=1)
    # The only way bytes leave is FlowPortLink.send, which is scheduler-mediated.
    assert not hasattr(link, "raw_send")
    assert not hasattr(link, "carrier_send")
    assert not hasattr(link, "direct")
    assert link.carrier.__func__ is port.carrier.__func__
    assert link.scheduler is port.scheduler


def test_a_control_frame_that_exceeds_its_own_bound_is_refused_not_truncated():
    port, _, _, _, _ = transfer(frames=1)
    oversized = b"\x00" * (port.contract.limits.max_control_bytes + 1)
    with pytest.raises(ContractError) as error:
        port.owner.offer(oversized, kind="ack")
    assert error.value.code == "flow_port_control_exceeds_admission"


def test_a_shrunken_grant_invalidates_an_unsent_handle_for_sending_only():
    port, link, tx, payload, limits = transfer(frames=4)
    handle = queue_one(port, link, tx)
    before = port.owner.snapshot()
    port.owner.accept_grant(0)
    with port.owner.dispatch_lease(handle) as lease:
        with pytest.raises(ContractError) as error:
            lease.validate(handle, now_us=port.now_us, now_epoch=NOW_EPOCH)
        assert error.value.code == "flow_port_handle_not_sendable"
        # The known handle is still reconciled exactly once, and no credit,
        # sequence or retry attempt is taken.
        lease.commit(CommitRequest(handle, CommitOutcome.REFUSED_SCHEDULER, port.now_us,
                                   handle.binding.contract.deadlines))
    after = port.owner.snapshot()
    assert after["rf_attempts"] == before["rf_attempts"]
    assert after["reconciled_unsent"] == before["reconciled_unsent"] + 1
    assert after["live_handles"] == 0


# -- C11: exactly-once attempted settlement -------------------------------------


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_an_attempted_settlement_converts_once_and_retains_the_debt(outcome):
    port, link, tx, payload, limits = transfer(frames=1)
    port.outcome = outcome
    queue_one(port, link, tx)
    result = port.dispatch()
    assert result.outcome is outcome
    snapshot = port.owner.snapshot()
    assert snapshot["rf_attempts"] == 1
    assert snapshot["unreflected"] == 1, "an attempted admission stays unreflected"
    assert snapshot["live_handles"] == 0
    # A second dispatch cannot settle anything again: the queue is empty.
    assert port.dispatch().outcome is None


def test_a_refused_carrier_takes_no_credit_sequence_or_retry_clock():
    port, link, tx, payload, limits = transfer(frames=1)
    port.outcome = CommitOutcome.REFUSED_CARRIER
    before = port.owner.snapshot()
    queue_one(port, link, tx)
    result = port.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_CARRIER
    after = port.owner.snapshot()
    assert after["rf_attempts"] == before["rf_attempts"] == 0
    assert after["unreflected"] == before["unreflected"] == 0
    assert after["logical_admissions"] == before["logical_admissions"]
    assert after["reconciled_unsent"] == before["reconciled_unsent"] + 1


def test_a_duplicate_completion_cannot_repeat_state():
    port, link, tx, payload, limits = transfer(frames=1)
    handle = queue_one(port, link, tx)
    request = CommitRequest(handle, CommitOutcome.ACCEPTED, port.now_us,
                            handle.binding.contract.deadlines)
    with port.owner.dispatch_lease(handle) as lease:
        lease.commit(request)
        after = port.owner.snapshot()
        with pytest.raises(ContractError):
            lease.commit(request)
    assert port.owner.snapshot() == after


def test_a_forged_or_stale_handle_cannot_acquire_a_lease():
    from dataclasses import replace

    port, link, tx, payload, limits = transfer(frames=1)
    handle = queue_one(port, link, tx)
    for forged in (replace(handle), replace(handle, owner_id=b"x" * 16),
                   replace(handle, reservation_id=b"y" * 16),
                   replace(handle, owner_revision=handle.owner_revision + 1)):
        with pytest.raises(ContractError) as error:
            port.owner.dispatch_lease(forged)
        assert error.value.code == "flow_port_handle_refused"
    assert port.owner.snapshot()["live_handles"] == 1, "the real handle is untouched"


# -- the no-send fence and the control-role addendum ----------------------------


def test_the_no_send_fence_suppresses_every_lane():
    port, link, tx, payload, limits = transfer(frames=2)
    port.owner.settle_sending()
    pump(port, link, tx, seconds=1.0)
    assert port.carrier_calls == [], "no lane reached the carrier"
    assert link.sent == []
    assert port.owner.snapshot()["queued"] == 0 or port.owner.snapshot()["no_send"] is True
    port.owner.resume_sending()
    pump(port, link, tx, seconds=1.0)
    assert port.carrier_calls, "the fence lifts"


def test_control_role_cannot_be_relabelled_and_is_fixed_at_construction():
    policy = LanePolicy()
    assert policy.role_for("ack") is FrameRole.CONTROL
    for kind in ("data", "retry", "probe", "", "ACK", "control"):
        assert policy.role_for(kind) is FrameRole.DATA
    with pytest.raises(ContractError):
        policy.role_for(None)


def test_the_admitted_bound_deducts_named_profile_overhead_once():
    _, _, _, profile, contract = contracts()
    # payload_capacity = mtu - header - authentication = 128 - 13 - 16 = 99.
    assert profile.payload_capacity == 99
    # The contract ceiling is the smaller bound, so the admitted frame bound is 64.
    assert admitted_frame_bound(contract=contract, profile=profile) == 64
    # Deducting nothing extra leaves the bound alone; an unaccounted overhead is
    # deducted exactly once.
    assert admitted_frame_bound(contract=contract, profile=profile,
                                unaccounted_overhead_bytes=12) == 52
    # A profile narrower than the contract wins.
    # A narrower profile wins: 40 - 13 - 16 = 11.
    _, _, _, narrow, narrow_contract = contracts(profile_payload_capacity=40)
    assert narrow.payload_capacity == 11
    assert admitted_frame_bound(contract=narrow_contract, profile=narrow) == 11


def test_the_byte_grant_maps_to_frames_by_floor_and_never_clamps_up():
    for byte_grant, expected in ((0, 0), (63, 0), (64, 1), (255, 3), (256, 4)):
        assert frame_grant_from_bytes(byte_grant, 64, 16) == expected
    assert frame_grant_from_bytes(1000, 64, 3) == 3, "the reorder ceiling still binds"
    # Every unreflected admission is subtracted from the snapshot: with four
    # slots and four distinct unreflected admissions there is no room for a fifth.
    port, link, tx, payload, limits = transfer(frames=1)
    port.owner._grant = 4
    port.owner._grant_established = True
    port.owner._unreflected = {1, 2, 3, 4}
    assert port.owner._has_slot() is False
    port.owner._unreflected = {1, 2, 3}
    assert port.owner._has_slot() is True


def test_no_grant_means_no_slot_and_no_local_inference():
    port, _, _, _, _ = transfer(frames=1)
    port.owner._grant = 0
    port.owner._grant_established = False
    assert port.owner._has_slot() is False, "local limits are not peer capacity"


# -- C12: namespace, lifecycle and contract boundaries --------------------------


def test_a_wrong_flow_cannot_prepare_or_claim_the_owner():
    from dataclasses import replace

    port, _, _, _, _ = transfer(frames=1)
    with pytest.raises(ContractError) as error:
        port.owner.prepare(replace(port.flow, flow_id=2), now_us=NOW_US, now_epoch=NOW_EPOCH)
    assert error.value.code == "flow_port_wrong_flow"


def test_mt3_never_silently_accepts_an_mt2_or_unparsable_frame():
    port, _, _, _, _ = transfer(frames=1)
    modern = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                              payload=b"x" * QUANTUM)
    assert port.owner.offer(modern, kind="data") is FrameRole.DATA
    with pytest.raises(ContractError) as error:
        port.owner.offer(b"MT2" + modern[3:], kind="data")
    assert error.value.code == "flow_port_unparsable_frame"
    with pytest.raises(ContractError):
        port.owner.offer(b"", kind="data")


def test_the_owner_refuses_an_attempt_binding_that_disagrees_with_the_registration():
    _, _, flow, profile, contract = contracts()
    owner = MeshticulumFlowOwner(owner_id=b"o" * 16, flow=flow, contract=contract,
                                 profile=profile)
    # The frozen record re-derives cost and refuses a mismatched profile digest.
    cost = profile_cost(profile)
    assert owner.cost == cost
    # A cost that is not this profile's cost cannot be bound at all.
    with pytest.raises(ContractError) as error:
        AttemptBinding(flow, contract.route, contract, 1, b"abc",
                       hashlib.sha256(b"abc").digest(), 0, 1, 0, contract.deadlines,
                       profile, Cost(1, 1))
    assert error.value.code == "attempt_profile_cost_mismatch"
    with pytest.raises(ContractError):
        MeshticulumFlowOwner(owner_id=bytes(16), flow=flow, contract=contract, profile=profile)
    # A profile whose digest is not the route's digest cannot be registered.
    _, _, _, other_profile, _ = contracts(flow_id=2)
    with pytest.raises(ContractError) as error:
        MeshticulumFlowOwner(owner_id=b"o" * 16, flow=flow, contract=contract,
                             profile=other_profile)
    assert error.value.code == "invalid_flow_port_registration"
    assert MeshticulumFlowOwner(owner_id=b"o" * 16, flow=flow, contract=contract,
                                profile=profile, no_send=False).no_send is False


# =============================================================================
# r7 controls. Each one carries a repair group from
# work/meshticulum-r6-review-20261004/DECISION.json and is written to be RED on
# the held r6 bytes and GREEN on this revision; the paired held-versus-repaired
# run records both directions.
# =============================================================================


def deferred(port, link, tx):
    """Queue exactly the flow's next frame and stop before any dispatch."""
    link.defer_to_host = True
    try:
        tx.pump(link.now)
    finally:
        link.defer_to_host = False
    return port.owner._live


# -- REPAIR01 / CF01: a queued frame has not started its logical flight ---------


def test_a_deferred_enqueue_consumes_no_sequence_credit_or_retry_bookkeeping():
    """CF01: r6 treated a truthy queue admission as a handed-over transmission."""
    port, link, tx, payload, limits = transfer(frames=1)
    before = (tx._next_seq, tx.in_flight(), tx.acked_frames(), tx.queued_bytes(),
              tx.retransmits, tx.refused_sends)
    assert deferred(port, link, tx) is not None, "the owner did reserve a live handle"
    after = (tx._next_seq, tx.in_flight(), tx.acked_frames(), tx.queued_bytes(),
             tx.retransmits, tx.refused_sends)
    assert after == before, "host enqueue spent flow sequence/pending/retry state"
    assert tx.deferred_sends == 1
    assert port.owner.snapshot()["rf_attempts"] == 0
    assert port.carrier_calls == []


@pytest.mark.parametrize("refusal", ["carrier", "authority"])
def test_a_deferred_refusal_leaves_the_whole_flow_where_it_was(refusal):
    """CF01: an unsent refusal may not consume flow sequence or leave a timer."""
    port, link, tx, payload, limits = transfer(frames=1)
    before = (tx._next_seq, tx.in_flight(), tx.queued_bytes())
    handle = deferred(port, link, tx)
    if refusal == "carrier":
        port.outcome = CommitOutcome.REFUSED_CARRIER
    else:
        def refusing(request):
            raise ContractError("fixture_authority_refused")
        link.authorize = refusing
    assert link.dispatch_queued() is False
    after = (tx._next_seq, tx.in_flight(), tx.queued_bytes())
    assert after == before, "an unsent refusal spent flow state"
    snapshot = port.owner.snapshot()
    assert snapshot["reconciled_unsent"] == 1, "and the handle is reconciled exactly once"
    assert snapshot["rf_attempts"] == 0
    assert snapshot["live_handles"] == 0
    assert port.owner._settled_handles == {id(handle)}


# -- REPAIR01 / CF02: the retry clock starts at the handover --------------------


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_a_delayed_dispatch_starts_the_attempt_and_clock_at_the_handover(outcome):
    """CF02: r6 armed the flow timer at enqueue, then retried against it."""
    port, link, tx, payload, limits = transfer(frames=1)
    port.outcome = outcome
    deferred(port, link, tx)
    # The host takes two seconds to re-read authority and dispatch.
    link.now = 2.0
    port.now_us = NOW_US + 2_000_000
    assert link.dispatch_queued() is True
    assert len(port.carrier_calls) == 1
    pending = tx._in_flight[0]
    assert pending.handed_over_at == 2.0, "r6 armed the clock at enqueue, not handover"
    assert pending.sent_at >= 2.0
    assert port.owner.snapshot()["rf_attempts"] == 1
    # The original deadlines and profile are unchanged at t2, so a pump that can
    # send must not find an expired frame and duplicate the attempt.
    retransmits, calls = tx.retransmits, len(port.carrier_calls)
    tx.pump(link.now, max_frames=0)
    assert (tx.retransmits, len(port.carrier_calls)) == (retransmits, calls)


# -- REPAIR02 / PORT-R6-01: the lifecycle fence is read before the carrier ------


def test_a_stop_after_enqueue_invokes_no_carrier_and_reconciles_once():
    """PORT-R6-01: r6 invoked the mock carrier once despite the pre-send stop."""
    port, link, tx, payload, limits = transfer(frames=1)
    handle = deferred(port, link, tx)
    port.owner.settle_sending()
    assert link.dispatch_queued() is False
    assert port.carrier_calls == [], "the fence is read before the carrier is invoked"
    snapshot = port.owner.snapshot()
    assert snapshot["reconciled_unsent"] == 1
    assert snapshot["rf_attempts"] == 0
    assert snapshot["live_handles"] == 0
    assert id(handle) in port.owner._settled_handles


def test_a_stop_inside_the_service_lease_still_invokes_no_carrier():
    """PORT-R6-01: the lifecycle is re-read after every policy lease."""
    port, link, tx, payload, limits = transfer(frames=1)
    deferred(port, link, tx)
    service = port.authorize

    def stop_inside(request):
        lease = service(request)
        port.owner.settle_sending()
        return lease

    link.authorize = stop_inside
    assert link.dispatch_queued() is False
    assert port.carrier_calls == []
    assert port.owner.snapshot()["reconciled_unsent"] == 1


def test_a_stop_during_the_carrier_call_still_settles_the_attempt_once():
    """PORT-R6-01: r6 refused to record an attempt after the fence went up."""
    port, link, tx, payload, limits = transfer(frames=1)
    deferred(port, link, tx)
    port.on_send = port.owner.settle_sending
    assert link.dispatch_queued() is True
    snapshot = port.owner.snapshot()
    assert snapshot["rf_attempts"] == 1, "an invoked attempt is recorded exactly once"
    assert snapshot["unreflected"] == 1, "and its uncertainty debt is retained"
    assert snapshot["reconciled_unsent"] == 0, "an attempt that happened is not refunded"
    assert snapshot["live_handles"] == 0
    assert link.last_result.flow_committed is True
    assert link.last_result.flow_fenced is False
    # No second completion and no second carrier call.
    assert port.dispatch().outcome is None
    assert len(port.carrier_calls) == 1


def test_a_zero_grant_after_the_carrier_call_still_settles_the_attempt():
    """PORT-R6-01: eligibility lost in flight must not un-record the attempt."""
    port, link, tx, payload, limits = transfer(frames=1)
    deferred(port, link, tx)
    port.on_send = lambda: port.owner.accept_grant(0)
    assert link.dispatch_queued() is True
    snapshot = port.owner.snapshot()
    assert snapshot["rf_attempts"] == 1
    assert snapshot["unreflected"] == 1
    assert snapshot["reconciled_unsent"] == 0
    assert snapshot["live_handles"] == 0
    assert link.last_result.flow_committed is True
    assert link.last_result.flow_fenced is False


# -- REPAIR03 / PORT-R6-02 and R6-ROOT-F01: atomic lane and frame identity ------


def test_a_credit_blocked_data_frame_cannot_be_sent_under_a_later_ack_lane():
    """PORT-R6-02: r6 popped the older DATA while enqueueing the newer ACK role."""
    port, link, tx, payload, limits = transfer(frames=1)
    port.owner.accept_grant(0)
    link.defer_to_host = True
    try:
        tx.pump(link.now)
    finally:
        link.defer_to_host = False
    assert port.owner._live is None, "a frame with no slot takes no reservation"
    assert port.owner.snapshot()["queued"] == 1, "and it stays queued under its own identity"

    # Room arrives, and the real flow emits an acknowledgement.
    port.owner.accept_grant(port.contract.limits.initial_credit_bytes)
    ack = build_ack_frame(flow_id=1, generation=1, ack_seq=0, credit=2, sack=0,
                          limits=limits, grant_revision=1)
    assert link.send("a", ack, kind=CONTROL_KIND) is True
    assert port.enqueued[-1][1] is FrameRole.CONTROL
    assert port.carrier_calls[-1].payload == ack, "the dispatched bytes are the ACK's"
    assert link.sent[-1] == ack
    assert port.owner.snapshot()["queued"] == 1, "the blocked DATA is still queued"


def test_a_shared_scheduler_never_lets_one_flow_claim_another_dispatch():
    """R6-ROOT-F01: r6 logged and settled B's frame on A's dispatch."""
    port, link, tx, payload, limits = transfer(frames=1)
    _, _, flow_b, profile_b, contract_b = contracts(flow_id=2)
    owner_b = MeshticulumFlowOwner(owner_id=b"p" * 16, flow=flow_b, contract=contract_b,
                                   profile=profile_b)
    port.scheduler.open_flow(flow_b, contract_b, profile_b, attempt_owner=owner_b,
                             now_us=port.now_us, now_epoch=port.now_epoch)
    link_b = FlowPortLink(scheduler=port.scheduler, owner=owner_b, flow=flow_b,
                          authorize=port.authorize, broker=port.broker, carrier=port.carrier,
                          monotonic_us=port.clock, epoch_clock=lambda: port.now_epoch)

    # A queues first, so the shared scheduler's per-peer order puts A at the head.
    assert deferred(port, link, tx) is not None
    frame_a = link._held.payload
    owner_b.accept_grant(contract_b.limits.initial_credit_bytes)
    frame_b = build_data_frame(flow_id=2, generation=1, seq=0, total=1, last=True,
                               payload=b"b" * QUANTUM)
    link_b.defer_to_host = True
    try:
        assert link_b.send("b", frame_b, kind=DATA_KIND) is DEFERRED
    finally:
        link_b.defer_to_host = False

    # B dispatches, and the scheduler hands it A's frame.
    assert link_b.dispatch_queued() is False
    assert link_b.foreign_dispatches == 1
    assert link_b.sent == [], "another flow's dispatch is not this flow's send"
    assert port.carrier_calls[-1].payload == frame_a
    assert owner_b.snapshot()["rf_attempts"] == 0
    assert owner_b.snapshot()["live_handles"] == 1, "B's own handle is still queued"

    # With A's frame consumed, B's own frame dispatches cleanly and exactly.
    assert link_b.dispatch_queued() is True
    assert link_b.sent == [frame_b]
    assert port.carrier_calls[-1].payload == frame_b
    assert owner_b.snapshot()["rf_attempts"] == 1


# -- REPAIR04 / PORT-R6-03 and PORT-R6-05: negotiated bounds and fit -----------


def test_a_grant_wider_than_the_negotiated_window_is_refused_without_mutation():
    """PORT-R6-03: r6 turned a 1024-byte grant into 16 slots under a 128-byte window."""
    port, _, _, _, _ = transfer(frames=1)
    _, _, narrow_flow, narrow_profile, narrow_contract = contracts(window_bytes=128,
                                                                  max_unconsumed_bytes=128)
    owner = MeshticulumFlowOwner(owner_id=b"n" * 16, flow=narrow_flow,
                                 contract=narrow_contract, profile=narrow_profile)
    assert owner.grant_ceiling_bytes() == 128
    before = owner.snapshot()
    with pytest.raises(ContractError) as error:
        owner.accept_grant(1024)
    assert error.value.code == "flow_port_grant_exceeds_window"
    assert owner.snapshot() == before, "a refused snapshot mutates nothing"
    owner.accept_grant(128)
    assert owner.snapshot()["frame_grant"] == 2, "floor(128 / 64), never rounded up"


def test_an_identical_repeat_grant_keeps_an_exactly_fitting_handle_sendable():
    """PORT-R6-05: r6 cancelled a live handle that fit the snapshot exactly."""
    port, link, tx, payload, limits = transfer(frames=1)
    port.owner.accept_grant(port.contract.limits.max_payload_bytes)
    handle = deferred(port, link, tx)
    assert handle is not None, "one free slot admits the first DATA"
    before = port.owner.snapshot()
    port.owner.accept_grant(port.contract.limits.max_payload_bytes)
    with port.owner.dispatch_lease(handle) as lease:
        assert lease.validate(handle, now_us=port.now_us, now_epoch=NOW_EPOCH) is handle.binding
    assert port.owner.snapshot() == before, "an identical snapshot is idempotent"


def test_a_shrink_that_still_fits_keeps_the_handle_sendable():
    """PORT-R6-05: a shrink invalidates only the reservations that no longer fit."""
    port, link, tx, payload, limits = transfer(frames=4)
    port.owner.accept_grant(port.contract.limits.max_payload_bytes * 3)
    handle = deferred(port, link, tx)
    port.owner.accept_grant(port.contract.limits.max_payload_bytes)
    with port.owner.dispatch_lease(handle) as lease:
        assert lease.validate(handle, now_us=port.now_us, now_epoch=NOW_EPOCH) is handle.binding
    assert port.owner.snapshot()["live_handles"] == 1


def test_a_transfer_longer_than_its_first_credit_advances_on_real_receipt():
    """PORT-R6-03: with no ACK seam r6 could never admit the second frame."""
    port, link, peer_link, tx, rx, payloads, limits = cross(frames=3, initial_credit_bytes=64)
    payload, short = payloads
    assert port.owner.snapshot()["frame_grant"] == 1, "exactly one frame of room"
    forwarded = cross_pump(port, link, peer_link, tx, rx)
    assert forwarded >= 3
    assert tx.complete and rx.complete, (tx.state, rx.state)
    assert rx.delivered == payload, "the exact bytes, longer than the opening credit"
    assert port.owner.snapshot()["frame_grant"] > 1, "and the snapshot really advanced"


# -- REPAIR05 / PORT-R6-04: bounded, deduplicated offers and control progress ---


def test_repeated_blocked_offers_neither_grow_the_queue_nor_mint_identities():
    """PORT-R6-04: r6 retained 1000 candidates and 16000 bytes for one frame."""
    port, _, _, _, _ = transfer(frames=1)
    port.owner.accept_grant(0)
    frame = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                             payload=b"k" * QUANTUM)
    before = port.owner.snapshot()
    for _ in range(1000):
        assert port.owner.offer(frame, kind=DATA_KIND) is FrameRole.DATA
    after = port.owner.snapshot()
    assert after["queued"] == before["queued"] + 1, "one frame is one queued frame"
    assert after["queued_bytes"] == before["queued_bytes"] + len(frame)
    assert after["logical_admissions"] == before["logical_admissions"] + 1
    assert port.carrier_calls == []


def test_the_owner_queue_is_bounded_by_the_negotiated_byte_budget():
    """PORT-R6-04: the bound is negotiated, and overflow is a clean refusal."""
    port, _, _, _, _ = transfer(frames=1)
    port.owner.accept_grant(0)
    cap = port.contract.limits.max_queued_bytes
    with pytest.raises(ContractError) as error:
        for seq in range(4096):
            port.owner.offer(build_data_frame(flow_id=1, generation=1, seq=seq, total=4096,
                                              last=False, payload=b"x" * QUANTUM),
                             kind=DATA_KIND)
    assert error.value.code == "flow_port_queue_limit"
    snapshot = port.owner.snapshot()
    assert snapshot["queued"] == port.owner._max_queued_frames
    assert snapshot["queued_bytes"] <= cap


def test_a_credit_blocked_data_frame_does_not_suppress_an_eligible_control_frame():
    """PORT-R6-04: a blocked DATA head must not stall the acknowledgement lane."""
    port, _, _, _, limits = transfer(frames=1)
    port.owner.accept_grant(0)
    data = build_data_frame(flow_id=1, generation=1, seq=0, total=1, last=True,
                            payload=b"d" * QUANTUM)
    assert port.owner.offer(data, kind=DATA_KIND) is FrameRole.DATA
    assert port.owner.prepare(port.flow, now_us=NOW_US, now_epoch=NOW_EPOCH) is None
    ack = build_ack_frame(flow_id=1, generation=1, ack_seq=0, credit=1, sack=0,
                          limits=limits, grant_revision=1)
    assert port.owner.offer(ack, kind=CONTROL_KIND) is FrameRole.CONTROL
    handle = port.owner.prepare(port.flow, now_us=NOW_US, now_epoch=NOW_EPOCH)
    assert handle is not None, "the eligible control frame still reaches the scheduler"
    assert handle.binding.payload == ack
    assert port.owner.snapshot()["queued"] == 1, "and the DATA is still held, not dropped"


# -- R7: a real handover settles the exact transaction it belongs to ------------


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_a_deferred_retry_spends_its_attempt_and_clock_at_the_real_handover(outcome):
    """R7-F01: the retry queued at t2 and dispatched at t4 spent nothing."""
    port, link, tx, _, _ = transfer(frames=1)
    tx.pump(0, max_frames=1)
    assert len(port.carrier_calls) == 1 and tx.in_flight() == 1
    before = tx._in_flight[0]
    first_rto, first_attempts = before.rto, before.attempts
    link.now = 2.0
    port.now_us = NOW_US + 2_000_000
    link.defer_to_host = True
    port.outcome = outcome
    tx.pump(link.now, max_frames=0)
    assert len(port.carrier_calls) == 1, "a queued retry is not an attempt"
    assert tx.retransmits == 0
    link.now = 4.0
    port.now_us = NOW_US + 4_000_000
    link.defer_to_host = False
    assert link.dispatch_queued() is True
    after = tx._in_flight[0]
    assert len(port.carrier_calls) == 2
    assert after.attempts == first_attempts + 1, "the retry spent exactly one attempt"
    assert after.handed_over_at == 4.0, "and its clock starts at the handover"
    assert after.sent_at >= 4.0
    assert after.rto > first_rto, "and its retry clock backed off"
    assert tx.retransmits == 1
    # The same pump that follows the handover cannot immediately retry again.
    tx.pump(link.now, max_frames=0)
    assert len(port.carrier_calls) == 2
    assert tx.retransmits == 1


def test_a_deferred_credit_probe_spends_one_bounded_silence_attempt():
    """R7-F02: an actually dispatched probe recorded no probe and no deadline."""
    port, link, tx, _, limits = transfer(frames=2)
    tx.pump(0, max_frames=1)
    link.now = 0.1
    port.now_us = NOW_US + 100_000
    link.deliver(build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=0, sack=0,
                                 grant_revision=2, limits=limits), kind="ack")
    tx.pump(link.now)
    assert tx.in_flight() == 0 and tx.acked_frames() == 1
    assert tx._probe_at is not None
    probes_before = tx.zero_window_probes
    # The probe queues at t1.1 and really leaves at t2.1.
    link.now = 1.1
    port.now_us = NOW_US + 1_100_000
    link.defer_to_host = True
    tx.pump(link.now)
    assert tx.zero_window_probes == probes_before, "a queued probe is not an attempt"
    link.now = 2.1
    port.now_us = NOW_US + 2_100_000
    link.defer_to_host = False
    assert link.dispatch_queued() is True
    assert len(port.carrier_calls) == 2
    assert port.owner.snapshot()["rf_attempts"] == 2
    assert tx.zero_window_probes == probes_before + 1, "the probe spent one bounded attempt"
    assert tx._probe_attempts == 1
    assert tx._probe_at == pytest.approx(2.1 + tx._probe_interval()), (
        "the next silence deadline starts at the handover, not the enqueue")
    assert tx.queued_bytes() == QUANTUM and tx._next_seq == 1, "and no DATA was admitted"


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_a_host_dispatched_frame_settles_the_originating_link_and_flow(outcome):
    """R7-F03: a direct host dispatch settled the owner but stranded the link."""
    port, link, tx, payload, _ = transfer(frames=1)
    handle = queue_one(port, link, tx)
    held_payload = link._held.payload
    port.outcome = outcome
    link.now = 2.0
    port.now_us = NOW_US + 2_000_000
    result = port.dispatch()
    assert result.outcome is outcome
    assert len(port.carrier_calls) == 1
    assert link.sent == [held_payload], "the exact queued bytes are logged as sent"
    assert handle.binding.payload == held_payload
    assert link._held is None, "the hold is released by the real outcome"
    assert link.queued_bytes("a") == 0
    pending = tx._in_flight
    assert len(pending) == 1 and 0 in pending
    assert pending[0].handed_over_at == 2.0, "the flow starts its attempt at the handover"
    assert tx.queued_bytes() == 0, "and consumes exactly the offered byte"
    assert port.owner.snapshot()["rf_attempts"] == 1
    tx.pump(link.now, max_frames=0)
    assert len(port.carrier_calls) == 1, "no further attempt follows the settlement"
    assert tx.retransmits == 0


def test_a_known_unsent_refusal_releases_the_hold_so_the_flow_can_reoffer():
    """R7-F03: a confirmed-unsent refusal left a stale hold and blocked recovery."""
    port, link, tx, payload, _ = transfer(frames=1)
    queue_one(port, link, tx)
    port.outcome = CommitOutcome.REFUSED_CARRIER
    result = port.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_CARRIER
    assert link._held is None, "the stale hold does not outlive the refusal"
    assert link.sent == [], "a refused emission is not logged as sent"
    snapshot = port.owner.snapshot()
    assert snapshot["reconciled_unsent"] == 1 and snapshot["rf_attempts"] == 0
    assert tx.queued_bytes() == len(payload), "the flow still owns every byte"
    # The flow still owns its byte, and the hold it was queued under is gone, so
    # a later eligible pump can re-offer it rather than being refused forever.
    port.outcome = CommitOutcome.ACCEPTED
    tx.pump(link.now)
    assert len(port.carrier_calls) == 2, "the re-offer reaches the carrier"
    assert port.owner.snapshot()["rf_attempts"] == 1
    assert tx.queued_bytes() == 0, "and the byte is finally handed over"
    assert tx.in_flight() == 1, "as exactly one flight"


def test_two_flows_on_one_scheduler_each_settle_their_own_originating_frame():
    """R7-F03: both flows really sent, yet both stayed held with 48 bytes out."""
    port, link_a, tx_a, payload_a, limits = transfer(frames=1)
    _, _, flow_b, profile_b, contract_b = contracts(flow_id=2)
    owner_b = MeshticulumFlowOwner(owner_id=b"p" * 16, flow=flow_b, contract=contract_b,
                                   profile=profile_b)
    port.scheduler.open_flow(flow_b, contract_b, profile_b, attempt_owner=owner_b,
                             now_us=port.now_us, now_epoch=port.now_epoch)
    owner_b.accept_grant(contract_b.limits.initial_credit_bytes)
    link_b = FlowPortLink(scheduler=port.scheduler, owner=owner_b, flow=flow_b,
                          authorize=port.authorize, broker=port.broker, carrier=port.carrier,
                          monotonic_us=port.clock, epoch_clock=lambda: port.now_epoch)
    tx_b = ReliableFlow("a", link_b, limits, flow_id=2,
                        startup=StartupGrant(remote_startup_credit(limits)))
    tx_b.send(b"B" * QUANTUM)

    queue_one(port, link_a, tx_a)
    tx_b.pump(0, max_frames=1)
    for _ in range(4):
        link_a.dispatch_queued()
        link_b.dispatch_queued()
        tx_a.pump(0, max_frames=0)
        tx_b.pump(0, max_frames=0)

    assert len(port.carrier_calls) == 2
    assert sorted(c.request.flow.flow_id for c in port.carrier_calls) == [1, 2]
    assert link_a.sent and link_b.sent, "each flow logged its own dispatch"
    assert link_a._held is None and link_b._held is None
    assert tx_a.queued_bytes() == 0 and tx_b.queued_bytes() == 0
    assert tx_a.in_flight() == 1 and tx_b.in_flight() == 1
    assert port.owner.rf_attempts == owner_b.rf_attempts == 1


# -- R8: one queued emission carries exactly one outcome record -----------------


def _queue_retry(port, link, tx, *, repetitions=1):
    """Drive one real send, then let its retry queue without dispatching it."""
    tx.pump(0, max_frames=1)
    link.now = 2.0
    port.now_us = NOW_US + 2_000_000
    link.defer_to_host = True
    try:
        for _ in range(repetitions):
            tx.pump(2, max_frames=0)
    finally:
        link.defer_to_host = False
    return link.now


def _markers(tx):
    return sum(len(value) for value in tx._deferred_retries.values())


def test_repeating_the_same_deferred_retry_records_one_outcome():
    """R8-F01: 1000 identical pumps left 1000 markers against one held frame."""
    port, link, tx, _, _ = transfer(frames=1)
    _queue_retry(port, link, tx, repetitions=1000)
    assert link._held is not None, "one queued reservation is still held"
    assert link.queued_bytes("a") == len(link._held.payload), (
        "exactly one frame's worth of bytes is held, however many times it was offered")
    assert _markers(tx) == 1, "one queued emission is one outcome record"
    assert len(port.carrier_calls) == 1, "and no repeat was a transmission"
    assert tx.deferred_sends == 1000, "every deferral is still counted as a deferral"


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_one_real_handover_clears_the_single_record_it_settles(outcome):
    """R8-F01: the record must be consumed by the handover that ends it."""
    port, link, tx, _, _ = transfer(frames=1)
    _queue_retry(port, link, tx, repetitions=1000)
    assert _markers(tx) == 1
    link.now = 4.0
    port.now_us = NOW_US + 4_000_000
    port.outcome = outcome
    assert link.dispatch_queued() is True
    assert _markers(tx) == 0, "the single record is gone, with nothing stranded"
    assert port.owner.rf_attempts == 2 and tx.retransmits == 1
    assert tx._in_flight[0].attempts == 2


def test_a_refused_queued_retry_leaves_no_record_behind():
    """R8-F02: a known-unsent refusal released the hold but stranded the marker."""
    port, link, tx, _, _ = transfer(frames=1)
    _queue_retry(port, link, tx, repetitions=1000)
    assert _markers(tx) == 1
    link.now = 4.0
    port.now_us = NOW_US + 4_000_000
    port.outcome = CommitOutcome.REFUSED_CARRIER
    assert link.dispatch_queued() is False
    assert link._held is None, "the hold is released"
    assert _markers(tx) == 0, "and the record for that emission goes with it"
    assert port.owner.rf_attempts == 1, "a refusal is not a new attempt"
    assert tx.retransmits == 0, "and not a retransmission"
    assert tx._in_flight[0].attempts == 1, "the pending attempt is untouched"


def test_the_retry_record_survives_until_its_own_outcome_arrives():
    """R8-F02: the record is keyed by application bytes, not by the held frame."""
    port, link, tx, _, _ = transfer(frames=1)
    _queue_retry(port, link, tx)
    assert _markers(tx) == 1
    held = link._held
    # The link holds the encoded frame; the flow records the bytes it carries.
    # They are different objects, so a cancellation must not match on identity.
    assert tx._deferred_retries[0][0] != held.payload
    assert tx.handover_cancelled(0, held.payload) is True, (
        "the encoded frame identifies the record it belongs to")
    assert _markers(tx) == 0
    assert tx.handover_cancelled(0, held.payload) is False, "and only once"


@pytest.mark.parametrize("refusal", ["carrier", "authority"])
def test_a_refused_queued_probe_retracts_its_obligation(refusal):
    """R8B-F01: a known-unsent probe kept its marker and stole a later DATA handover."""
    port, link, tx, _, limits = transfer(frames=2)
    tx.pump(0, max_frames=1)
    link.now = 0.1
    port.now_us = NOW_US + 100_000
    link.deliver(build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=0, sack=0,
                                 grant_revision=2, limits=limits), kind="ack")
    tx.pump(link.now)
    link.now = 1.1
    port.now_us = NOW_US + 1_100_000
    link.defer_to_host = True
    tx.pump(link.now)
    assert link._held is not None, "the probe is queued for the host"
    assert tx._probe_outstanding is True, "and the flow knows it is owed a handover"
    link.now = 2.1
    port.now_us = NOW_US + 2_100_000
    link.defer_to_host = False
    if refusal == "carrier":
        # The carrier was invoked and its queue refused the probe before send.
        port.outcome = CommitOutcome.REFUSED_CARRIER
        assert link.dispatch_queued() is False
    else:
        # The scheduler refused the frame before the carrier was ever invoked.
        port.outcome = CommitOutcome.ACCEPTED
        refused = port.dispatch(authorize=lambda request: None)
        assert refused.outcome is CommitOutcome.REFUSED_SCHEDULER
        assert refused.reason == "service_authority_refused"
        assert refused.invoked is False, "the carrier never saw the probe"
    assert link._held is None, "the hold is released"
    assert tx._probe_outstanding is False, (
        "the abandoned probe obligation is retracted with the emission it described")
    assert tx._probe_attempts == 0, "and spends no silence attempt"
    assert tx.retransmits == 0, "nor a retransmission"
    assert port.owner.rf_attempts == 1, "a refusal is not a new RF attempt"
    assert tx._next_seq == 1 and tx.queued_bytes() == QUANTUM, "and no DATA was admitted"
    # A real canonical ACK reopens one slot; the fresh frame must be accounted as DATA
    # rather than as the probe identity the abandoned emission left behind.
    link.now = 2.2
    port.now_us = NOW_US + 2_200_000
    link.deliver(build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=1, sack=0,
                                 grant_revision=3, limits=limits), kind="ack")
    link.defer_to_host = True
    tx.pump(link.now)
    assert link._held is not None
    link.now = 2.3
    port.now_us = NOW_US + 2_300_000
    link.defer_to_host = False
    port.outcome = CommitOutcome.ACCEPTED
    assert link.dispatch_queued() is True
    assert tx._next_seq == 2, "the DATA handover consumed its own sequence"
    assert tx.queued_bytes() == 0, "and its own byte"
    assert tx.in_flight() == 1, "and created one logical flight"
    assert tx._probe_attempts == 0 and tx.zero_window_probes == 0, (
        "the probe was never charged an attempt it did not make")
    assert tx.retransmits == 0


# =============================================================================
# R8C-F01: the slot going to another flow is a queue, not a refusal
# =============================================================================


def _at(seconds, port, *links):
    """Move the host clock the fixture reads and every link's own clock."""
    port.now_us = NOW_US + int(seconds * 1_000_000)
    for link in links:
        link.now = seconds


def _competing_hold(port, limits, *, flow_id=2, quantum=QUANTUM):
    """Queue one legitimate DATA frame on another flow of the same scheduler."""
    _, _, flow_b, profile_b, contract_b = contracts(flow_id=flow_id)
    owner_b = MeshticulumFlowOwner(owner_id=b"p" * 16, flow=flow_b, contract=contract_b,
                                   profile=profile_b)
    port.scheduler.open_flow(flow_b, contract_b, profile_b, attempt_owner=owner_b,
                             now_us=port.now_us, now_epoch=port.now_epoch)
    owner_b.accept_grant(contract_b.limits.initial_credit_bytes)
    link_b = FlowPortLink(scheduler=port.scheduler, owner=owner_b, flow=flow_b,
                          authorize=port.authorize, broker=port.broker, carrier=port.carrier,
                          monotonic_us=port.clock, epoch_clock=lambda: port.now_epoch)
    tx_b = ReliableFlow("a", link_b, limits, flow_id=flow_id, receive=False,
                        startup=StartupGrant(remote_startup_credit(limits)))
    tx_b.send(b"B" * quantum)
    link_b.defer_to_host = True
    try:
        tx_b.pump(link_b.now, max_frames=1)
    finally:
        link_b.defer_to_host = False
    assert link_b._held is not None, "the competing frame is queued, not sent"
    return link_b


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_a_retry_queued_by_a_foreign_dispatch_settles_at_its_own_handover(outcome):
    """R8C-F01: a foreign dispatch reported the queued retry as refused."""
    port, link, tx, _, limits = transfer(frames=1)
    tx.pump(0, max_frames=1)
    assert tx.in_flight() == 1 and port.owner.snapshot()["rf_attempts"] == 1
    link_b = _competing_hold(port, limits)

    _at(2.0, port, link, link_b)
    port.outcome = outcome
    tx.pump(2.0, max_frames=0)
    assert link.foreign_dispatches == 1, "the slot went to the other flow"
    assert tx.refused_sends == 0, "a frame still queued was never refused"
    assert tx.deferred_sends == 1
    assert _markers(tx) == 1, "the queued retransmission carries its one record"
    assert tx.retransmits == 0, "and spends nothing before the frame leaves"

    _at(2.2, port, link, link_b)
    assert port.dispatch().outcome is outcome
    assert tx.retransmits == 1, "the real handover spends exactly one retransmission"
    assert tx._in_flight[0].attempts == 2
    assert tx._in_flight[0].handed_over_at == 2.2, "at the actual dispatch instant"
    assert _markers(tx) == 0
    assert link._held is None
    assert port.owner.rf_attempts == 2 and len(link.sent) == 2


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_a_probe_queued_by_a_foreign_dispatch_settles_at_its_own_handover(outcome):
    """R8C-F01: the queued probe stayed unrecorded, so its dispatch settled nothing."""
    port, link, tx, _, limits = transfer(frames=2)
    tx.pump(0, max_frames=1)
    _at(0.1, port, link)
    link.deliver(build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=0, sack=0,
                                 grant_revision=2, limits=limits), kind="ack")
    tx.pump(0.1)
    assert tx.in_flight() == 0 and tx._probe_at is not None
    link_b = _competing_hold(port, limits)

    _at(2.0, port, link, link_b)
    port.outcome = outcome
    tx.pump(2.0, max_frames=1)
    assert link.foreign_dispatches == 1, "the slot went to the other flow"
    assert tx.refused_sends == 0 and tx.deferred_sends == 1
    assert tx._probe_outstanding is True, "the queued probe is owed a handover"
    assert tx.zero_window_probes == 0, "and is not charged an attempt in advance"

    _at(2.2, port, link, link_b)
    assert port.dispatch().outcome is outcome
    assert tx.zero_window_probes == 1 and tx._probe_attempts == 1
    assert tx._probe_outstanding is False
    assert tx._probe_at == pytest.approx(2.2 + tx._probe_interval()), (
        "the silence clock starts at the real handover")
    assert tx._next_seq == 1 and tx.queued_bytes() == QUANTUM, "no DATA was admitted"
    assert link._held is None
    assert port.owner.rf_attempts == 2


def test_a_first_frame_offered_behind_another_flow_is_queued_not_refused():
    """R8C-F01: a foreign dispatch on a first offer is still this flow's queue."""
    port, link, tx, payload, limits = transfer(frames=1)
    link_b = _competing_hold(port, limits)

    tx.pump(0, max_frames=1)
    assert link.foreign_dispatches == 1, "the competing frame took the slot"
    assert tx.refused_sends == 0 and tx.deferred_sends == 1
    assert tx.in_flight() == 0 and tx.queued_bytes() == len(payload), (
        "the flow keeps the byte it has not handed over")
    assert link.sent == [], "and nothing is logged as sent"

    _at(0.2, port, link, link_b)
    assert port.dispatch().outcome is CommitOutcome.ACCEPTED
    assert tx.queued_bytes() == 0 and tx.in_flight() == 1
    assert tx._next_seq == 1, "exactly one sequence is consumed, on the handover"
    assert link.sent == [port.carrier_calls[-1].payload]


def test_a_terminally_unsent_retry_is_still_a_refusal_not_a_queue():
    """R8C-F01: the new distinction may not turn every false into a deferral."""
    port, link, tx, _, _ = transfer(frames=1)
    tx.pump(0, max_frames=1)

    _at(2.0, port, link)
    port.outcome = CommitOutcome.REFUSED_CARRIER
    tx.pump(2.0, max_frames=0)
    assert link._held is None, "the terminal refusal releases the hold"
    assert tx.refused_sends == 1, "a frame the carrier never took is a refusal"
    assert tx.deferred_sends == 0
    assert _markers(tx) == 0, "and it leaves no record waiting for a handover"
    assert tx.retransmits == 0 and tx._in_flight[0].attempts == 1
    assert port.owner.rf_attempts == 1


def _hold_a_competing_data_frame(port, receiver, limits):
    """Queue a second flow's legitimate DATA ahead of this receiver's ACK.

    The ACK lane and the DATA lane share one scheduler, so this is exactly the
    ordinary contention the queued-ACK finding describes: the receiver offers a
    current-credit acknowledgement, the shared dispatch gives the slot to a
    different flow, and the acknowledgement stays queued under its own handle.
    """
    _, _, flow_b, profile_b, contract_b = contracts(flow_id=2)
    owner_b = MeshticulumFlowOwner(owner_id=b"p" * 16, flow=flow_b, contract=contract_b,
                                   profile=profile_b)
    port.scheduler.open_flow(flow_b, contract_b, profile_b, attempt_owner=owner_b,
                             now_us=port.now_us, now_epoch=port.now_epoch)
    owner_b.accept_grant(contract_b.limits.initial_credit_bytes)
    link_b = FlowPortLink(scheduler=port.scheduler, owner=owner_b, flow=flow_b,
                          authorize=port.authorize, broker=port.broker, carrier=port.carrier,
                          monotonic_us=port.clock, epoch_clock=lambda: port.now_epoch)
    tx_b = ReliableFlow("a", link_b, limits, flow_id=2, receive=False,
                        startup=StartupGrant(remote_startup_credit(limits)))
    tx_b.send(b"B" * QUANTUM)
    link_b.defer_to_host = True
    try:
        tx_b.pump(link_b.now, max_frames=1)
    finally:
        link_b.defer_to_host = False
    assert link_b._held is not None, "the competing frame is queued, not sent"
    return link_b


def _queue_the_receivers_current_ack(port, link, receiver, limits):
    """Offer the receiver's current-credit ACK and let the other flow take it."""
    link_b = _hold_a_competing_data_frame(port, receiver, limits)
    receiver.grant_startup()
    receiver.pump(0, max_frames=0)
    assert link.foreign_dispatches == 1, "the competing DATA took the shared slot"
    assert link.sent == [], "the acknowledgement is still queued, not sent"
    return link_b


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_a_queued_ack_commits_its_own_snapshot_at_the_real_handover(outcome):
    """R8D-F01: a dispatched ACK left the receiver's canonical state at its start."""
    port, link, peer_link, receiver, peer, _, limits = cross()
    link_b = _queue_the_receivers_current_ack(port, link, receiver, limits)
    assert receiver._grant_revision == 1, (
        "nothing is committed while the acknowledgement is only queued")

    _at(0.1, port, link, peer_link, link_b)
    port.outcome = outcome
    assert port.dispatch().outcome is outcome
    assert link.handed_over == 1 and len(link.sent) == 1
    assert receiver._grant_snapshot == (0, 0, 8), "the emitted snapshot is canonical"
    assert receiver._grant_revision == 2, "and it carries the revision it went out on"
    assert receiver._last_ack_at == pytest.approx(0.1), (
        "the acknowledgement clock starts at the dispatch instant, not at enqueue")
    assert receiver._ack_dirty is False

    for frame in link.sent:
        peer_link.send("a", frame, kind="ack")
    peer_link.advance(0.2)
    peer.pump(peer_link.now, max_frames=0)
    assert peer._peer_credit == 8 and peer.conflicting_grants == 0


def test_a_changed_snapshot_after_a_queued_ack_mints_a_higher_revision():
    """R8D-F01: the next changed snapshot reused the revision the ACK went out on."""
    port, link, peer_link, receiver, peer, _, limits = cross()
    link_b = _queue_the_receivers_current_ack(port, link, receiver, limits)
    port.outcome = CommitOutcome.ACCEPTED
    _at(0.1, port, link, peer_link, link_b)
    assert port.dispatch().outcome is CommitOutcome.ACCEPTED

    for frame in link.sent:
        peer_link.send("a", frame, kind="ack")
    peer_link.advance(0.2)
    peer.pump(peer_link.now, max_frames=0)

    # A public application action changes the same canonical snapshot to zero.
    forwarded = len(link.sent)
    receiver.stall()
    _at(20.0, port, link, peer_link, link_b)
    port.ledger.observe(CounterSample(port.now_us, 10_000_000, b"l" * 16), port.now_us)
    port.outcome = CommitOutcome.ACCEPTED
    receiver.pump(20, max_frames=0)
    assert len(link.sent) == forwarded + 1, "the changed snapshot really went out"
    second = parse_frame(link.sent[-1])
    assert second["grant_revision"] == 3, (
        "changed canonical state takes a higher revision than the queued emission")

    for frame in link.sent[forwarded:]:
        peer_link.send("a", frame, kind="ack")
    peer_link.advance(20)
    peer.pump(peer_link.now, max_frames=0)
    assert peer._peer_credit == 0 and peer.conflicting_grants == 0, (
        "the peer accepts the newer statement instead of refusing a reuse")


def test_a_terminally_unsent_ack_commits_nothing_and_stays_offered():
    """R8D-F01: committing at enqueue would mint canonical state for an unsent ACK."""
    port, link, peer_link, receiver, _, _, limits = cross()
    link_b = _queue_the_receivers_current_ack(port, link, receiver, limits)
    forwarded = len(link.sent)

    _at(0.1, port, link, peer_link, link_b)
    port.outcome = CommitOutcome.REFUSED_CARRIER
    assert port.dispatch().outcome is CommitOutcome.REFUSED_CARRIER
    assert link.handed_over == 0, "the carrier never took it"
    assert receiver._grant_revision == 1, "the refusal minted no revision"
    assert receiver._grant_snapshot == (0, 0, 4), "nor canonical state"
    assert receiver._last_ack_at == -1.0 and receiver._ack_dirty is True

    # The flow still owns the statement, so a later pump re-offers it afresh.
    _at(20.0, port, link, peer_link, link_b)
    port.ledger.observe(CounterSample(port.now_us, 10_000_000, b"l" * 16), port.now_us)
    port.outcome = CommitOutcome.ACCEPTED
    receiver.pump(20, max_frames=0)
    assert len(link.sent) == forwarded + 1, "the retracted emission is re-offered"
    assert parse_frame(link.sent[-1])["grant_revision"] == 2, (
        "the revision it never spent is still available to it")
    assert receiver._grant_revision == 2


def test_dispatched_data_never_settles_a_control_emission():
    """The control lane settles on its own bytes, not on whatever leaves first."""
    port, link, peer_link, receiver, peer, _, limits = cross()
    link_b = _hold_a_competing_data_frame(port, receiver, limits)
    before = (receiver._grant_snapshot, receiver._grant_revision, receiver._last_ack_at)

    _at(0.1, port, link, peer_link, link_b)
    port.outcome = CommitOutcome.ACCEPTED
    assert port.dispatch().outcome is CommitOutcome.ACCEPTED
    assert link.sent == [], "the DATA went out on the other flow's link"
    assert peer._peer_credit == 4, "the peer's view is untouched by DATA dispatch"
    assert (receiver._grant_snapshot, receiver._grant_revision,
            receiver._last_ack_at) == before, "and so is this receiver's canonical state"


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_an_older_queued_ack_cannot_retire_newer_receive_work(outcome):
    """R8E-F01: an older ACK's handover cleared receive work raised after it.

    The peer sends its final frame while an ACK for the opening snapshot waits
    behind another legitimate flow. The credit is identical before and after -
    the configured byte capacity exceeds the slot cap - so nothing but the
    cumulative base and the selective receipt distinguishes the two statements.
    Equal credit does not make two acknowledgements equal, and committing the
    older one must not discard the news that a newer one is owed.
    """
    port, link, peer_link, receiver, peer, _, limits = cross()
    link_b = _queue_the_receivers_current_ack(port, link, receiver, limits)
    queued_snapshot = receiver._ack_pending[1]


    # The peer's remaining DATA arrives while the earlier ACK is still only
    # queued. The receiver has delivered nothing yet, so this completes it.
    for seq in (0, 1):
        final = parse_frame(build_data_frame(
            flow_id=1, generation=1, seq=seq, total=2, last=seq == 1,
            payload=b"Z" * QUANTUM, modulus=limits.seq_modulus, limits=limits))
        assert final is not None
        receiver._accept(final)
    assert receiver._expected == 2 and receiver.recv_state is FlowState.COMPLETE
    credit_now = receiver._credit()
    assert credit_now == queued_snapshot[2], (
        "the credit is identical, so only the base and receipt have moved")

    _at(0.1, port, link, peer_link, link_b)
    port.outcome = outcome
    assert port.dispatch().outcome is outcome
    assert receiver._grant_snapshot == queued_snapshot, (
        "the older emission still commits exactly the snapshot it stated")
    assert receiver._grant_revision == 2
    assert receiver._ack_dirty is True, (
        "and it leaves the newer receive work standing")
    after_handover = len(link.sent)
    assert after_handover == 1, "the older emission is the only one that left"

    # The receiver must now state the completion, not stay silent.
    _at(20.0, port, link, peer_link, link_b)
    port.ledger.observe(CounterSample(port.now_us, 10_000_000, b"l" * 16), port.now_us)
    port.outcome = CommitOutcome.ACCEPTED
    receiver.pump(20, max_frames=0)
    assert len(link.sent) == after_handover + 1, (
        "the newer acknowledgement really went out")
    newer = parse_frame(link.sent[-1])
    assert newer["ack_seq"] == 2, "it states the completed cumulative base"
    assert newer["grant_revision"] > 2, (
        "changed canonical state takes a revision above the queued emission")
    assert newer["credit"] == credit_now


def test_a_valid_small_modulus_ack_is_rebuilt_under_its_own_limits():
    """R8E-F02: the canonical rebuild invented limits the flow never had.

    The flow's own limits are valid: a window4/reassembly65 configuration over a
    256-slot sequence space satisfies every constructor rule. The rebuild then
    refuses that same emission anyway, because the credit it legitimately
    advertised is checked against the constructor's default reassembly ceiling of
    64 - a configuration that never applied to this flow. Only the recorded bytes
    may be compared, and the owning limits travel with the comparison.
    """
    small = FlowLimits(seq_modulus=256, window_frames=4, initial_credit_frames=4,
                       max_reassembly_frames=65, max_delivered_bytes=65536,
                       max_queued_bytes=65536, max_total_frames=4096)
    frame = build_ack_frame(flow_id=1, generation=1, ack_seq=3, credit=65, sack=0,
                            modulus=small.seq_modulus, limits=small, grant_revision=5)

    with pytest.raises(FlowError, match="credit out of range"):
        build_ack_from_wire(frame, small.seq_modulus)
    assert build_ack_from_wire(frame, small.seq_modulus, limits=small) == frame, (
        "under the owning limits the same bytes canonicalise to themselves")


def test_a_small_modulus_flow_settles_its_queued_ack_without_fencing():
    """R8E-F02: the callback match must not fence an otherwise definite outcome.

    A callback that cannot rebuild the emission it is settling leaves the owner
    holding an unresolved attempt. Completion and retraction both have to match
    the exact recorded bytes of the flow that queued them. Here the flow's own
    reassembly ceiling is 65, so the credit it advertises is one above the
    constructor's default - and the defaulting rebuild cannot match its own
    emission while the owning limits match it exactly.
    """
    small = FlowLimits(seq_modulus=256, window_frames=4, initial_credit_frames=4,
                       max_reassembly_frames=65, max_delivered_bytes=65536,
                       max_queued_bytes=65536, max_total_frames=4096)
    link = SimLink(LinkProfile("f", 4000.0, one_way_delay_s=0.0),
                   LinkProfile("r", 4000.0, one_way_delay_s=0.0), seed=1)
    receiver = ReliableFlow("b", link, small, flow_id=1, generation=1,
                            receive=True, startup=StartupGrant(
                                credit_frames=remote_startup_credit(small)))
    receiver.grant_startup()
    assert receiver._credit() == 65, (
        "the flow legitimately advertises one frame above the default ceiling")

    # The exact bytes this flow emits are what the host callbacks compare.
    wire = build_ack_frame(flow_id=1, generation=1, ack_seq=receiver._expected,
                           credit=receiver._credit(), sack=receiver._sack_bitmap(),
                           modulus=small.seq_modulus, limits=small, grant_revision=2)
    assert build_ack_from_wire(wire, small.seq_modulus, limits=small) == wire, (
        "the owning limits match the emission exactly")
    with pytest.raises(FlowError, match="credit out of range"):
        build_ack_from_wire(wire, small.seq_modulus)

    # A re-handover of that exact emission settles it; the defaulting match
    # would have refused it and left the owner holding an unresolved attempt.
    receiver._ack_pending = (wire, (receiver._expected, receiver._sack_bitmap(),
                                    receiver._credit()), 2, False, receiver._ack_work_gen)
    assert receiver.ack_handover(wire, at=0.1) is True, (
        "the owning limits settle the emission the flow actually queued")
    assert receiver._ack_pending is None
    assert receiver._grant_snapshot == (0, 0, 65)
    assert receiver._grant_revision == 2

    # A frame this flow never emitted is still refused under those limits.
    foreign = build_ack_frame(flow_id=1, generation=1, ack_seq=1, credit=65, sack=0,
                              modulus=small.seq_modulus, limits=small, grant_revision=5)
    assert build_ack_from_wire(foreign, small.seq_modulus, limits=small) == foreign
    assert receiver.ack_handover_cancelled(foreign) is False
