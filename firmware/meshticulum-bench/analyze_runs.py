"""Summarize collected physical-run evidence, retaining failures and null metrics.

This checks consistency of operator-collected records, not independent hardware
attestation. It never fabricates a sample from source tests or a missing file.
One-way latency and measured airtime/energy remain unavailable without their
instruments. Reported transfer latency uses the sender's one monotonic clock.
"""
import argparse
import json
import math
from pathlib import Path
import statistics


def need(ok, code):
    if not ok: raise ValueError(code)


def attempts(collection):
    rows = collection['attempts']
    need(type(rows) is list and len(rows) <= 256, 'invalid_attempt_count')
    seen = set()
    counts, reserve, elapsed = {}, 0, []
    physical = set()
    for row in rows:
        key = row['request']
        need(type(key) is int and key > 0 and key not in seen, 'duplicate_or_invalid_attempt_request')
        seen.add(key)
        need(type(row['reserved_estimated_us']) is int and row['reserved_estimated_us'] > 0,
             'invalid_airtime_reservation')
        reserve += row['reserved_estimated_us']
        outcome = row['outcome']
        need(outcome in ('RF_DONE', 'REFUSED', 'NOT_SENT', 'UNKNOWN', 'RESERVED'), 'invalid_attempt_outcome')
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == 'RF_DONE':
            evidence = row['driver_evidence']
            need(type(evidence) is dict and evidence.get('irq', 0) & 1
                 and not evidence.get('irq', 0) & 0x200 and evidence.get('error') == 0,
                 'rf_done_without_driver_evidence')
            duration = evidence.get('driver_elapsed_us', evidence.get('host_driver_elapsed_us'))
            need(type(duration) is int and duration >= 0, 'missing_driver_elapsed_time')
            elapsed.append(duration)
            physical.add(row['packet_sha256'])
    return {'outcomes': counts, 'reserved_estimated_airtime_us': reserve,
            'driver_operation_elapsed_us': elapsed}, physical


def one(plan, collections):
    sender, receiver = plan['sender'], plan['receiver']
    need(sender != receiver and set(collections) == {sender, receiver}, 'wrong_collection_pair')
    result = {'run_id': plan['run_id'], 'sender': sender, 'receiver': receiver, 'object': plan['object'],
        'completed': False, 'sender_start_to_flow_ack_seconds': None,
        'sender_acknowledged_goodput_bytes_per_second': None, 'one_way_latency_seconds': None,
        'measured_airtime_seconds': None, 'measured_energy_joules': None, 'measured_eirp_dbm': None,
        'attempts': {}, 'counts': {}, 'matched_data_rf_packets': 0, 'matched_ack_rf_packets': 0}
    physical = {}
    for unit, collection in collections.items():
        need(collection['schema'] == 'meshticulum.collected-run.v1' and collection['unit'] == unit
             and collection['run_id'] == plan['run_id'], 'foreign_run_collection')
        need(collection['runtime_archive_sha256'] == plan['runtime_archive_sha256']
             and collection['runtime_manifest_sha256'] == plan['runtime_manifest_sha256'], 'runtime_pin_mismatch')
        provision = collection['provision']
        need(provision is not None and provision['unit'] == unit and provision['run_id'] == plan['run_id']
             and provision['package_sha256'] == plan['staged'][unit]['sha256']
             and provision['object'] == plan['object'], 'unbound_provision_receipt')
        need(provision['receiver_payload_installed'] is False, 'receiver_payload_copied_over_ip')
        grant = collection['grant']
        need(grant['unit'] == unit and grant['session_id'] == plan['run_id']
             and grant['profile'] == plan['profile'] and grant['object'] == plan['object']
             and grant['sender_unit'] == sender, 'grant_binding_mismatch')
        result['attempts'][unit], physical[unit] = attempts(collection)
        observation = collection['result']
        if observation is not None:
            need(observation['unit'] == unit and observation['host_boot_id'] == grant['host_boot_id'],
                 'wrong_observed_host')
            if 'session_id' in observation:
                need(observation['session_id'] == plan['run_id'] and observation['object'] == plan['object'],
                     'wrong_observed_session')
            result['counts'][unit] = observation.get('flow', {}).get('counts', {})
            result.setdefault('flow_retransmits', {})[unit] = observation.get('flow', {}).get('retransmits')
        result.setdefault('collection_stable', {})[unit] = collection['attempt_snapshot_stable']
        result.setdefault('errors', {})[unit] = None if observation is None else observation.get('error')
        result.setdefault('resources', {})[unit] = None if observation is None else observation.get('resource')
    tx, rx = collections[sender]['result'], collections[receiver]['result']
    if tx is None or rx is None:
        result['status'] = 'incomplete_run_evidence'
        return result
    need(tx.get('sends', True) is True and rx.get('sends', False) is False, 'wrong_endpoint_direction')
    if tx.get('grant_context_sha256') is not None and rx.get('grant_context_sha256') is not None:
        need(tx['grant_context_sha256'] == rx['grant_context_sha256'], 'different_peer_grant_contexts')
    for source, target, kind, label in ((sender, receiver, 'DATA', 'matched_data_rf_packets'),
                                        (receiver, sender, 'ACK', 'matched_ack_rf_packets')):
        outgoing = {event['packet_sha256'] for event in collections[source]['result'].get('flow', {}).get('events', [])
                    if event['event'] == 'tx' and event['outer_kind'] == kind and event['outcome'] == 'RF_DONE'}
        incoming = {event['packet_sha256'] for event in collections[target]['result'].get('flow', {}).get('events', [])
                    if event['event'] == 'rx' and event['outer_kind'] == kind}
        need(outgoing <= physical[source], 'tx_event_missing_durable_driver_attempt')
        result[label] = len(outgoing & incoming)
    done = tx['complete'] and rx['complete'] and all(result['collection_stable'].values())
    if done:
        need(rx.get('chunk_durable') is True and rx.get('verified_chunk_sha256') == plan['object']['sha256']
             and rx['flow']['received_bytes'] == plan['object']['bytes']
             and tx.get('disarmed_on_exit') is True and rx.get('disarmed_on_exit') is True,
             'completed_without_verified_chunk_and_containment')
        if plan.get('object_manifest') is not None:
            stored = collections[receiver].get('object_receipt', {})
            need(collections[receiver].get('object_manifest') == plan['object_manifest']
                 and stored.get('object_id') == plan['object']['object_id']
                 and plan['object']['chunk_index'] in stored.get('verified_chunks', []),
                 'completed_chunk_not_present_at_collection')
        expected_frames = math.ceil(plan['object']['bytes'] / 193)
        need(result['matched_data_rf_packets'] >= expected_frames and result['matched_ack_rf_packets'] > 0
             and tx['flow']['acked_frames'] == expected_frames,
             'completed_without_correlated_rf_path')
        need(not any(values['outcomes'].get('UNKNOWN', 0) or values['outcomes'].get('RESERVED', 0)
                     for values in result['attempts'].values()), 'completed_with_unknown_attempt')
        duration = (tx['flow_complete_monotonic_us'] - tx['start_monotonic_us']) / 1000000
        need(math.isfinite(duration) and 0 < duration <= 240, 'invalid_sender_duration')
        result.update(completed=True, status='completed', sender_start_to_flow_ack_seconds=duration,
            sender_acknowledged_goodput_bytes_per_second=plan['object']['bytes'] / duration)
    else:
        result['status'] = 'failed_or_interrupted'
    return result


def distribution(values):
    if not values: return None
    values = sorted(values)
    return {'n': len(values), 'min': values[0], 'median': statistics.median(values),
            'p95_nearest_rank': values[math.ceil(.95 * len(values)) - 1], 'max': values[-1]}


def aggregate(runs):
    valid = [r for r in runs if r.get('status') != 'invalid_evidence']
    complete = [r for r in valid if r['completed']]
    return {'schema': 'meshticulum.benchmark-summary.v1', 'runs': runs, 'recorded_runs': len(runs),
        'valid_evidence_runs': len(valid), 'completed_runs': len(complete),
        'completion_fraction_of_recorded_runs': len(complete) / len(runs) if runs else None,
        'sender_start_to_flow_ack_seconds': distribution([r['sender_start_to_flow_ack_seconds'] for r in complete]),
        'sender_acknowledged_goodput_bytes_per_second': distribution(
            [r['sender_acknowledged_goodput_bytes_per_second'] for r in complete]),
        'measured_airtime_seconds': None, 'measured_energy_joules': None, 'one_way_latency_seconds': None,
        'limits': ['Results require actual operator-collected hardware records; parser acceptance alone is not RF proof.',
            'Latency includes sender startup and receipt of reliable-flow ACK; it does not prove peer durable commit time.',
            'Receiver durable chunk hash is independently required for completed samples.',
            'Source airtime reservations and driver operation elapsed time are not measured RF airtime.',
            'Failed, interrupted and invalid records remain in the recorded-run denominator.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directories', nargs='+', type=Path,
                   help='Each contains RUN.json and <unit>-collected.json for both endpoints.')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    runs = []
    for directory in args.directories:
        plan = json.loads((directory / 'RUN.json').read_text())
        try:
            collected = {unit: json.loads((directory / (unit + '-collected.json')).read_text())
                         for unit in (plan['sender'], plan['receiver'])}
            runs.append(one(plan, collected))
        except (OSError, ValueError, TypeError, KeyError) as error:
            runs.append({'run_id': plan['run_id'], 'status': 'invalid_evidence',
                         'completed': False, 'error': str(error)[:256]})
    args.output.write_text(json.dumps(aggregate(runs), indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'recorded_runs': len(runs),
                      'completed_runs': sum(r['completed'] for r in runs)}))


if __name__ == '__main__': main()
