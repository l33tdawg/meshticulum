"""Matched finite scalar4/8 experiment. No devices or wall-clock RF evidence."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

repo = Path(sys.argv[1]).resolve(strict=True)
sys.path[:0] = [str(repo), str(repo / 'src'), str(repo / 'examples')]
inputs = sorted(str(path.relative_to(repo)) for path in (repo/'src/meshticulum').rglob('*.py'))
inputs += ['examples/radio_bundle.py', 'examples/radio_file.py',
    'tests/test_burst_sf5_recovery.py', 'tests/test_burst_worker.py',
    'tests/test_meshticulum_bench_runtime.py', 'tests/radio_profiles.py']
input_hashes = {name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in inputs}
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from meshticulum.resources import byte_bundle, bundle_wire, knowledge_pack as kp, resource
from tests import test_burst_sf5_recovery as model
import radio_bundle

useful = bytes(index % 251 for index in range(23900))
key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
manifest, chunks, signature = byte_bundle.build(useful, pack_id='sf5-scalar-source',
    created_at='2026-10-30T00:00:00Z', private_key=key)
entry = manifest['chunks'][0]
canonical = kp.chunk_bytes(chunks[entry['sha256']])
response = radio_bundle.response_for(kp.manifest_digest(manifest), entry, canonical)
original = model.provision
def provision(path, unit, payload, clock, profile):
    plan = original(path, unit, payload, clock, profile)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = radio_bundle.service_for(entry, response, plan['expires_epoch_us'])
    return plan
model.provision = provision

cases = [('clean', {}), ('startup-ack', dict(drop_ack_seqs={0: 1})),
    ('ack-at-seq8', dict(drop_ack_seqs={8: 1})),
    ('final-ack', dict(drop_final_ack=True)),
    ('head-data', dict(drop_data={0: 1})), ('middle-data', dict(drop_data={3: 1})),
    ('train-tail-data', dict(drop_data={7: 1})), ('file-tail-data', dict(drop_data={127: 1})),
    ('repeated-ack-at-seq8-loss', dict(drop_ack_seqs={8: 6})),
    ('host40-10', dict(host_work_us=(40_000, 10_000))),
    ('host120-40', dict(host_work_us=(120_000, 40_000)))]
rows = []
try:
    with tempfile.TemporaryDirectory(prefix='sf5-scalar4-8-source-') as temporary:
        for label, options in cases:
            for depth in (4, 8):
                results, air = model.exercise(Path(temporary) / label / str(depth),
                    payload=response, sender_cap=160, train_depth=depth, host_pump=True,
                    receipt_confirmation=True, **options)
                received = results[1].get('received', b'')
                verified = (received == response and bundle_wire.decode_chunk(
                    received[resource.RESPONSE.size:], entry) == canonical)
                elapsed = air.timeline.now - 100_000_000
                row = dict(case=label, train_depth=depth, parameters=options,
                    useful_bytes=len(useful), response_bytes=len(response),
                    modeled_elapsed_us=elapsed,
                    modeled_useful_bytes_per_second=len(useful)*1e6/elapsed,
                    complete=all(r.get('complete', False) for r in results),
                    exact_response_and_chunk_verified=verified,
                    collisions=sum(frame.collision for frame in air.frames),
                    physical_peak=air.channels[0].peak,
                    radios_disarmed=all(not channel.armed for channel in air.channels),
                    pending_retained=len(air.channels[0].events), endpoints=[])
                for index, result in enumerate(results):
                    snapshot = result.get('snapshot', {})
                    row['endpoints'].append(dict(role='sender' if index == 0 else 'receiver',
                        error_type=type(result['error']).__name__ if 'error' in result else None,
                        error=str(result['error']) if 'error' in result else None,
                        cleanup_error_types=[type(e).__name__ for e in result.get('cleanup_errors', [])],
                        retransmits=snapshot.get('retransmits'),
                        journal=snapshot.get('attempt_journal'), counts=snapshot.get('counts'),
                        settled_states=result.get('settled_states')))
                rows.append(row)
                print(json.dumps({k: row[k] for k in ('case','train_depth','complete',
                    'modeled_elapsed_us','modeled_useful_bytes_per_second','collisions')}), flush=True)
finally:
    model.provision = original
    report = dict(schema='meshticulum.scalar-eight-source.v1', source_only=True,
        radio_devices_opened=0, source_base_commit=subprocess.check_output(
            ['git','-C',str(repo),'rev-parse','HEAD'], text=True).strip(),
        source_checkout_modified=bool(subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True)),
        source_sha256=input_hashes, source_inputs_stable=all(hashlib.sha256((repo/name).read_bytes()).hexdigest()==pin for name,pin in input_hashes.items()),
        helper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        response_sha256=hashlib.sha256(response).hexdigest(), cases=rows,
        timing_boundary='Modeled discovery/ARM through receipt confirmation and cleanup; real process startup/CPU/fsync time excluded.',
        limitations=['Idealized native RX/setup; not measured target timing or physical goodput.',
            'Real scalar USB 64B/2ms modeled writes and durable journal/crypto code; only injected CPU time advances modeled clock.',
            'One bounded signed part; no complete BundleTransfer quarantine/publication application.',
            'Both fresh source fixture sender caps are explicitly160 (within native256); receiver64 and original leases unchanged.'])
    Path(sys.argv[2]).write_text(json.dumps(report, sort_keys=True, indent=2)+'\n')
