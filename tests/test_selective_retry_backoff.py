"""R11 source controls: loss of an early repair is not two expired timers.

The synthetic carrier supplies explicit accepted SACKs and backlog bounds.
Real-port controls use the existing owner and scheduler with a modeled carrier;
none of these tests measures RF delivery or physical performance.
"""
from types import SimpleNamespace

import pytest

from meshticulum.flow.flow import FlowLimits, ReliableFlow, StartupGrant
from tests.test_flow_selective_fast_retry import Link, ack
from tests.test_selective_retry_adapter import at, receipt, selective_gap, transfer


class BacklogLink(Link):
    def __init__(self):
        super().__init__()
        self.backlog_bytes = 0

    def profile(self, endpoint):
        return SimpleNamespace(capacity_bytes_per_second=1_000_000)

    def queued_bytes(self, endpoint):
        return self.backlog_bytes


def eight_frame_hole(*, enabled=True, initial_rto=2.22, max_retries=4):
    link = BacklogLink()
    limits = FlowLimits(frame_payload_bytes=4, window_frames=8,
        initial_credit_frames=8, max_reassembly_frames=8, max_total_frames=64,
        max_queued_bytes=512, max_delivered_bytes=512, initial_rto_s=initial_rto,
        min_rto_s=2.22, max_rto_s=15, max_retries=max_retries,
        selective_fast_retry=enabled)
    flow = ReliableFlow('a', link, limits, startup=StartupGrant(8))
    flow.send(bytes(range(36)))
    flow.pump(0, max_frames=8, drain=False)
    ack(flow, positions=tuple(range(1, 8)), credit=1, at=.5)
    assert len(link.frames) == 8 and set(flow._in_flight) == {0}
    return flow, link, flow._in_flight[0]


@pytest.mark.parametrize('backlog_us', [0, 239640, 2_000_000])
def test_lost_fast_repair_waits_existing_rto_after_all_queued_work(backlog_us):
    flow, link, pending = eight_frame_hole()
    link.backlog_bytes = backlog_us
    assert flow.retry_selective_gap(.7)
    assert pending.attempts == 2 and pending.rto == 2.22
    assert pending.sent_at == pytest.approx(.7 + backlog_us / 1e6)
    due = .7 + backlog_us / 1e6 + 2.22
    for when, revision in ((.8, 2), (1.2, 3), (1.7, 4)):
        ack(flow, positions=tuple(range(1, 8)), credit=1, revision=revision, at=when)
        assert not flow.retry_selective_gap(when)
    flow.pump(due - .000001, max_frames=0, drain=False)
    assert len(link.frames) == 9 and pending.attempts == 2
    flow.pump(due + .000001, max_frames=0, drain=False)
    assert len(link.frames) == 10 and pending.attempts == 3
    assert pending.rto == 4.44
    assert flow.retransmits == 2 and flow.fast_retransmits == flow.fast_retry_offers == 1
    assert flow._next_seq == 8 and flow.queued_bytes() == 4 and flow._peer_credit == 1


def test_fast_preserves_existing_flight_interval_not_new_adaptive_global_rto():
    flow, link, pending = eight_frame_hole(initial_rto=4.0)
    assert flow.rto == 2.22 and pending.rto == 4.0
    assert flow.retry_selective_gap(.7)
    assert pending.rto == 4.0
    flow.pump(4.699999, max_frames=0, drain=False)
    assert len(link.frames) == 9
    flow.pump(4.700001, max_frames=0, drain=False)
    assert len(link.frames) == 10 and pending.rto == 8.0


@pytest.mark.parametrize('enabled', [False, True])
def test_ordinary_timeout_still_doubles_when_no_early_offer_is_made(enabled):
    flow, link, pending = eight_frame_hole(enabled=enabled)
    flow.pump(2.219999, max_frames=0, drain=False)
    assert len(link.frames) == 8 and pending.rto == 2.22
    flow.pump(2.220001, max_frames=0, drain=False)
    assert len(link.frames) == 9 and pending.attempts == 2 and pending.rto == 4.44
    assert flow.fast_retransmits == flow.fast_retry_offers == 0
    assert not flow.retry_selective_gap(2.3)


def test_late_cumulative_ack_retires_lost_repair_timer_without_new_data():
    flow, link, _ = eight_frame_hole()
    assert flow.retry_selective_gap(.7)
    ack(flow, base=8, positions=(), credit=8, revision=3, at=2.9)
    assert flow.in_flight() == 0
    flow.pump(3.0, max_frames=0, drain=False)
    assert len(link.frames) == 9 and flow.retransmits == 1
    assert flow.queued_bytes() == 4 and flow._next_seq == 8
    assert not flow.retry_selective_gap(3.0)


def test_retry_ceiling_after_lost_fast_repair_still_bounds_transmissions():
    flow, link, pending = eight_frame_hole(max_retries=2)
    assert flow.retry_selective_gap(.7)
    flow.pump(2.919999, max_frames=0, drain=False)
    assert flow.failure is None and pending.attempts == 2
    flow.pump(2.920001, max_frames=0, drain=False)
    assert flow.failure == 'retry_ceiling' and len(link.frames) == 9


def test_real_port_lost_fast_then_timeout_spend_exactly_two_retry_attempts():
    case = transfer(enabled=True)
    pending = selective_gap(case)
    original_rto = pending.rto
    initial_debt = case.port.ledger.snapshot(case.port.now_us)['debt_own_us']
    assert case.flow.retry_selective_gap(case.link.now)
    assert pending.rto == original_rto and pending.attempts == 2
    assert case.port.owner.rf_attempts == len(case.port.carrier_calls) == 5
    due = pending.sent_at + original_rto
    at(case, due - .000001)
    case.flow.pump(case.link.now, max_frames=0)
    assert len(case.port.carrier_calls) == 5
    at(case, due + .000001)
    case.flow.pump(case.link.now, max_frames=0)
    assert case.port.owner.rf_attempts == len(case.port.carrier_calls) == 6
    assert pending.attempts == 3 and pending.rto == original_rto * 2
    assert case.flow.retransmits == 2 and case.flow.fast_retransmits == 1
    assert case.port.ledger.snapshot(case.port.now_us)['debt_own_us'] == (
        initial_debt + 2 * case.port.owner.cost.own_tx_us)
    receipt(case, base=4, revision=3)
    assert case.flow.in_flight() == 0
    assert case.port.ledger.snapshot(case.port.now_us)['debt_own_us'] == (
        initial_debt + 2 * case.port.owner.cost.own_tx_us)
