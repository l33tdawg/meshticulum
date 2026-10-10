# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Independent source-only MT04 carrier arithmetic and sibling-flow witnesses.

All profiles, leases, clocks and counters are synthetic. These cases prove no
RF timing, firmware queue completion, legality, broker or serial integration.
"""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meshticulum.contracts.carrier import (
    CarrierKind, CarrierProfile, ProfileBasis, decode_text_frame, encode_text_frame,
)
from meshticulum.contracts.errors import ContractError
from meshticulum.contracts.identity import (
    DeadlineSet, FlowKey, PeerID, RouteBinding, ServiceID, ServiceKind, SessionKey,
)
from meshticulum.contracts.negotiation import (
    CapabilityLimits, CommitOutcome, Feature, NegotiatedContract, TrafficClass,
)
from meshticulum.scheduler.budget import BudgetPolicy, CounterSample, Ledger
from meshticulum.scheduler.scheduler import (
    DispatchGrant, LeaseScope, Scheduler, SchedulerPolicy, profile_cost,
)


def profile(forward=.25, ack=.5, reverse=.75, *, digest=b"p" * 32,
            header=13, authentication=16):
    return CarrierProfile(CarrierKind.TEXT, 128, header, authentication, digest,
                          2, 1, 8, forward, ack, reverse, 0, 1000,
                          ProfileBasis.SOURCE_FIXTURE)


@pytest.mark.parametrize("components,own,shared", [
    ((.25, .5, .75), 250_000, 1_500_000),
    ((.0000001, .0000001, .0000001), 1, 3),
    ((.000001, 0, 0), 1, 1),
    ((.000001000001, 0, 0), 2, 2),
    ((1.0000000001, 2.0000000001, 3.0000000001), 1_000_001, 6_000_003),
    ((.333333333, .666666667, .000000001), 333_334, 1_000_002),
    ((3600, 3600, 3600), 3_600_000_000, 10_800_000_000),
])
def test_all_components_ceil_individually_and_own_tx_stays_separate(components, own, shared):
    cost = profile_cost(profile(*components))
    assert cost.own_tx_us == own
    assert cost.shared_medium_us == shared
    assert type(cost.own_tx_us) is int and type(cost.shared_medium_us) is int


@pytest.mark.parametrize("components", [(0, 0, 0), (0, 1, 1)])
def test_zero_own_tx_profile_cannot_create_a_free_dispatch_cost(components):
    with pytest.raises(ContractError, match="^invalid_cost$"):
        profile_cost(profile(*components))


def test_text_sizing_accounts_for_explicit_header_and_authentication():
    p = profile()
    assert p.payload_capacity == 64
    # The whole raw frame, including the 29 asserted overhead bytes, fits:
    # 4 prefix bytes + 4 * ceil(93 / 3) = 128 ASCII bytes.
    raw = b"h" * 13 + b"a" * 16 + b"d" * 64
    text = encode_text_frame(raw)
    assert len(text.encode("ascii")) == 128
    assert decode_text_frame(text).payload == raw
    with pytest.raises(ContractError):
        encode_text_frame(raw + b"d")
    assert replace(p, authentication_bytes=17).payload_capacity == 63


class SiblingRig:
    def __init__(self):
        self.local = PeerID("source-local", "a" * 64)
        self.peer = PeerID("source-peer", "b" * 64)
        self.session = SessionKey(self.local, self.peer, b"s" * 16, b"g" * 16)
        policy = BudgetPolicy(10_000_000_000, 10_000_000_000,
                              10_000_000_000, 10_000_000_000, quantization_us=1)
        self.ledger = Ledger(policy, b"e" * 16, 0)
        self.ledger.observe(CounterSample(0, 0, b"e" * 16), now_us=0)
        self.scheduler = Scheduler(local_peer=self.local, ledger=self.ledger,
            epoch=b"t" * 16, broker_epoch=b"b" * 16,
            policy=SchedulerPolicy(max_frame_cost_us=8_000_000))
        self.sent = []

    def add(self, identifier, cost, ack=0, reverse=0):
        p = profile(cost, ack, reverse, digest=bytes([identifier]) * 32)
        route = RouteBinding(self.session, ServiceID(self.peer, ServiceKind.SAGE),
            "12345678-1234-4234-8234-123456789abc", b"c" * 32, p.profile_digest, 1)
        limits = CapabilityLimits(64, 64, 64, 64, 1, 4096, 6, 8, 2, 4096, 32)
        contract = NegotiatedContract(1, frozenset({Feature.BYTE_CREDIT,
            Feature.SELECTIVE_ACK_32, Feature.INCREMENTAL_STREAM, Feature.HALF_CLOSE}),
            limits, route, DeadlineSet(1000, 1000, 2000), TrafficClass.INTERACTIVE,
            1, bytes([identifier]) * 16)
        flow = FlowKey(self.session, self.local, identifier)
        self.scheduler.open_flow(flow, contract, p, now_us=0, now_epoch=100)
        self.scheduler.enqueue(flow, b"d", now_us=0, now_epoch=100)
        return flow

    def lease(self, scope):
        @contextmanager
        def grant(request):
            yield DispatchGrant(request, scope, request.deadlines, 1, b"b" * 16)
        return grant

    def dispatch(self):
        def send(frame):
            self.sent.append(frame.request.flow)
            return CommitOutcome.ACCEPTED
        return self.scheduler.dispatch_one(authorize=self.lease(LeaseScope.SERVICE),
            broker=self.lease(LeaseScope.BROKER), send=send,
            monotonic_us=lambda: 0, epoch_clock=lambda: 100)


def test_frame_bound_uses_complete_shared_cost_before_queue_allocation():
    rig = SiblingRig()
    rig.add(1, 4, 3, 1)  # Exactly the local eight-second frame-cost ceiling.
    before = rig.scheduler.snapshot()
    with pytest.raises(ContractError, match="^scheduler_frame_cost_limit$"):
        rig.add(2, 4, 3, 1.0000001)  # Forward fits; complete cost exceeds by1us.
    assert rig.scheduler.snapshot() == before


def test_costly_sibling_keeps_progress_under_continuous_cheaper_sibling_load():
    rig = SiblingRig()
    expensive, cheap = rig.add(1, 8), rig.add(2, 1)
    counts = {expensive: 0, cheap: 0}
    longest_cheap_run = current_cheap_run = 0
    for _ in range(80):
        result = rig.dispatch()
        assert result.outcome is CommitOutcome.ACCEPTED
        flow = rig.sent[-1]
        counts[flow] += 1
        current_cheap_run = current_cheap_run + 1 if flow == cheap else 0
        longest_cheap_run = max(longest_cheap_run, current_cheap_run)
        rig.scheduler.enqueue(flow, b"d", now_us=0, now_epoch=100)
        assert rig.scheduler.snapshot()["queued_frames"] == 2
    # Flow rotation is by opportunities within this peer, while peer/class
    # fairness debits airtime. There is no equal per-flow airtime claim.
    assert counts[expensive] >= 30 and counts[cheap] >= 30
    assert abs(counts[expensive] - counts[cheap]) <= 2
    assert longest_cheap_run <= 2
    expected_spend = counts[expensive] * 8_000_000 + counts[cheap] * 1_000_000
    assert rig.ledger.snapshot(0)["estimated_shared_us"] == expected_spend
