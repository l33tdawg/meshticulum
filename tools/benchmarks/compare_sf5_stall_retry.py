"""Paired source model for train12 saturated-window stall recovery."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    sys.path[:0] = [str(repo), str(repo / 'src'), str(repo / 'examples')]

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from meshticulum.resources import byte_bundle, knowledge_pack as kp
    import radio_bundle
    from tests import test_burst_sf5_recovery as model

    inputs = sorted(
        str(path.relative_to(repo))
        for path in (repo / 'src/meshticulum').rglob('*.py')
    )
    inputs += [
        'examples/radio_bundle.py',
        'examples/radio_file.py',
        'tests/test_burst_sf5_recovery.py',
        'tests/test_burst_worker.py',
        'tests/test_meshticulum_bench_runtime.py',
        'tests/radio_profiles.py',
        'tests/test_burst_stall_retry.py',
        'tests/test_flow_stall_retry.py',
        'firmware/meshticulum-bench/burst_adapter.hpp',
        'firmware/meshticulum-bench/experimental/tx_burst.hpp',
    ]
    pins = {
        name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
        for name in inputs
    }

    useful = bytes(index % 251 for index in range(23900))
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    manifest, chunks, _ = byte_bundle.build(
        useful,
        pack_id='sf5-stall-source',
        created_at='2026-10-30T00:00:00Z',
        private_key=key,
    )
    entry = manifest['chunks'][0]
    canonical = kp.chunk_bytes(chunks[entry['sha256']])
    response = radio_bundle.response_for(
        kp.manifest_digest(manifest),
        entry,
        canonical,
    )
    service = radio_bundle.service_for(entry, response, 1_180_000_000)
    roles = {
        'sender': {'attempts': 160, 'estimated_airtime_us': 8_000_000},
        'receiver': {'attempts': 64, 'estimated_airtime_us': 3_000_000},
    }
    cases = {
        'seven-consecutive-holes': {seq: 1 for seq in range(15, 22)},
        'nine-consecutive-holes': {seq: 1 for seq in range(9, 18)},
    }
    rows = []
    with tempfile.TemporaryDirectory(prefix='sf5-stall-source-') as temporary:
        for name, faults in cases.items():
            arm_rows = []
            for enabled in (False, True):
                results, air = model.exercise(
                    Path(temporary) / name / str(enabled),
                    train_depth=8,
                    train12=True,
                    stall_retry=enabled,
                    payload=response,
                    service=service,
                    operation_limits=roles,
                    sender_cap=160,
                    receiver_cap=64,
                    host_pump=True,
                    receipt_confirmation=True,
                    drop_data=faults,
                )
                elapsed = air.timeline.now - 100_000_000
                sender = results[0].get('snapshot', {})
                counts = sender.get('counts', {})
                row = {
                    'stall_retry': enabled,
                    'complete': all(result.get('complete', False) for result in results),
                    'modeled_elapsed_us': elapsed,
                    'modeled_useful_bytes_per_second':
                        len(useful) * 1_000_000 / elapsed,
                    'attempts': sender.get('attempt_journal', {}).get('attempts'),
                    'retransmits': sender.get('retransmits'),
                    'fast_retransmits': sender.get('fast_retransmits'),
                    'stall_repair_turns': counts.get('stall_repair_turn', 0),
                    'stall_repair_extras': counts.get('stall_repair_extra', 0),
                    'modeled_collisions': sum(frame.collision for frame in air.frames),
                    'physical_peak': air.channels[0].peak,
                    'all_radios_disarmed': all(not channel.armed for channel in air.channels),
                    'untriggered_data_drops': air.drop_data,
                    'sender_error': (str(results[0]['error'])
                                     if 'error' in results[0] else None),
                    'receiver_error': (str(results[1]['error'])
                                       if 'error' in results[1] else None),
                }
                arm_rows.append(row)
            assert arm_rows[0]['complete'] == arm_rows[1]['complete']
            assert arm_rows[0]['attempts'] == arm_rows[1]['attempts']
            rows.append({
                'case': name,
                'faults': faults,
                'without_stall_retry': arm_rows[0],
                'with_stall_retry': arm_rows[1],
                'elapsed_reduction_us':
                    arm_rows[0]['modeled_elapsed_us']
                    - arm_rows[1]['modeled_elapsed_us'],
                'elapsed_reduction_percent':
                    100 * (arm_rows[0]['modeled_elapsed_us']
                           - arm_rows[1]['modeled_elapsed_us'])
                    / arm_rows[0]['modeled_elapsed_us'],
            })

    report = {
        'schema': 'meshticulum.sf5-stall-retry-source.v1',
        'source_only': True,
        'radio_devices_opened': 0,
        'source_base_commit': subprocess.check_output(
            ['git', '-C', str(repo), 'rev-parse', 'HEAD'],
            text=True,
        ).strip(),
        'source_checkout_modified': bool(subprocess.check_output(
            ['git', '-C', str(repo), 'status', '--porcelain'],
            text=True,
        )),
        'source_sha256': pins,
        'source_inputs_stable': all(
            hashlib.sha256((repo / name).read_bytes()).hexdigest() == pin
            for name, pin in pins.items()
        ),
        'helper_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'useful_sha256': hashlib.sha256(useful).hexdigest(),
        'response_sha256': hashlib.sha256(response).hexdigest(),
        'fixed_operation_limits': roles,
        'cases': rows,
        'timing_boundary':
            'Modeled discovery/ARM through receipt confirmation and cleanup; '
            'real process startup, CPU and fsync time are excluded.',
        'limitations': [
            'Synthetic identical signed 23900-byte input and 128-DATA response geometry; not a RF measurement.',
            'The model runs real flow, owner, AEAD and durable journals, but native setup and receive priority are idealized.',
            'Seven-consecutive-holes retains two pre-existing ACK/DATA model collisions in both arms.',
            'The nine-hole case retains zero modeled collisions and is the cleaner scheduling comparison.',
            'A smaller source-model time is not physical application throughput or Alpha 4 qualification.',
        ],
    }
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps({
        row['case']: {
            'seconds_saved': row['elapsed_reduction_us'] / 1_000_000,
            'percent': row['elapsed_reduction_percent'],
            'collisions': row['with_stall_retry']['modeled_collisions'],
        }
        for row in rows
    }, sort_keys=True))


if __name__ == '__main__':
    main()
