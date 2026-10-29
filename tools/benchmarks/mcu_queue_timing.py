#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Summarize one endpoint's retained MCU queue trace without touching hardware.

Usage: python tools/benchmarks/mcu_queue_timing.py part-0-unit-b-RESULT.json

Accept an endpoint result, its metrics object, or its flow_snapshot. Inputs must
describe one host clock and one MCU boot; provenance is a caller claim, not
authenticated here. Public aggregate benchmark reports lack these raw events.
Output contains only counts and duration aggregates, never identifiers, hashes,
payloads, paths or absolute timestamps. Host and MCU intervals remain separate.
Observed sums overlap and MUST NOT be added to estimate total time or CPU cost.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from statistics import median

MAX_BYTES = 32 * 1024 * 1024
MAX_EVENTS = 100_000
STAGES = ('QUEUED', 'ISSUED', 'STARTED', 'RF_DONE')
TERMINALS = ('RF_DONE', 'UNKNOWN', 'NOT_SENT', 'REFUSED')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def integer(value):
    return type(value) is int and 0 <= value < 2**64


def elapsed(start, end):
    if not integer(start) or not integer(end) or end < start:
        return None
    return end - start


def summary(values, clock):
    measured = [v for v in values if v is not None]
    return dict(clock=clock, measured_count=len(measured),
        missing_or_invalid_count=len(values) - len(measured),
        observed_sum_us=sum(measured) if measured else None,
        min_us=min(measured) if measured else None,
        median_us=median(measured) if measured else None,
        max_us=max(measured) if measured else None)


def strict_json_loads(data):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON object key')
            result[key] = value
        return result

    def finite_float(value):
        number = float(value)
        require(math.isfinite(number), 'nonfinite JSON number')
        return number

    def reject_constant(_value):
        raise ValueError('nonfinite JSON number')

    return json.loads(data, object_pairs_hook=unique_object,
                      parse_float=finite_float, parse_constant=reject_constant)


def analyze(document):
    require(type(document) is dict, 'expected one endpoint result or snapshot')
    metrics = document.get('metrics', document)
    require(type(metrics) is dict, 'invalid metrics object')
    snapshot = metrics.get('flow_snapshot', metrics)
    require(type(snapshot) is dict, 'invalid flow snapshot')
    queue = snapshot.get('physical_queue', {})
    require(type(queue) is dict, 'invalid physical queue snapshot')
    physical = metrics.get('physical_diagnostics', queue.get('events'))
    require(type(physical) is list and len(physical) <= MAX_EVENTS,
            'per-request physical trace required; aggregate reports are insufficient')
    flow = snapshot.get('events', [])
    require(type(flow) is list and len(flow) <= MAX_EVENTS, 'invalid flow events')
    records, invalid, diagnostics = {}, set(), Counter()
    submit_order, accept_order = [], []
    last_physical = None

    def keep(event, label):
        request = event.get('request')
        require(integer(request) and request > 0, 'invalid event request')
        row = records.setdefault(request, {})
        if label in row:
            # JSON distinguishes true, 1 and 1.0 even though Python equality
            # does not. A malformed replay must not disappear as a duplicate.
            if (json.dumps(row[label], sort_keys=True, allow_nan=False)
                    == json.dumps(event, sort_keys=True, allow_nan=False)):
                diagnostics['exact_duplicate_events'] += 1
            else:
                diagnostics['conflicting_duplicate_events'] += 1
                invalid.add(request)
        else:
            row[label] = event
            return True
        return False

    for event in physical:
        require(type(event) is dict, 'invalid physical event')
        kind = event.get('event')
        if kind == 'physical':
            stage = event.get('stage')
            require(stage in STAGES + TERMINALS, 'unrecognized physical stage')
            added = keep(event, stage)
        elif kind in ('submit', 'release', 'unknown'):
            added = keep(event, kind)
        else:
            raise ValueError('unrecognized physical event')
        if added:
            request, stamp = event['request'], event.get('host_mono_us')
            if (not integer(stamp) or last_physical is not None
                    and elapsed(last_physical[1], stamp) is None):
                diagnostics['physical_host_order_errors'] += 1
                invalid.add(request)
                if last_physical is not None:
                    invalid.add(last_physical[0])
            last_physical = request, stamp
            if kind == 'submit':
                submit_order.append(request)
    for event in flow:
        require(type(event) is dict, 'invalid flow event')
        if event.get('event') == 'tx_queued' and keep(event, 'accept'):
            accept_order.append(event['request'])

    # These lists retain capture order. Sorting clocks/IDs could hide resets or
    # a reordered capture. queue_data calls are serialized; their intervals
    # cannot overlap, even though they overlap the MCU's transmission work.
    for name, order in (('submission_order_errors', submit_order),
                        ('acceptance_order_errors', accept_order)):
        diagnostics[name] += 0
        for before, after in zip(order, order[1:]):
            if name == 'submission_order_errors':
                start = records[before]['submit'].get('host_mono_us')
                end = records[after]['submit'].get('host_mono_us')
            else:
                start = records[before]['accept'].get('queue_accept_mono_us')
                end = records[after]['accept'].get('queue_submit_mono_us')
            if after <= before or elapsed(start, end) is None:
                diagnostics[name] += 1
                invalid.update((before, after))
    diagnostics['physical_host_order_errors'] += 0
    diagnostics['submission_acceptance_sequence_mismatch'] = submit_order != accept_order

    counts = snapshot.get('counts', {})
    require(type(counts) is dict, 'invalid flow counts')
    expected = counts.get('tx_QUEUED')
    require(expected is None or integer(expected), 'invalid queued count')
    diagnostics['observed_requests'] = len(records)
    diagnostics['expected_queued_requests'] = expected
    diagnostics['expected_but_unobserved_requests'] = (
        max(0, expected - len(records)) if expected is not None else None)
    diagnostics['exact_duplicate_events'] += 0
    diagnostics['conflicting_duplicate_events'] += 0
    diagnostics['requests_with_missing_stages'] = 0
    diagnostics['requests_with_invalid_clock_order'] = 0
    diagnostics['requests_with_failure'] = 0
    diagnostics['requests_with_invalid_acceptance'] = 0
    diagnostics['requests_with_invalid_release'] = 0
    for request, row in records.items():
        if any(name not in row for name in ('submit', *STAGES, 'release')):
            diagnostics['requests_with_missing_stages'] += 1
        if any(name in row for name in ('UNKNOWN', 'NOT_SENT', 'REFUSED', 'unknown')):
            diagnostics['requests_with_failure'] += 1
            invalid.add(request)
        # Check clocks independently. No host timestamp is compared with MCU time.
        bad = False
        for names, field in ((('submit', *STAGES, 'release'), 'host_mono_us'),
                             (STAGES, 'mcu_event_us')):
            stamps = [row[name].get(field) for name in names if name in row]
            if any(not integer(t) for t in stamps) or any(
                    b < a for a, b in zip(stamps, stamps[1:])):
                bad = True
        if bad:
            diagnostics['requests_with_invalid_clock_order'] += 1
            invalid.add(request)
        release, done = row.get('release', {}), row.get('RF_DONE', {})
        if release and (release.get('stage') != 'RF_DONE'
                        or not integer(release.get('mcu_event_us'))
                        or release.get('mcu_event_us') != done.get('mcu_event_us')):
            diagnostics['requests_with_invalid_release'] += 1
            invalid.add(request)

    def span(request, row, first, last, field):
        if request in invalid:
            return None
        return elapsed(row.get(first, {}).get(field), row.get(last, {}).get(field))

    host = {}
    for name, first, last in (
        ('post_reservation_submit_to_queued_report', 'submit', 'QUEUED'),
        ('settled_rf_done_report_to_confirmed_release', 'RF_DONE', 'release')):
        host[name] = summary([span(req, row, first, last, 'host_mono_us')
                              for req, row in records.items()], 'host_monotonic')
    acceptance, pre_submit = [], []
    for req, row in records.items():
        event = row.get('accept', {})
        start, end = event.get('queue_submit_mono_us'), event.get('queue_accept_mono_us')
        value = elapsed(start, end)
        post_reserve = row.get('submit', {}).get('host_mono_us')
        queued_report = row.get('QUEUED', {}).get('host_mono_us')
        if (value is None or not integer(event.get('queue_accept_elapsed_us'))
                or value != event.get('queue_accept_elapsed_us')
                or not integer(post_reserve) or not integer(queued_report)
                or not start <= post_reserve <= queued_report <= end):
            diagnostics['requests_with_invalid_acceptance'] += 1
            value = None
        if req in invalid:
            value = None
        acceptance.append(value)
        pre_submit.append(elapsed(start, row.get('submit', {}).get('host_mono_us'))
                          if value is not None else None)
    host['queue_call_to_acceptance'] = summary(acceptance, 'host_monotonic')
    host['queue_call_to_post_reservation_submit'] = summary(pre_submit, 'host_monotonic')

    mcu = {}
    for first, last in zip(STAGES, STAGES[1:]):
        mcu[first.lower() + '_to_' + last.lower()] = summary(
            [span(req, row, first, last, 'mcu_event_us') for req, row in records.items()],
            'mcu_monotonic')

    # Only consecutive unique owner frame IDs support nominal four-frame grouping.
    # Retransmits, a missing submit, or a cropped prefix make grouping unavailable.
    frames = [records[req]['submit'].get('owner_frame_id') for req in submit_order]
    grouping = (bool(frames) and len(submit_order) == len(records)
                and all(integer(v) and v > 0 for v in frames)
                and frames == list(range(1, len(frames) + 1)))
    groups = dict(available=grouping, basis='nominal consecutive owner frames, groups of four')
    gaps_complete = False
    if grouping:
        ordered = [(req, records[req]) for req in submit_order]
        within, between = [], []
        for (prev_req, prev), (req, row) in zip(ordered, ordered[1:]):
            gap = None if req in invalid or prev_req in invalid else elapsed(
                prev.get('RF_DONE', {}).get('mcu_event_us'),
                row.get('ISSUED', {}).get('mcu_event_us'))
            (between if (row['submit']['owner_frame_id'] - 1) % 4 == 0 else within).append(gap)
        groups['within_group_rf_done_to_next_issued'] = summary(within, 'mcu_monotonic')
        groups['between_groups_rf_done_to_next_issued'] = summary(between, 'mcu_monotonic')
        gaps_complete = all(gap is not None for gap in within + between)

    physical_complete = (bool(records) and expected == len(records) and not invalid
                         and not diagnostics['requests_with_missing_stages']
                         and type(queue.get('pending')) is int and queue['pending'] == 0
                         and type(queue.get('worker_pending')) is int and queue['worker_pending'] == 0)
    complete = (physical_complete and all(v is not None for v in acceptance) and gaps_complete
                and not diagnostics['submission_acceptance_sequence_mismatch'])
    return dict(schema='meshticulum.mcu-queue-timing.v1', trace_complete=complete,
        diagnostics=dict(diagnostics), host_intervals=host, mcu_intervals=mcu,
        nominal_four_frame_groups=groups,
        limits=[
            'Single host and MCU boot are caller claims; capture provenance is not authenticated.',
            'Completeness concerns recorded events, not successful delivery or hardware qualification.',
            'Observed sums may overlap; do not add them or subtract them from end-to-end time.',
            'Submit is logged after reservation; terminal host report is logged after durable settlement.',
            'USB, journal commit, CPU, RX guards and ACK wait costs are not isolated by these events.',
        ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace', type=Path, help='one retained endpoint result or snapshot JSON')
    args = parser.parse_args()
    try:
        with args.trace.open('rb') as stream:
            data = stream.read(MAX_BYTES + 1)
        require(len(data) <= MAX_BYTES, 'input exceeds 32 MiB')
        report = analyze(strict_json_loads(data))
    except (OSError, UnicodeError, ValueError, RecursionError):
        # Do not echo paths or arbitrary capture values on malformed/private input.
        parser.exit(2, 'Cannot analyze input: expected a bounded single-endpoint queue trace.\n')
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
