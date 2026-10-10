# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Meaningful trace-boundary controls; no devices, credentials or network."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("meshticulum_path_report", Path(__file__).resolve().parents[1] /
                                           "scripts/lantern/benchmarks/meshticulum_path_report.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def trace(index=1):
    clock = "c" * 64
    row = {"schema": REPORT.SCHEMA, "run_id": f"run-{index}", "layer": "sage_federation",
           "operation": "QueryPeer", "temperature": "cold", "provenance": "source_fixture",
           "outcome": "complete", "bindings": {name: "a" * 64 for name in REPORT.BINDINGS},
           "intervals": {name: None for name in REPORT.INTERVALS},
           "counters": {name: None for name in REPORT.COUNTERS},
           "authorization_budget_ns": 5_000_000_000}
    row["bindings"]["clock_sha256"] = clock
    row["intervals"]["total"] = {"clock_sha256": clock, "start_ns": 0, "end_ns": index * 8_000_000_000}
    row["intervals"]["post_status_authorization"] = {
        "clock_sha256": clock, "start_ns": 2_000_000_000, "end_ns": index * 8_000_000_000}
    row["counters"]["application_bytes"] = index * 800
    return row


def test_failed_attempts_missing_energy_and_authorization_overrun_remain_visible():
    failed = trace(2)
    failed["outcome"] = "timeout"
    report = REPORT.summarize([trace(), failed])
    group = report["groups"][0]
    assert group["reported_complete_count"] == 1
    assert group["failed_or_incomplete_count"] == 1
    assert group["runs"][1]["outcome"] == "timeout"
    assert group["intervals_ns"]["total"]["median"] == 8_000_000_000
    assert group["counters"]["energy_uj"]["median"] is None
    assert "energy_uj" in group["missing_complete_metrics"]["counters"]
    assert group["reported_application_goodput_Bps"]["median"] == 100
    assert group["authorization_budget_checks"][0]["within_reported_budget"] is False
    assert report["physical_acceptance"] is False
    assert report["authenticated_sage_acceptance"] is False
    assert report["rf_completion_established"] is False
    assert report["p95_estimated"] is False


@pytest.mark.parametrize("field", ["layer", "operation", "temperature", "provenance",
                                   "authorization_budget_ns", *REPORT.BINDINGS])
def test_incomparable_work_is_never_pooled(field):
    changed = trace(2)
    if field in REPORT.BINDINGS:
        changed["bindings"][field] = "b" * 64
        if field == "clock_sha256":
            for span in changed["intervals"].values():
                if span is not None:
                    span["clock_sha256"] = "b" * 64
    else:
        changed[field] = {"layer": "https", "operation": "HTTPSGET", "temperature": "warm",
                          "provenance": "reported_observation", "authorization_budget_ns": 20_000_000_000}[field]
    assert len(REPORT.summarize([trace(), changed])["groups"]) == 2


@pytest.mark.parametrize("fault", ["clock", "backwards", "boolean_time", "fractional_counter",
                                   "negative_counter", "nan_counter", "extra_private_field",
                                   "nested_private_field", "missing_metric_field", "outside_total",
                                   "boolean_budget", "zero_budget", "non_digest_binding"])
def test_untrusted_trace_cannot_expand_the_schema_or_forge_clock_arithmetic(fault):
    row = trace()
    if fault == "clock":
        row["intervals"]["total"]["clock_sha256"] = "b" * 64
    elif fault == "backwards":
        row["intervals"]["total"]["start_ns"] = 9_000_000_000
    elif fault == "boolean_time":
        row["intervals"]["total"]["start_ns"] = False
    elif fault == "fractional_counter":
        row["counters"]["application_bytes"] = 1.5
    elif fault == "negative_counter":
        row["counters"]["application_bytes"] = -1
    elif fault == "nan_counter":
        row["counters"]["application_bytes"] = float("nan")
    elif fault == "extra_private_field":
        row["payload"] = "must-never-appear-in-report"
    elif fault == "nested_private_field":
        row["intervals"]["total"]["token"] = "must-never-appear-in-report"
    elif fault == "missing_metric_field":
        del row["counters"]["energy_uj"]
    elif fault == "outside_total":
        row["intervals"]["post_status_authorization"]["end_ns"] = 9_000_000_000
    elif fault == "boolean_budget":
        row["authorization_budget_ns"] = True
    elif fault == "zero_budget":
        row["authorization_budget_ns"] = 0
    elif fault == "non_digest_binding":
        row["bindings"]["authority_evidence_sha256"] = "not-a-provenance-proof"
    with pytest.raises(ValueError):
        REPORT.summarize([row])


def test_no_total_or_zero_duration_does_not_invent_a_goodput_result():
    for total in (None, {"clock_sha256": "c" * 64, "start_ns": 0, "end_ns": 0}):
        row = trace()
        row["intervals"] = {name: None for name in REPORT.INTERVALS}
        row["intervals"]["total"] = total
        result = REPORT.summarize([row])["groups"][0]
        assert result["reported_application_goodput_Bps"]["samples"] == 0
        assert result["reported_application_goodput_Bps"]["median"] is None
        assert result["authorization_budget_checks"][0]["within_reported_budget"] is None


def test_overlapping_stage_intervals_are_not_added_into_total():
    row = trace()
    row["intervals"]["tls_handshake"] = copy.deepcopy(row["intervals"]["total"])
    row["intervals"]["request_response"] = copy.deepcopy(row["intervals"]["total"])
    result = REPORT.summarize([row])["groups"][0]
    assert result["intervals_ns"]["total"]["median"] == 8_000_000_000


def test_duplicate_requests_are_not_double_counted():
    with pytest.raises(ValueError, match="duplicate_run"):
        REPORT.summarize([trace(), trace()])


@pytest.mark.parametrize("raw", [b'{"schema":1,"schema":2}', b'[NaN]', b'[Infinity]'])
def test_duplicate_json_keys_and_nonfinite_numbers_are_refused_at_file_boundary(tmp_path, raw):
    path = tmp_path / "input.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        REPORT.load(path)


def test_file_size_and_run_count_have_hard_bounds(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b" " * (REPORT.MAX_INPUT_BYTES + 1))
    with pytest.raises(ValueError, match="input_size"):
        REPORT.load(path)
    with pytest.raises(ValueError, match="run_count"):
        REPORT.summarize([trace()] * (REPORT.MAX_RUNS + 1))


def test_report_roundtrip_has_no_nonfinite_values():
    report = REPORT.summarize([trace()])
    assert json.loads(json.dumps(report, allow_nan=False)) == report
