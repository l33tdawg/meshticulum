# SPDX-License-Identifier: Apache-2.0
"""Retained traces cannot turn missing stages or clock offsets into measured costs."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'mcu_queue_timing', ROOT / 'tools/benchmarks/mcu_queue_timing.py')
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


def capture(count=5, host_offset=1_000_000, mcu_offset=40):
    physical, flow = [], []
    for frame in range(1, count + 1):
        host, mcu = host_offset + frame * 200, mcu_offset + frame * 200
        common = dict(request=frame, packet_sha256='private-digest', token='private-token')
        physical.append(dict(common, event='submit', host_mono_us=host,
                             owner_frame_id=frame))
        for stage, at in [('QUEUED', 10), ('ISSUED', 20), ('STARTED', 30), ('RF_DONE', 130)]:
            physical.append(dict(common, event='physical', stage=stage,
                                 host_mono_us=host + at, mcu_event_us=mcu + at))
        physical.append(dict(common, event='release', stage='RF_DONE',
                             host_mono_us=host + 140, mcu_event_us=mcu + 130))
        flow.append(dict(common, event='tx_queued', queue_submit_mono_us=host - 5,
                         queue_accept_mono_us=host + 10, queue_accept_elapsed_us=15))
    return dict(metrics=dict(physical_diagnostics=physical, flow_snapshot=dict(
        events=flow, counts={'tx_QUEUED': count},
        physical_queue=dict(events=physical, pending=0, worker_pending=0))))


def test_independent_clock_offsets_and_metadata_only_output():
    report = TOOL.analyze(capture())
    assert report == TOOL.analyze(capture(host_offset=7, mcu_offset=9_000_000_000))
    assert report['trace_complete']
    assert report['host_intervals']['queue_call_to_acceptance']['observed_sum_us'] == 75
    assert report['mcu_intervals']['started_to_rf_done']['observed_sum_us'] == 500
    groups = report['nominal_four_frame_groups']
    assert groups['within_group_rf_done_to_next_issued']['measured_count'] == 3
    assert groups['between_groups_rf_done_to_next_issued']['observed_sum_us'] == 90
    text = json.dumps(report)
    assert 'private-digest' not in text and 'private-token' not in text


def test_missing_stage_preserves_partial_measurement_and_missing_count():
    data = capture()
    events = data['metrics']['physical_diagnostics']
    events[:] = [e for e in events if not (e['request'] == 2 and e.get('stage') == 'STARTED')]
    report = TOOL.analyze(data)
    assert not report['trace_complete']
    span = report['mcu_intervals']['started_to_rf_done']
    assert span['measured_count'] == 4 and span['missing_or_invalid_count'] == 1
    assert span['observed_sum_us'] == 400
    assert report['diagnostics']['requests_with_missing_stages'] == 1


def test_exact_duplicates_deduplicate_but_conflicts_invalidate_request():
    data = capture()
    events = data['metrics']['physical_diagnostics']
    events.append(copy.deepcopy(events[1]))
    report = TOOL.analyze(data)
    assert report['trace_complete'] and report['diagnostics']['exact_duplicate_events'] == 1
    events[-1]['mcu_event_us'] += 1
    report = TOOL.analyze(data)
    assert not report['trace_complete']
    assert report['diagnostics']['conflicting_duplicate_events'] == 1
    assert report['mcu_intervals']['queued_to_issued']['measured_count'] == 4


@pytest.mark.parametrize('mutation', ['backwards', 'failure', 'count', 'pending', 'missing_frame',
                                     'acceptance', 'release', 'cross_request_clock_reset'])
def test_bad_or_incomplete_trace_cannot_claim_completeness(mutation):
    data = capture()
    metrics = data['metrics']
    if mutation == 'backwards':
        metrics['physical_diagnostics'][2]['mcu_event_us'] = 1
    elif mutation == 'failure':
        metrics['physical_diagnostics'][4]['stage'] = 'UNKNOWN'
    elif mutation == 'count':
        metrics['flow_snapshot']['counts']['tx_QUEUED'] += 1
    elif mutation == 'pending':
        metrics['flow_snapshot']['physical_queue']['pending'] = 1
    elif mutation == 'missing_frame':
        metrics['physical_diagnostics'][0]['owner_frame_id'] = 8
    elif mutation == 'acceptance':
        metrics['flow_snapshot']['events'][0]['queue_accept_elapsed_us'] = 1
    elif mutation == 'release':
        metrics['physical_diagnostics'][5]['mcu_event_us'] += 1
    else:
        for event in metrics['physical_diagnostics']:
            if event['request'] == 3 and 'mcu_event_us' in event:
                event['mcu_event_us'] -= 400
    assert not TOOL.analyze(data)['trace_complete']


def test_snapshot_input_and_aggregate_report_refusal():
    data = capture()
    assert TOOL.analyze(data) == TOOL.analyze(data['metrics']['flow_snapshot'])
    public = json.loads((ROOT / 'benchmarks/2026-10-24-mcu-queue-lora/results.json').read_text())
    with pytest.raises(ValueError, match='per-request physical trace required'):
        TOOL.analyze(public)


def test_cross_request_host_clock_reset_is_not_hidden_by_valid_local_spans():
    data = capture()
    metrics = data['metrics']
    for event in metrics['physical_diagnostics']:
        if event['request'] == 3:
            event['host_mono_us'] -= 400
    accept = metrics['flow_snapshot']['events'][2]
    accept['queue_submit_mono_us'] -= 400
    accept['queue_accept_mono_us'] -= 400
    report = TOOL.analyze(data)
    assert not report['trace_complete']
    assert report['diagnostics']['physical_host_order_errors'] > 0
    assert report['diagnostics']['submission_order_errors'] > 0
    assert report['diagnostics']['acceptance_order_errors'] > 0


@pytest.mark.parametrize('stream', ['physical', 'acceptance'])
def test_reordered_capture_is_not_silently_sorted(stream):
    data = capture()
    if stream == 'physical':
        events = data['metrics']['physical_diagnostics']
        events[:12] = events[6:12] + events[:6]
    else:
        events = data['metrics']['flow_snapshot']['events']
        events[0], events[1] = events[1], events[0]
    report = TOOL.analyze(data)
    assert not report['trace_complete']
    assert report['diagnostics']['submission_acceptance_sequence_mismatch']


def test_boolean_release_timestamp_cannot_match_integer_one():
    data = capture(count=1)
    events = data['metrics']['physical_diagnostics']
    for event in events:
        if 'mcu_event_us' in event:
            event['mcu_event_us'] = 1
    assert TOOL.analyze(data)['trace_complete']
    events[-1]['mcu_event_us'] = True
    report = TOOL.analyze(data)
    assert not report['trace_complete']
    assert report['diagnostics']['requests_with_invalid_release'] == 1
    events[-1]['mcu_event_us'] = 1
    replay = copy.deepcopy(events[-1])
    replay['mcu_event_us'] = True
    events.append(replay)
    report = TOOL.analyze(data)
    assert not report['trace_complete']
    assert report['diagnostics']['conflicting_duplicate_events'] == 1


@pytest.mark.parametrize('bad_prefix', [
    '"private-key":1,"private-key":2,',
    '"private-key":NaN,', '"private-key":Infinity,',
    '"private-key":-Infinity,', '"private-key":1e9999,',
])
def test_cli_rejects_ambiguous_json_without_private_output(tmp_path, bad_prefix):
    payload = '{' + bad_prefix + json.dumps(capture())[1:]
    assert_cli_refused(tmp_path, payload)


def assert_cli_refused(tmp_path, payload):
    path = tmp_path / 'private-input-name.json'
    path.write_text(payload)
    result = subprocess.run([sys.executable, str(ROOT / 'tools/benchmarks/mcu_queue_timing.py'),
                             str(path)], capture_output=True, text=True)
    assert result.returncode == 2
    assert result.stdout == ''
    assert result.stderr == ('Cannot analyze input: expected a bounded '
                             'single-endpoint queue trace.\n')


def test_cli_rejects_excessive_nesting_without_traceback(tmp_path):
    assert_cli_refused(tmp_path, '{"private-key":' + '[' * 3000 + '0' + ']' * 3000 + '}')
