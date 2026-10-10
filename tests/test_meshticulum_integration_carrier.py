# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Independent carrier outcomes at the source-only flow-owned commit seam.

The shared owner has independent credit, sequence and retry rules. Its typed
lease is a simulation, not a production flow, broker or physical carrier proof.
No worker return, ACK, counter timestamp or host close reclaims airtime debt.
"""

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_meshticulum_flow_scheduler_integration import IntegrationRig
from meshticulum.contracts.errors import ContractError
from meshticulum.contracts.negotiation import CommitOutcome
from meshticulum.scheduler.budget import CounterSample, Ledger


def assert_attempted_once(rig, flow, outcome):
    assert len(rig.calls) == len(rig.owner.commit_calls) == 1
    assert rig.owner.commit_calls[0].outcome is outcome
    assert rig.owner.commit_calls[0].reservation is rig.calls[0].request.flow_reservation
    assert rig.owner.oracle(flow) == {
        "credit": 5, "next_sequence": 8, "rf_attempts": 1,
        "retry_deadline_us": 102_000_000, "live_handles": 0, "terminal_callbacks": 1,
    }
    budget = rig.budget()
    assert budget["debt_own_us"] == 750_000
    assert budget["debt_shared_us"] == 1_500_000
    assert budget["pending_reservations"] == 0


def assert_unsent(rig, flow):
    assert rig.owner.oracle(flow) == {
        "credit": 8, "next_sequence": 7, "rf_attempts": 0,
        "retry_deadline_us": None, "live_handles": 0, "terminal_callbacks": 1,
    }
    budget = rig.budget()
    assert budget["debt_own_us"] == budget["debt_shared_us"] == 0
    assert budget["pending_reservations"] == 0


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_attempted_debt_survives_counter_window_host_close_and_restore(outcome):
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    rig.outcome = outcome
    result = rig.dispatch()
    assert result.invoked and result.flow_committed and not result.flow_fenced
    assert_attempted_once(rig, flow, outcome)

    # The cumulative counter never moves. A strictly newer reading at a new
    # window does not prove this carrier-admitted attempt physically finished.
    rig.now_us = rig.latest_us = 161_000_000
    sample = CounterSample(rig.now_us, 10_000_000, b"l" * 16)
    rig.ledger.observe(sample, rig.now_us)
    rig.ledger.observe(sample, rig.now_us)
    assert rig.budget()["window_start_us"] == 160_000_000
    assert_attempted_once(rig, flow, outcome)
    before_close = rig.budget()
    assert rig.scheduler.close() == 0
    assert rig.scheduler.close() == 0
    assert rig.scheduler.snapshot()["closed"] is True
    assert rig.budget() == before_close
    assert len(rig.owner.commit_calls) == 1

    checkpoint = rig.ledger.checkpoint(rig.now_us)
    restored = Ledger.restore(checkpoint, b"r" * 16, 200_000_000)
    after_recovery_window = restored.snapshot(260_000_001)
    assert after_recovery_window["debt_own_us"] == 750_000
    assert after_recovery_window["debt_shared_us"] == 1_500_000
    assert after_recovery_window["pending_reservations"] == 0
    assert after_recovery_window["sample_fresh"] is False


@pytest.mark.parametrize("invalid", [None, True, False, "confirmed_carrier_acceptance",
    {"outcome": "confirmed_carrier_acceptance"}, CommitOutcome.REFUSED_SCHEDULER])
def test_invalid_carrier_outcome_is_unknown_with_one_full_attempt(invalid):
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    rig.outcome = invalid
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.UNKNOWN and result.invoked
    assert result.flow_committed and not result.flow_fenced
    assert_attempted_once(rig, flow, CommitOutcome.UNKNOWN)
    assert rig.scheduler.snapshot()["queued_frames"] == 0
    assert rig.dispatch().outcome is None
    assert_attempted_once(rig, flow, CommitOutcome.UNKNOWN)


@pytest.mark.parametrize("error", [RuntimeError("synthetic carrier unavailable"),
    ContractError("synthetic_attempted_callback_error")])
def test_exception_after_carrier_invocation_never_becomes_unsent(error):
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    rig.outcome = error
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.UNKNOWN and result.invoked
    assert result.flow_committed and not result.flow_fenced
    assert_attempted_once(rig, flow, CommitOutcome.UNKNOWN)
    assert rig.dispatch().outcome is None
    assert_attempted_once(rig, flow, CommitOutcome.UNKNOWN)


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
@pytest.mark.parametrize("scope", ["service", "broker", "owner"])
def test_post_send_lease_exit_failure_cannot_refund_or_repeat_attempt(outcome, scope):
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    rig.outcome = outcome
    def fail_exit():
        raise RuntimeError("synthetic post-send exit failure")
    if scope == "owner":
        rig.owner.on_lease_exit = fail_exit
    else:
        setattr(rig, "on_" + scope + "_exit", fail_exit)
    result = rig.dispatch()
    assert result.outcome is outcome and result.invoked and result.flow_committed
    assert result.post_send_checks_passed is False
    assert result.flow_fenced is (scope == "owner")
    assert_attempted_once(rig, flow, outcome)
    rig.scheduler.close()
    assert_attempted_once(rig, flow, outcome)
    assert rig.scheduler.snapshot()["queued_frames"] == 0


def test_confirmed_carrier_refusal_preserves_exact_unsent_eligibility():
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    first = rig.owner.states[flow].live
    binding = first.binding
    rig.outcome = CommitOutcome.REFUSED_CARRIER
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_CARRIER and result.invoked
    assert result.flow_committed and not result.flow_fenced
    assert_unsent(rig, flow)
    assert rig.owner.commit_calls[0].reservation is first
    assert rig.owner.states[flow].binding is binding
    assert rig.dispatch().outcome is None
    assert len(rig.calls) == len(rig.owner.commit_calls) == 1

    # Only explicit owner preparation resubmits. It keeps the same original
    # protocol frame, sequence, attempt ordinal, eligibility and deadlines.
    rig.enqueue(flow)
    second = rig.owner.states[flow].live
    assert second is not first and second.binding is binding
    assert second.binding.frame_id == 101 and second.binding.sequence == 7
    assert second.binding.attempt_ordinal == 1
    assert second.binding.retry_not_before_us == 100_000_000
    assert second.binding.original_deadlines == binding.original_deadlines
    rig.outcome = CommitOutcome.ACCEPTED
    assert rig.dispatch().flow_committed
    assert rig.owner.oracle(flow) == {
        "credit": 5, "next_sequence": 8, "rf_attempts": 1,
        "retry_deadline_us": 102_000_000, "live_handles": 0, "terminal_callbacks": 2,
    }
    assert rig.budget()["debt_shared_us"] == 1_500_000


def test_missing_sample_is_no_send_and_explicit_fresh_admission_keeps_identity():
    rig = IntegrationRig(sample=False)
    flow = rig.open()
    rig.enqueue(flow)
    binding = rig.owner.states[flow].live.binding
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert result.flow_committed and not result.flow_fenced
    assert rig.calls == []
    assert_unsent(rig, flow)
    assert rig.owner.states[flow].binding is binding
    assert rig.dispatch().outcome is None
    assert len(rig.owner.commit_calls) == 1

    rig.ledger.observe(CounterSample(rig.now_us, 10_000_000, b"l" * 16), rig.now_us)
    rig.enqueue(flow)
    assert rig.owner.states[flow].live.binding is binding
    assert rig.dispatch().outcome is CommitOutcome.ACCEPTED
    assert len(rig.calls) == 1 and len(rig.owner.commit_calls) == 2
    assert rig.owner.states[flow].credit == 5
    assert rig.owner.states[flow].next_sequence == 8
    assert rig.owner.states[flow].rf_attempts == 1
    assert rig.budget()["debt_shared_us"] == 1_500_000


@pytest.mark.parametrize("scope", ["service", "broker"])
def test_pre_send_adapter_exception_is_returned_without_rf_credit_or_debt(scope):
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    def fail_before_send():
        raise RuntimeError("synthetic policy unavailable")
    setattr(rig, "on_" + scope, fail_before_send)
    result = rig.dispatch()
    assert result.outcome is CommitOutcome.REFUSED_SCHEDULER and not result.invoked
    assert result.flow_committed and not result.flow_fenced
    assert rig.calls == []
    assert_unsent(rig, flow)
    assert len(rig.owner.commit_calls) == 1
    rig.scheduler.close()
    assert len(rig.owner.commit_calls) == 1


@pytest.mark.parametrize("phase", ["before", "after"])
def test_completion_keyboard_interrupt_retains_unknown_debt_and_never_repeats(phase):
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    handle = rig.owner.states[flow].live
    rig.outcome = CommitOutcome.UNKNOWN
    real_dispatch_lease = rig.owner.dispatch_lease
    completion_entries = []
    def interrupting_lease(reservation):
        lease = real_dispatch_lease(reservation)
        real_commit = lease.commit
        def interrupted_commit(request):
            completion_entries.append(request)
            if phase == "after":
                real_commit(request)
            raise KeyboardInterrupt("synthetic completion interruption")
        lease.commit = interrupted_commit
        return lease
    rig.owner.dispatch_lease = interrupting_lease
    with pytest.raises(KeyboardInterrupt, match="synthetic completion interruption"):
        rig.dispatch()
    assert len(rig.calls) == len(completion_entries) == 1
    assert completion_entries[0].reservation is handle
    assert completion_entries[0].outcome is CommitOutcome.UNKNOWN
    budget = rig.budget()
    assert budget["debt_own_us"] == 750_000
    assert budget["debt_shared_us"] == 1_500_000
    assert budget["pending_reservations"] == 0
    state = rig.scheduler.snapshot()  # Also proves the busy guard was released.
    assert state["queued_frames"] == 0
    assert state["fenced_flows"] == state["unresolved_attempts"] == 1
    assert rig.dispatch().outcome is None
    rig.scheduler.close()
    assert len(completion_entries) == 1
    assert rig.budget()["debt_shared_us"] == 1_500_000
    if phase == "after":
        assert_attempted_once(rig, flow, CommitOutcome.UNKNOWN)
    else:
        assert rig.owner.oracle(flow) == {
            "credit": 8, "next_sequence": 7, "rf_attempts": 0,
            "retry_deadline_us": None, "live_handles": 1, "terminal_callbacks": 0,
        }


def test_owner_lease_exit_keyboard_interrupt_after_commit_fences_attempt():
    rig = IntegrationRig()
    flow = rig.open()
    rig.enqueue(flow)
    rig.outcome = CommitOutcome.UNKNOWN
    def interrupt_exit():
        raise KeyboardInterrupt("synthetic flow lease interruption")
    rig.owner.on_lease_exit = interrupt_exit
    with pytest.raises(KeyboardInterrupt, match="synthetic flow lease interruption"):
        rig.dispatch()
    assert_attempted_once(rig, flow, CommitOutcome.UNKNOWN)
    state = rig.scheduler.snapshot()
    assert state["queued_frames"] == 0
    assert state["fenced_flows"] == state["unresolved_attempts"] == 1
