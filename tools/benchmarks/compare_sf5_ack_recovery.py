# SPDX-License-Identifier: Apache-2.0
"""Repeat bounded source-only ACK recovery comparisons; never opens a radio."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    sys.path[:0] = [str(repo), str(repo / 'src')]
    from meshticulum.bench.flow_runtime import BenchFlow
    from tests import test_burst_sf5_recovery as model

    class PreviousAckPolicy(BenchFlow):
        def _service_ack_recovery(self, now_us):
            return False

    cases = [
        ('clean', {}),
        ('lost_final_ack', dict(drop_final_ack=True)),
        ('four_data_losses_receiver_cap20', dict(receiver_cap=20, drop_data={0: 1, 2: 1, 5: 2})),
        ('four_data_losses_receiver_cap64', dict(receiver_cap=64, drop_data={0: 1, 2: 1, 5: 2})),
        ('host_cost_10_10_ms', dict(host_work_us=(10000, 10000))),
        ('host_cost_40_10_ms', dict(host_work_us=(40000, 10000))),
        ('host_cost_120_40_ms', dict(host_work_us=(120000, 40000))),
    ]
    rows = []
    try:
        with tempfile.TemporaryDirectory(prefix='meshticulum-ack-comparison-') as temporary:
            for name, parameters in cases:
                pair = dict(case=name, modeled_faults=parameters, receipt_confirmation=True)
                for label, cls in [('before', PreviousAckPolicy), ('after', BenchFlow)]:
                    model.BenchFlow = cls
                    results, air = model.exercise(Path(temporary) / name / label,
                                                  receipt_confirmation=True, **parameters)
                    pair[label] = dict(
                        complete=all(r.get('complete', False) for r in results),
                        modeled_elapsed_us=air.timeline.now - 100_000_000,
                        modeled_collision_frames=sum(frame.collision for frame in air.frames),
                        modeled_injected_losses=sum(frame.lost and not frame.collision for frame in air.frames),
                        all_radios_disarmed=all(not channel.armed for channel in air.channels),
                        retained_physical_requests=len(air.channels[0].events),
                        endpoints=[dict(role='sender' if index == 0 else 'receiver',
                            complete=result.get('complete', False),
                            error=None if 'error' not in result else str(result['error']),
                            cleanup_error_types=[type(e).__name__ for e in result.get('cleanup_errors', [])],
                            acked_frames=result['snapshot']['acked_frames'],
                            retransmits=result['snapshot']['retransmits'],
                            received_bytes=len(result['received']),
                            journal=result['snapshot']['attempt_journal'],
                            counts=result['snapshot']['counts']) for index, result in enumerate(results)])
                rows.append(pair)
    finally:
        model.BenchFlow = BenchFlow

    paths = ['src/meshticulum/bench/flow_runtime.py', 'tests/test_burst_sf5_recovery.py',
             'tests/test_burst_final_ack_repair.py']
    report = dict(schema='meshticulum.sf5-ack-recovery-source-comparison.v1', source_only=True,
        evidence_boundary='Deterministic modeled host/USB/RF time and explicit injected losses; not measured RF throughput or proof of the cause of retained hardware losses.',
        baseline='Same current source and receipt-confirmation opt-in; subclass disables only _service_ack_recovery. Original retry floors, clocks, grants, ciphertext and durable charging remain active.',
        object_bytes=len(model.PAYLOAD), data_frames=9, radio_devices_opened=0,
        python=sys.version.split()[0], sqlite=sqlite3.sqlite_version, platform=platform.platform(),
        source_commit=subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
        source_worktree_status=subprocess.check_output(['git', '-C', str(repo), 'status', '--short'], text=True).splitlines(),
        source_sha256={name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in paths},
        helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), cases=rows)
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    print(json.dumps(dict(output=str(args.output), cases=len(rows), sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))


if __name__ == '__main__':
    main()
