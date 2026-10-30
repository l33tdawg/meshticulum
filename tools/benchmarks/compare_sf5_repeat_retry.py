"""Matched v6/v7 repeat-repair source model; all radio and timer bounds stay fixed."""
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
    from meshticulum.resources import byte_bundle, bundle_wire, knowledge_pack as kp, resource
    from tests import test_burst_sf5_recovery as model
    import radio_bundle

    inputs = sorted(str(path.relative_to(repo)) for path in (repo / 'src/meshticulum').rglob('*.py'))
    inputs += ['examples/radio_bundle.py', 'examples/radio_file.py',
        'tests/test_burst_sf5_recovery.py', 'tests/test_burst_worker.py',
        'tests/test_meshticulum_bench_runtime.py', 'tests/radio_profiles.py']
    pins = {name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in inputs}
    useful = bytes(index % 251 for index in range(23900))
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    manifest, chunks, signature = byte_bundle.build(useful, pack_id='sf5-repair-source',
        created_at='2026-10-30T00:00:00Z', private_key=key)
    entry = manifest['chunks'][0]
    canonical = kp.chunk_bytes(chunks[entry['sha256']])
    response = radio_bundle.response_for(kp.manifest_digest(manifest), entry, canonical)
    service = radio_bundle.service_for(entry, response, 1_180_000_000)
    roles = {'sender': {'attempts': 160, 'estimated_airtime_us': 8_000_000},
             'receiver': {'attempts': 64, 'estimated_airtime_us': 3_000_000}}
    retained_data_losses = {18:2,24:1,35:1,43:1,44:1,46:1,59:2,61:1,68:2,
        83:2,86:1,87:1,88:1,91:1,103:1,109:1,112:2,113:1,120:1,124:1,126:1}
    cases = [('clean', {}), ('run06-data-loss-counts', {'drop_data':retained_data_losses}),
        ('run06-data-plus-conditional-ack-loss', {'drop_data':retained_data_losses,
            'drop_ack_seqs':{18:1}, 'drop_final_ack':True}),
        ('lost-first-and-fast', {'drop_data':{0:2}}),
        ('lost-first-and-two-fast', {'drop_data':{0:3}}),
        ('two-holes', {'drop_data':{0:1,2:1}}),
        ('two-lost-repairs', {'drop_data':{0:2,18:2}}),
        ('terminal-holes', {'drop_data':{123:1,124:1,126:1}}),
        ('terminal-lost-repair', {'drop_data':{123:2,124:1,126:1}}),
        ('tail127', {'drop_data':{127:1}}),
        ('final-ack', {'drop_final_ack':True}),
        ('seal40-10', {'host_work_us':(40_000,10_000)}),
        ('step18-3', {'host_step_work_us':(18_000,3_000)}),
        ('run06-loss-step18-3', {'drop_data':retained_data_losses,'host_step_work_us':(18_000,3_000)}),
        ('run06-loss-step18-3-seal10-1', {'drop_data':retained_data_losses,
            'host_step_work_us':(18_000,3_000),'host_work_us':(10_000,1_000)})]
    rows = []
    with tempfile.TemporaryDirectory(prefix='sf5-repeat-retry-source-') as temporary:
        for name, faults in cases:
            for fill in (False, True):
                peak_flights = peak_reorder = 0
                def observe(index, endpoint, now):
                    nonlocal peak_flights, peak_reorder
                    peak_flights = max(peak_flights, endpoint.flow.in_flight())
                    peak_reorder = max(peak_reorder, len(endpoint.flow._buffer))
                    assert endpoint.burst_frames == 8
                    assert endpoint.flow.in_flight() <= 32
                    assert len(endpoint.flow._buffer) < 32
                results, air = model.exercise(Path(temporary) / name / str(fill),
                    train_depth=8, flow_window=32, scalar_fill_pair=not fill, repeat_retry=fill,
                    payload=response, service=service, operation_limits=roles,
                    sender_cap=160, receiver_cap=64, host_pump=True,
                    receipt_confirmation=True, observe=observe, **faults)
                received = results[1].get('received', b'')
                verified = (received == response and bundle_wire.decode_chunk(
                    received[resource.RESPONSE.size:], entry) == canonical)
                elapsed = air.timeline.now - 100_000_000
                row = dict(case=name, selective_repeat_retry=fill, flow_window_frames=32, train_frames=8,
                    modeled_faults=faults, useful_bytes=len(useful), response_bytes=len(response),
                    modeled_elapsed_us=elapsed, modeled_useful_bytes_per_second=len(useful)*1e6/elapsed,
                    complete=all(r.get('complete', False) for r in results),
                    exact_response_and_chunk_verified=verified,
                    modeled_collisions=sum(frame.collision for frame in air.frames),
                    logical_flight_peak=peak_flights, receiver_reorder_peak=peak_reorder,
                    physical_peak=air.channels[0].peak,
                    all_radios_disarmed=all(not channel.armed for channel in air.channels),
                    untriggered_data_drops=air.drop_data, untriggered_ack_base_drops=air.drop_ack_seqs,
                    physical_retained=len(air.channels[0].events), endpoints=[])
                for index, result in enumerate(results):
                    snapshot = result.get('snapshot', {})
                    row['endpoints'].append(dict(role='sender' if index == 0 else 'receiver',
                        error_type=type(result['error']).__name__ if 'error' in result else None,
                        error=str(result['error']) if 'error' in result else None,
                        cleanup_error_types=[type(e).__name__ for e in result.get('cleanup_errors', [])],
                        retransmits=snapshot.get('retransmits'), fast_retransmits=snapshot.get('fast_retransmits'),
                        repeat_retry_epochs=snapshot.get('repeat_retry_epochs'),
                        journal=snapshot.get('attempt_journal'), counts=snapshot.get('counts'),
                        settled_states=result.get('settled_states')))
                rows.append(row)
                print(json.dumps({k: row[k] for k in ('case', 'selective_repeat_retry', 'complete',
                    'modeled_elapsed_us', 'modeled_collisions', 'receiver_reorder_peak')}), flush=True)
    report = dict(schema='meshticulum.repeat-retry-source.v1', source_only=True,
        radio_devices_opened=0, source_base_commit=subprocess.check_output(
            ['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
        source_checkout_modified=bool(subprocess.check_output(
            ['git', '-C', str(repo), 'status', '--porcelain'], text=True)),
        source_sha256=pins, source_inputs_stable=all(
            hashlib.sha256((repo / name).read_bytes()).hexdigest() == pin for name, pin in pins.items()),
        helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        useful_sha256=hashlib.sha256(useful).hexdigest(), response_sha256=hashlib.sha256(response).hexdigest(),
        fixed_operation_limits=roles, cases=rows,
        timing_boundary='Modeled discovery/ARM through receipt confirmation and cleanup; real process startup, CPU and fsync time excluded.',
        limitations=['Synthetic identical signed 23900-byte input and 128-DATA response geometry; not the retained RF ciphertext or a paired hardware result.',
            'The run06 DATA-loss-count cases inject the retained 21 logical positions and 26 missing attempts; altered scheduling can change which attempt is selective. This does not replay RF timing, receive failures, or the actual ciphertext.',
            'Conditional ACK losses use cumulative-base predicates, not exact snapshot/revision or hardware timing; untriggered predicates remain visible.',
            'Idealized native RX/setup and modeled 64B/2ms USB writes; only explicit injected seal/step CPU work advances the source clock. Step work is a sensitivity, not a calibrated measured implementation cost.',
            'Real flow, owner, AEAD and durable journals; no complete BundleTransfer quarantine/publication application.',
            'This cannot establish the >=3000 useful file B/s target, whose physical denominator includes process startup.'])
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')


if __name__ == '__main__':
    main()
