#!/usr/bin/env python3
"""Summarize bounded metadata-only backhaul traces, without making requests.

Each interval belongs to one observer's monotonic clock. Missing measurements
stay missing, failures stay in the report, and different source/profile/path/
authority/clock bindings are never pooled. Input provenance is a caller claim;
this tool does not authenticate the capture or qualify a physical transport.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics

SCHEMA = "meshticulum.path-trace.v1"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_RUNS = 1000
MAX_INTEGER = 2**63 - 1
LAYERS = ("bare_carrier", "broker", "protected_message", "https", "sage_federation",
          "verified_resource")
INTERVALS = ("total", "protected_identity", "protected_storage", "broker_dispatch",
             "broker_capture", "tls_handshake", "post_status_authorization",
             "request_response", "budget_wait", "wake_wait")
COUNTERS = ("application_bytes", "handshake_tx_bytes", "handshake_rx_bytes",
            "request_tx_bytes", "response_rx_bytes", "forward_frames", "reverse_frames",
            "retries", "forward_airtime_us", "reverse_airtime_us", "peak_rss_bytes",
            "energy_uj")
BINDINGS = ("source_manifest_sha256", "profile_sha256", "path_evidence_sha256",
            "authority_evidence_sha256", "clock_sha256")
FIELDS = {"schema", "run_id", "layer", "operation", "temperature", "provenance",
          "outcome", "bindings", "intervals", "counters", "authorization_budget_ns"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def integer(value):
    return type(value) is int and 0 <= value <= MAX_INTEGER


def exact_fields(value, fields, reason):
    require(type(value) is dict and set(value) == set(fields), reason)


def validate(row):
    exact_fields(row, FIELDS, "trace_schema")
    require(row["schema"] == SCHEMA, "trace_version")
    for name in ("run_id", "operation"):
        require(type(row[name]) is str and ID.fullmatch(row[name]) is not None,
                "trace_identifier")
    for name, choices in (("layer", LAYERS), ("temperature", ("cold", "warm")),
                          ("provenance", ("source_fixture", "reported_observation")),
                          ("outcome", ("complete", "refused", "timeout", "cancelled", "unknown"))):
        require(type(row[name]) is str and row[name] in choices, "trace_category")
    exact_fields(row["bindings"], BINDINGS, "trace_bindings")
    require(all(type(value) is str and DIGEST.fullmatch(value) is not None
                for value in row["bindings"].values()), "trace_binding_digest")
    exact_fields(row["intervals"], INTERVALS, "trace_intervals")
    for span in row["intervals"].values():
        if span is None:
            continue
        exact_fields(span, ("clock_sha256", "start_ns", "end_ns"), "interval_schema")
        require(span["clock_sha256"] == row["bindings"]["clock_sha256"], "interval_clock_mismatch")
        require(integer(span["start_ns"]) and integer(span["end_ns"]), "interval_integer")
        require(span["end_ns"] >= span["start_ns"], "interval_backwards")
    total = row["intervals"]["total"]
    if total is not None:
        require(all(span is None or total["start_ns"] <= span["start_ns"] <=
                    span["end_ns"] <= total["end_ns"]
                    for span in row["intervals"].values()), "interval_outside_total")
    exact_fields(row["counters"], COUNTERS, "trace_counters")
    require(all(value is None or integer(value) for value in row["counters"].values()),
            "counter_integer")
    budget = row["authorization_budget_ns"]
    require(budget is None or (integer(budget) and budget > 0), "authorization_budget")
    return row


def elapsed(span):
    return None if span is None else span["end_ns"] - span["start_ns"]


def statistics_for(values):
    values = sorted(value for value in values if value is not None)
    return {"samples": len(values), "median": statistics.median(values) if values else None,
            "minimum": min(values) if values else None, "maximum": max(values) if values else None}


def summarize(rows):
    require(type(rows) is list and 0 < len(rows) <= MAX_RUNS, "run_count")
    groups = {}
    seen = set()
    for row in rows:
        validate(row)
        require(row["run_id"] not in seen, "duplicate_run")
        seen.add(row["run_id"])
        fields = ("layer", "operation", "temperature", "provenance", "authorization_budget_ns")
        key = tuple(row[name] for name in fields) + tuple(row["bindings"][name] for name in BINDINGS)
        groups.setdefault(key, []).append(row)
    result = []
    for key, runs in groups.items():
        complete = [row for row in runs if row["outcome"] == "complete"]
        intervals = {name: statistics_for(elapsed(row["intervals"][name]) for row in complete)
                     for name in INTERVALS}
        counters = {name: statistics_for(row["counters"][name] for row in complete)
                    for name in COUNTERS}
        rates = []
        authorization = []
        for row in runs:
            duration = elapsed(row["intervals"]["post_status_authorization"])
            budget = row["authorization_budget_ns"]
            authorization.append({"run_id": row["run_id"], "reported_duration_ns": duration,
                                  "reported_budget_ns": budget,
                                  "within_reported_budget": None if duration is None or budget is None
                                  else duration <= budget})
            total = elapsed(row["intervals"]["total"])
            application_bytes = row["counters"]["application_bytes"]
            if row["outcome"] == "complete" and total is not None and total > 0 and application_bytes is not None:
                rates.append(application_bytes * 1e9 / total)
        result.append({"binding": dict(zip(fields + BINDINGS, key)), "runs": runs,
                       "reported_complete_count": len(complete),
                       "failed_or_incomplete_count": len(runs) - len(complete),
                       "intervals_ns": intervals, "counters": counters,
                       "reported_application_goodput_Bps": statistics_for(rates),
                       "authorization_budget_checks": authorization,
                       "missing_complete_metrics": {
                           "intervals": [name for name, value in intervals.items() if value["samples"] == 0],
                           "counters": [name for name, value in counters.items() if value["samples"] == 0]}})
    return {"schema": "meshticulum.path-report.v1", "groups": result,
            "input_provenance_authenticated": False, "physical_acceptance": False,
            "authenticated_sage_acceptance": False, "rf_completion_established": False,
            "speedup_claimed": False, "p95_estimated": False,
            "scope": "Offline arithmetic on caller-reported metadata. No private payloads or free-form detail fields. Intervals may overlap and are never summed into total latency. Reported completion, digests, frame counts and budget checks do not authenticate a path, content, caller authority, radio completion, or regulatory compliance."}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("nonfinite_json_number")


def load(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    require(len(raw) <= MAX_INPUT_BYTES, "input_size")
    try:
        rows = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("input_json") from error
    return rows, hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        rows, digest = load(args.evidence)
        report = summarize(rows)
        report["input_sha256"] = digest
    except ValueError as error:
        parser.error(str(error))
    encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.write_text(encoded)


if __name__ == "__main__":
    main()
