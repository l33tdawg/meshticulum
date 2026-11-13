"""Matched train12/train24 source-clock model; no physical speed claim."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.repo.resolve(strict=True)
    sys.path[:0] = [str(root), str(root/'src'), str(root/'examples')]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from meshticulum.resources import byte_bundle, bundle_wire, knowledge_pack as kp, resource
    from meshticulum.bench.admission import RadioAdmission
    from tests import test_burst_sf5_recovery as model
    import radio_bundle
    model.PROFILE = replace(model.PROFILE, preamble=32, sync_word=18)
    model.ADMISSION = RadioAdmission('MY', ((921375000, 500000, 5, 5, 0, 32, 18),),
                                     10000, 180000, 256, 60_000_000)
    paths = sorted((root/'src/meshticulum').rglob('*.py'))
    paths += [root/p for p in ('tests/test_burst_sf5_recovery.py',
        'tests/test_burst_worker.py', 'tests/test_meshticulum_bench_runtime.py',
        'tests/radio_profiles.py', 'examples/radio_file.py', 'examples/radio_bundle.py',
        'firmware/meshticulum-bench/burst_adapter.hpp',
        'firmware/meshticulum-bench/experimental/tx_burst.hpp')]
    pins = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    useful = hashlib.shake_256(b'public-source-only-alpha5-matched-23900').digest(23900)
    key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b'public-source-only-alpha5-key').digest())
    manifest, chunks, _ = byte_bundle.build(useful, pack_id='alpha5-source-train-comparison',
        created_at='2026-11-12T00:00:00Z', private_key=key)
    entry = manifest['chunks'][0]
    canonical = kp.chunk_bytes(chunks[entry['sha256']])
    response = radio_bundle.response_for(kp.manifest_digest(manifest), entry, canonical)
    service = radio_bundle.service_for(entry, response, 1_180_000_000)
    roles = dict(sender=dict(attempts=160, estimated_airtime_us=8_000_000),
                 receiver=dict(attempts=64, estimated_airtime_us=3_000_000))
    cases = [('clean', {}), ('final-ack-lost', dict(drop_final_ack=True)),
             ('slow-host', dict(host_work_us=(40_000, 10_000)))]
    rows = []
    with tempfile.TemporaryDirectory(prefix='train24-source-') as temporary:
        for case, faults in cases:
            for train in (12, 24):
                result, air = model.exercise(Path(temporary)/case/str(train),
                    train_depth=8, train12=train==12, train24=train==24,
                    payload=response, service=service, operation_limits=roles,
                    sender_cap=160, receiver_cap=64, host_pump=True,
                    receipt_confirmation=True, **faults)
                received = result[1].get('received', b'')
                verified = received == response and bundle_wire.decode_chunk(
                    received[resource.RESPONSE.size:], entry) == canonical
                elapsed = air.timeline.now - 100_000_000
                endpoints = []
                for index, r in enumerate(result):
                    snapshot = r.get('snapshot', {})
                    endpoints.append(dict(role='sender' if index==0 else 'receiver',
                        complete=r.get('complete', False),
                        error=None if 'error' not in r else str(r['error']),
                        cleanup_errors=[str(e) for e in r.get('cleanup_errors', [])],
                        counts=snapshot.get('counts'), journal=snapshot.get('attempt_journal'),
                        retransmits=snapshot.get('retransmits'),
                        settled_states=r.get('settled_states'),
                        native_accepted_attempts=air.channels[index].attempts))
                row = dict(case=case, train_frames=train, queue_slots=8,
                    useful_bytes=len(useful), response_bytes=len(response),
                    data_frames=air.data_count, modeled_elapsed_us=elapsed,
                    modeled_useful_bytes_per_second=len(useful)*1_000_000/elapsed,
                    exact_response_and_chunk_verified=verified,
                    complete=all(r.get('complete', False) and 'error' not in r
                                 and not r.get('cleanup_errors') for r in result),
                    modeled_faults=faults,
                    modeled_ack_emissions=sum('ack_seq' in f.inner for f in air.frames),
                    modeled_collisions=sum(f.collision for f in air.frames),
                    physical_peak=air.channels[0].peak,
                    physical_retained=len(air.channels[0].events),
                    all_radios_disarmed=all(not c.armed for c in air.channels),
                    endpoints=endpoints)
                rows.append(row)
                print(json.dumps({k:row[k] for k in ('case','train_frames','complete',
                    'modeled_elapsed_us','modeled_ack_emissions')}, sort_keys=True), flush=True)
    report = dict(schema='meshticulum.train24-source.v1', source_only=True,
        device_actions=0, source_base_commit=subprocess.check_output(
            ['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
        helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        source_sha256=pins, source_inputs_stable=all(
            hashlib.sha256((root/p).read_bytes()).hexdigest()==digest for p,digest in pins.items()),
        useful_sha256=hashlib.sha256(useful).hexdigest(),
        response_sha256=hashlib.sha256(response).hexdigest(), fixed_operation_limits=roles,
        profile=dict(spreading_factor=5, bandwidth_hz=500000, preamble=32, sync_word=18),
        timing_boundary='Modeled discovery/ARM through receipt confirmation and cleanup; excludes process startup and unmodeled CPU/fsync.',
        limitations=['Synthetic signed23900-byte fixture; same response for both policies; not retained RF ciphertext.',
            'Real frozen flow, AEAD and journals run against modeled radios and a deterministic event clock.',
            'USB64-byte/2ms pacing is modeled; IRQ/header/driver setup and CPU/fsync costs are not measured.',
            'Failures, ACK loss and slow-host sensitivity are retained; fresh native refusal remains paid.',
            'No whole-file quarantine publication or physical receiver reader verification in this model.',
            'Rates are source-model outputs only; neither process-start performance nor4000useful B/s is established.'],
        cases=rows)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':
    main()
