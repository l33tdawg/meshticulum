# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Deterministic MT04 pacing witnesses; no radio, runtime, clock or broker."""

from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meshticulum.contracts.errors import ContractError
from meshticulum.contracts.negotiation import CommitOutcome
from meshticulum.scheduler.budget import (
    BudgetCheckpoint, BudgetPolicy, Cost, CounterSample, Ledger, MAX_TIME_US,
    MAX_USAGE_US, MAX_WINDOW_US, Reservation,
)


EPOCH = b"e" * 16
NEW_EPOCH = b"n" * 16
POLICY = BudgetPolicy(100, 60, 90, 50, quantization_us=1)


def ready(policy=POLICY, *, tx_us=100, start_us=0):
    ledger = Ledger(policy, EPOCH, start_us)
    ledger.observe(CounterSample(start_us, tx_us, EPOCH), start_us)
    return ledger


def rejected(code, call):
    with pytest.raises(ContractError, match="^" + code + "$"):
        call()


def test_startup_needs_a_fresh_matching_counter_before_any_reservation():
    ledger = Ledger(POLICY, EPOCH, 0)
    rejected("no_sample", lambda: ledger.reserve(Cost(1, 1), 0))
    rejected("future_sample", lambda: ledger.observe(CounterSample(1, 0, EPOCH), 0))
    assert ledger.snapshot(0)["pending_reservations"] == 0
    ledger.observe(CounterSample(0, 0, EPOCH), 0)
    assert ledger.validate(ledger.reserve(Cost(1, 1), 0), 0) is True


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
def test_acceptance_and_uncertainty_charge_full_cost_once(outcome):
    ledger = ready()
    handle = ledger.reserve(Cost(20, 35), 1)
    assert ledger.snapshot(1)["own_used_us"] == 21
    assert ledger.snapshot(1)["shared_used_us"] == 36
    ledger.settle(handle, outcome, 1)
    state = ledger.snapshot(1)
    assert state["estimated_own_us"] == 20
    assert state["estimated_shared_us"] == 35
    assert state["pending_reservations"] == 0
    rejected("reservation_not_live", lambda: ledger.settle(handle, outcome, 2))
    assert ledger.snapshot(1) == state


@pytest.mark.parametrize("outcome", [CommitOutcome.REFUSED_SCHEDULER, CommitOutcome.REFUSED_CARRIER])
def test_known_pre_send_refusal_refunds_without_any_attempt_estimate(outcome):
    ledger = ready()
    before = ledger.snapshot(0)
    handle = ledger.reserve(Cost(20, 35), 0)
    ledger.settle(handle, outcome, 0)
    assert ledger.snapshot(0) == before


def test_own_and_shared_medium_limits_have_discriminating_controls():
    own = ready()
    h = own.reserve(Cost(50, 50), 1)
    own.settle(h, CommitOutcome.ACCEPTED, 1)
    rejected("own_budget", lambda: own.reserve(Cost(10, 10), 2))
    assert own.validate(own.reserve(Cost(9, 9), 2), 2)
    shared = ready()
    h = shared.reserve(Cost(10, 80), 1)
    shared.settle(h, CommitOutcome.ACCEPTED, 1)
    rejected("shared_budget", lambda: shared.reserve(Cost(10, 10), 2))
    assert shared.snapshot(2)["own_remaining_us"] == 49
    assert shared.validate(shared.reserve(Cost(1, 9), 2), 2)


def test_exact_cached_sample_never_clears_post_sample_attempts():
    ledger = ready()
    handle = ledger.reserve(Cost(20, 35), 1)
    ledger.settle(handle, CommitOutcome.ACCEPTED, 1)
    before = ledger.snapshot(1)
    ledger.observe(CounterSample(0, 100, EPOCH), 1)
    assert ledger.snapshot(1) == before
    assert before["unsampled_own_us"] == 20
    ledger.observe(CounterSample(2, 120, EPOCH), 2)
    after = ledger.snapshot(2)
    assert after["unsampled_own_us"] == 0
    assert after["estimated_own_us"] == 20
    assert after["own_used_us"] == 41
    assert after["shared_used_us"] == 56


def test_new_read_captured_before_last_attempt_cannot_reconcile_that_attempt():
    ledger = ready()
    handle = ledger.reserve(Cost(20, 35), 10)
    ledger.settle(handle, CommitOutcome.ACCEPTED, 10)
    ledger.observe(CounterSample(5, 100, EPOCH), 10)
    state = ledger.snapshot(10)
    assert state["unsampled_own_us"] == 20
    assert state["own_used_us"] == 21
    assert state["estimated_shared_us"] == 35


def test_new_counter_read_does_not_remove_open_or_total_estimated_costs():
    ledger = ready(replace(POLICY, max_reservations=2))
    committed = ledger.reserve(Cost(10, 20), 1)
    ledger.settle(committed, CommitOutcome.UNKNOWN, 1)
    open_handle = ledger.reserve(Cost(10, 20), 1)
    ledger.observe(CounterSample(2, 100, EPOCH), 2)
    state = ledger.snapshot(2)
    assert state["estimated_own_us"] == 10
    assert state["pending_reservations"] == 1
    assert state["own_used_us"] == 21
    assert state["shared_used_us"] == 41
    assert ledger.validate(open_handle, 2)


def test_unmanaged_firmware_tx_conservatively_raises_shared_medium_usage():
    ledger = ready()
    h = ledger.reserve(Cost(10, 30), 1)
    ledger.settle(h, CommitOutcome.ACCEPTED, 1)
    ledger.observe(CounterSample(2, 140, EPOCH), 2)
    state = ledger.snapshot(2)
    assert state["own_used_us"] == 51
    assert state["shared_used_us"] == 71
    rejected("shared_budget", lambda: ledger.reserve(Cost(1, 20), 2))


def test_quantized_counter_has_an_explicit_conservative_padding_control():
    padded = ready(replace(POLICY, quantization_us=10))
    assert padded.snapshot(0)["own_used_us"] == 10
    rejected("own_budget", lambda: padded.reserve(Cost(51, 51), 0))
    assert padded.validate(padded.reserve(Cost(50, 50), 0), 0)


def test_sample_expiry_at_last_pre_send_boundary_keeps_cost_booked():
    ledger = ready()
    handle = ledger.reserve(Cost(10, 20), 49)
    assert ledger.validate(handle, 49)
    rejected("stale_sample", lambda: ledger.validate(handle, 50))
    assert ledger.snapshot(50)["pending_reservations"] == 1
    ledger.settle(handle, CommitOutcome.REFUSED_CARRIER, 50)
    assert ledger.snapshot(50)["estimated_own_us"] == 0
    rejected("stale_sample", lambda: ledger.reserve(Cost(1, 1), 50))
    ledger.observe(CounterSample(50, 100, EPOCH), 50)
    assert ledger.validate(ledger.reserve(Cost(10, 20), 50), 50)


def test_counter_budget_change_after_reservation_refuses_final_send_validation():
    ledger = ready()
    handle = ledger.reserve(Cost(20, 35), 1)
    assert ledger.validate(handle, 1)
    ledger.observe(CounterSample(2, 150, EPOCH), 2)
    rejected("own_budget", lambda: ledger.validate(handle, 2))
    assert ledger.snapshot(2)["pending_reservations"] == 1
    ledger.settle(handle, CommitOutcome.REFUSED_CARRIER, 2)
    assert ledger.snapshot(2)["own_used_us"] == 51


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
@pytest.mark.parametrize("bad_time", [None, True, 0.5, float("nan"), float("inf"), -1, 0])
def test_attempt_accounting_survives_failed_post_send_clock(outcome, bad_time):
    ledger = ready()
    handle = ledger.reserve(Cost(20, 35), 1)
    ledger.settle(handle, outcome, bad_time)
    state = ledger.snapshot(1)
    assert state["fenced"]
    assert state["pending_reservations"] == 0
    assert state["estimated_own_us"] == 20
    assert state["estimated_shared_us"] == 35
    rejected("ledger_fenced", lambda: ledger.reserve(Cost(1, 1), 1))


def test_fenced_or_expired_counter_still_allows_a_live_attempt_to_be_charged():
    ledger = ready()
    handle = ledger.reserve(Cost(20, 35), 1)
    rejected("counter_decreased", lambda: ledger.observe(CounterSample(2, 99, EPOCH), 2))
    ledger.settle(handle, CommitOutcome.UNKNOWN, 60)
    state = ledger.snapshot(60)
    assert state["fenced"] and not state["sample_fresh"]
    assert state["estimated_own_us"] == 20
    assert state["pending_reservations"] == 0


@pytest.mark.parametrize("failure,sample", [
    ("sample_conflict", CounterSample(0, 101, EPOCH)),
    ("counter_decreased", CounterSample(1, 99, EPOCH)),
    ("counter_epoch_changed", CounterSample(1, 100, NEW_EPOCH)),
])
def test_counter_faults_fence_until_new_instance_and_retain_open_cost(failure, sample):
    ledger = ready()
    handle = ledger.reserve(Cost(10, 20), 0)
    rejected(failure, lambda: ledger.observe(sample, 1))
    rejected("ledger_fenced", lambda: ledger.observe(CounterSample(2, 110, EPOCH), 2))
    state = ledger.snapshot(2)
    assert state["fenced"] and state["pending_reservations"] == 1
    ledger.settle(handle, CommitOutcome.UNKNOWN, 2)
    assert ledger.snapshot(2)["estimated_shared_us"] == 20


def test_clock_rollback_fences_without_reopening_budget():
    ledger = ready()
    handle = ledger.reserve(Cost(10, 20), 10)
    rejected("clock_regressed", lambda: ledger.validate(handle, 9))
    assert ledger.snapshot(10)["pending_reservations"] == 1
    ledger.settle(handle, CommitOutcome.UNKNOWN, 9)
    assert ledger.snapshot(10)["estimated_shared_us"] == 20


def test_older_sample_refuses_without_moving_current_counter_or_fences():
    ledger = ready()
    ledger.observe(CounterSample(10, 110, EPOCH), 10)
    before = ledger.snapshot(10)
    rejected("sample_reordered", lambda: ledger.observe(CounterSample(5, 100, EPOCH), 10))
    assert ledger.snapshot(10) == before


def test_window_boundary_never_erases_open_reservations():
    ledger = ready()
    handle = ledger.reserve(Cost(20, 35), 49)
    ledger.observe(CounterSample(100, 100, EPOCH), 100)
    state = ledger.snapshot(100)
    assert state["window_start_us"] == 100
    assert state["pending_reservations"] == 1
    assert state["own_used_us"] == 21 and state["shared_used_us"] == 36
    rejected("reservation_capacity", lambda: ledger.reserve(Cost(1, 1), 100))
    ledger.settle(handle, CommitOutcome.UNKNOWN, 100)
    assert ledger.snapshot(100)["estimated_shared_us"] == 35


def test_sample_timing_or_counter_progress_cannot_age_out_carrier_queue_debt():
    unsampled = ready()
    h = unsampled.reserve(Cost(20, 35), 1)
    unsampled.settle(h, CommitOutcome.UNKNOWN, 1)
    assert unsampled.snapshot(100)["estimated_shared_us"] == 35
    unsampled.observe(CounterSample(100, 120, EPOCH), 100)
    assert unsampled.snapshot(100)["estimated_shared_us"] == 35
    sampled = ready()
    h = sampled.reserve(Cost(20, 35), 1)
    sampled.settle(h, CommitOutcome.ACCEPTED, 1)
    sampled.observe(CounterSample(2, 120, EPOCH), 2)
    sampled.observe(CounterSample(100, 120, EPOCH), 100)
    assert sampled.snapshot(100)["estimated_shared_us"] == 35
    assert sampled.snapshot(100)["own_used_us"] == 21


@pytest.mark.parametrize("outcome", [CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN])
@pytest.mark.parametrize("settlement_time", [0, 1])
def test_literal_queued_attempt_cannot_reopen_budget_with_no_counter_progress(outcome, settlement_time):
    policy = BudgetPolicy(10_000_000, 6_000_000, 6_000_000, 10_000_000, quantization_us=1)
    ledger = ready(policy, tx_us=0)
    handle = ledger.reserve(Cost(5_000_000, 5_000_000), 0)
    ledger.settle(handle, outcome, settlement_time)
    ledger.observe(CounterSample(1, 0, EPOCH), 1)
    assert ledger.snapshot(1)["debt_own_us"] == 5_000_000
    ledger.observe(CounterSample(10_000_000, 0, EPOCH), 10_000_000)
    assert ledger.snapshot(10_000_000)["own_used_us"] == 5_000_001
    rejected("own_budget", lambda: ledger.reserve(Cost(5_000_000, 5_000_000), 10_000_000))
    checkpoint = ledger.checkpoint(10_000_000)
    restored = Ledger.restore(checkpoint, NEW_EPOCH, 0)
    restored.observe(CounterSample(0, 0, NEW_EPOCH), 0)
    restored.observe(CounterSample(10_000_000, 0, NEW_EPOCH), 10_000_000)
    assert restored.snapshot(10_000_000)["debt_own_us"] == 5_000_000
    rejected("own_budget", lambda: restored.reserve(Cost(5_000_000, 5_000_000), 10_000_000))


def test_same_settlement_tick_read_cannot_reconcile_unsampled_diagnostics():
    ledger = ready()
    h = ledger.reserve(Cost(10, 20), 1)
    ledger.settle(h, CommitOutcome.ACCEPTED, 1)
    ledger.observe(CounterSample(1, 110, EPOCH), 1)
    assert ledger.snapshot(1)["unsampled_own_us"] == 10
    ledger.observe(CounterSample(2, 110, EPOCH), 2)
    assert ledger.snapshot(2)["unsampled_own_us"] == 0
    assert ledger.snapshot(2)["debt_own_us"] == 10


def test_late_initial_sample_cannot_hide_prior_cumulative_tx_in_current_window():
    late = Ledger(POLICY, EPOCH, 0)
    late.observe(CounterSample(10, 100, EPOCH), 10)
    assert late.snapshot(10)["own_used_us"] == 101
    rejected("own_budget", lambda: late.reserve(Cost(1, 1), 10))
    aligned = ready(start_us=10)
    assert aligned.validate(aligned.reserve(Cost(1, 1), 10), 10)


def test_forged_copied_stale_or_wrong_epoch_handles_refuse_without_any_mutation():
    ledger = ready()
    handle = ledger.reserve(Cost(10, 20), 1)
    foreign = ready().reserve(Cost(10, 20), 0)
    before = ledger.snapshot(1)
    for forged, code in [(replace(handle), "reservation_not_live"),
                         (replace(handle, token=2), "reservation_not_live"),
                         (replace(handle, epoch=NEW_EPOCH), "reservation_scope_mismatch"),
                         (foreign, "reservation_scope_mismatch")]:
        rejected(code, lambda: ledger.settle(forged, CommitOutcome.ACCEPTED, 100))
        rejected(code, lambda: ledger.validate(forged, 100))
        assert ledger.snapshot(1) == before
    rejected("invalid_outcome", lambda: ledger.settle(handle, "attempted_unknown", 100))
    assert ledger.snapshot(1) == before
    ledger.settle(handle, CommitOutcome.ACCEPTED, 1)
    settled = ledger.snapshot(1)
    rejected("reservation_not_live", lambda: ledger.settle(handle, CommitOutcome.ACCEPTED, 100))
    assert ledger.snapshot(1) == settled


def test_checkpoint_restore_keeps_spend_and_retires_unresolved_tokens():
    source = ready()
    handle = source.reserve(Cost(20, 35), 1)
    checkpoint = source.checkpoint(1)
    recovered = Ledger.restore(checkpoint, NEW_EPOCH, 1)
    state = recovered.snapshot(1)
    assert state["estimated_own_us"] >= checkpoint.pending_own_us
    assert state["estimated_shared_us"] >= checkpoint.pending_shared_us
    assert state["recovered_unresolved_reservations"] == 1
    rejected("reservation_scope_mismatch", lambda: recovered.settle(handle, CommitOutcome.REFUSED_CARRIER, 1))
    recovered.observe(CounterSample(1, 0, NEW_EPOCH), 1)
    rejected("recovery_gate", lambda: recovered.reserve(Cost(1, 1), 100))
    rejected("stale_sample", lambda: recovered.reserve(Cost(1, 1), 101))
    recovered.observe(CounterSample(101, 0, NEW_EPOCH), 101)
    assert recovered.validate(recovered.reserve(Cost(1, 1), 101), 101)


def test_restart_with_spent_budget_cannot_reopen_the_same_window():
    source = ready()
    handle = source.reserve(Cost(50, 50), 1)
    source.settle(handle, CommitOutcome.UNKNOWN, 1)
    checkpoint = source.checkpoint(2)
    recovered = Ledger.restore(checkpoint, EPOCH, 2)
    recovered.observe(CounterSample(2, 150, EPOCH), 2)
    assert recovered.snapshot(2)["estimated_own_us"] >= 50
    rejected("recovery_gate", lambda: recovered.reserve(Cost(1, 1), 50))
    # Even a caller whose monotonic clock restarted waits a complete new window.
    reset_clock = Ledger.restore(checkpoint, NEW_EPOCH, 0)
    reset_clock.observe(CounterSample(0, 0, NEW_EPOCH), 0)
    rejected("recovery_gate", lambda: reset_clock.reserve(Cost(1, 1), 99))


def test_checkpoint_stays_bounded_and_payload_free_after_many_settlements():
    policy = BudgetPolicy(1000, 800, 900, 999, quantization_us=1)
    ledger = ready(policy)
    for _ in range(100):
        h = ledger.reserve(Cost(1, 2), 0)
        ledger.settle(h, CommitOutcome.ACCEPTED, 0)
    checkpoint = ledger.checkpoint(0)
    value = asdict(checkpoint)
    value["epoch"] = value["epoch"].hex()
    assert len(json.dumps(value).encode()) < 1024
    assert checkpoint.pending_reservations == 0
    assert set(ledger.snapshot(0)).isdisjoint({"epoch", "ledger_id", "token", "payload", "peer"})
    assert ledger.checkpoint(0) == checkpoint


def test_large_counter_and_recovery_usage_remain_integer_bounded():
    ledger = ready(tx_us=0)
    ledger.observe(CounterSample(1, MAX_TIME_US, EPOCH), 1)
    checkpoint = ledger.checkpoint(1)
    assert checkpoint.own_used_us <= MAX_USAGE_US
    recovered = Ledger.restore(checkpoint, NEW_EPOCH, 2)
    recovered.observe(CounterSample(1, MAX_TIME_US, NEW_EPOCH), 2)
    assert recovered.checkpoint(2).shared_used_us <= MAX_USAGE_US


def test_identity_policy_and_checkpoints_are_immutable_normal_values():
    ledger = ready()
    h = ledger.reserve(Cost(1, 2), 0)
    for value, field in [(h, "cost"), (POLICY, "window_us"),
                         (ledger.checkpoint(0), "own_used_us")]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, 0)
    with pytest.raises(AttributeError):
        ledger.epoch = NEW_EPOCH
    with pytest.raises(AttributeError):
        ledger.policy = replace(POLICY, own_tx_limit_us=90)


@pytest.mark.parametrize("bad", [None, True, False, "1", 1.0, 0, -1,
                                float("nan"), float("inf"), MAX_WINDOW_US + 1])
def test_costs_are_strict_positive_bounded_integer_microseconds(bad):
    with pytest.raises(ContractError):
        Cost(bad, MAX_WINDOW_US)
    with pytest.raises(ContractError):
        Cost(1, bad)


def test_shared_cost_cannot_omit_own_transmission_time():
    rejected("invalid_cost", lambda: Cost(2, 1))


@pytest.mark.parametrize("field", ["window_us", "own_tx_limit_us", "shared_limit_us",
                                  "sample_max_age_us", "quantization_us", "max_reservations"])
@pytest.mark.parametrize("bad", [True, 1.0, "1", 0, -1, float("nan"), float("inf"), 1 << 100])
def test_policy_fields_are_strict_bounded_counts(field, bad):
    with pytest.raises(ContractError):
        replace(POLICY, **{field: bad})


@pytest.mark.parametrize("epoch", [None, True, "e" * 16, bytes(16), b"e" * 15,
                                  b"e" * 17, bytearray(b"e" * 16)])
def test_epoch_is_an_exact_nonzero_incarnation_value(epoch):
    with pytest.raises(ContractError):
        CounterSample(0, 0, epoch)
    with pytest.raises(ContractError):
        Ledger(POLICY, epoch, 0)


@pytest.mark.parametrize("bad", [True, 1.0, "1", -1, float("nan"), float("inf"), MAX_TIME_US + 1])
def test_invalid_times_and_counter_values_are_refused(bad):
    with pytest.raises(ContractError):
        CounterSample(bad, 0, EPOCH)
    with pytest.raises(ContractError):
        CounterSample(0, bad, EPOCH)
    with pytest.raises(ContractError):
        Ledger(POLICY, EPOCH, bad)
    ledger = ready()
    with pytest.raises(ContractError):
        ledger.reserve(Cost(1, 1), bad)


@pytest.mark.parametrize("change", [
    {"own_used_us": True}, {"shared_used_us": -1}, {"pending_reservations": 2},
    {"pending_own_us": 0}, {"pending_shared_us": 0}, {"window_start_us": 2},
    {"shared_used_us": 0}, {"fenced": 1}, {"policy": "policy-ref"},
])
def test_checkpoint_aggregates_cannot_be_structurally_weakened(change):
    ledger = ready()
    ledger.reserve(Cost(10, 20), 1)
    with pytest.raises(ContractError):
        replace(ledger.checkpoint(1), **change)


def test_nested_type_aliases_and_checkpoint_dicts_are_not_admitted():
    class CostAlias(Cost):
        pass
    class SampleAlias(CounterSample):
        pass
    ledger = ready()
    rejected("invalid_cost", lambda: ledger.reserve(CostAlias(1, 1), 0))
    rejected("invalid_sample", lambda: ledger.observe(SampleAlias(0, 0, EPOCH), 0))
    rejected("invalid_checkpoint", lambda: Ledger.restore(asdict(ledger.checkpoint(0)), NEW_EPOCH, 0))
