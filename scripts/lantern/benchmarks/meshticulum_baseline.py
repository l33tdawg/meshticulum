#!/usr/bin/env python3
"""Offline framing/codec baseline. Never opens a radio, service or network socket.

Run with PYTHONPATH=software/runtime/src and the pinned development interpreter.
Only synthetic identities/text are used. Timings describe this local process;
physical RF and protected full-path measurements remain separate empty gates.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time

from nacl.signing import SigningKey
from sage_lantern import mesh_envelope, mesh_fragments


def summary(values):
    ordered = sorted(values)
    return dict(samples=len(values), median_ns=int(statistics.median(values)),
                p95_ns=ordered[max(0, (len(ordered)*95+99)//100-1)])


def baseline(samples):
    sender_key, recipient_key = SigningKey(bytes(range(32))), SigningKey(bytes(range(32, 64)))
    sender = mesh_envelope.Peer('fixture-sender', bytes(sender_key.verify_key))
    recipient = mesh_envelope.Peer('fixture-recipient', bytes(recipient_key.verify_key))
    cases = []
    for payload_bytes in (32, 128, 512):
        for version in (1, 2):
            fields = dict(sender=sender, recipient=recipient,
                message_id='00000000-0000-4000-8000-000000000001',
                content='x'*payload_bytes, issued_at=100, expires_at=1000)
            if version == 2:
                fields.update(issued_at=1000, expires_at=1600,
                    attempt_id='00000000-0000-4000-8000-000000000002',
                    logical_issued_at=100, logical_expires_at=1000)
            seal_ns, open_ns = [], []
            for _ in range(samples):
                start = time.perf_counter_ns()
                sealed = mesh_envelope.seal_message(sender_key, **fields)
                seal_ns.append(time.perf_counter_ns()-start)
                start = time.perf_counter_ns()
                opened = mesh_envelope.open_envelope(sealed.wire, recipient_key,
                    expected_sender=sender, expected_recipient=recipient, now=fields['issued_at'])
                open_ns.append(time.perf_counter_ns()-start)
                assert opened.content == fields['content']
            fragments = mesh_fragments.split_envelope(sealed.wire.encode('ascii'))
            rebuilt = b''.join(mesh_fragments.decode_fragment(f).payload for f in fragments)
            assert rebuilt == sealed.wire.encode('ascii')
            frame_bytes = sum(len(f.encode('ascii')) for f in fragments)
            cases.append(dict(version=version, application_bytes=payload_bytes,
                envelope_wire_bytes=sealed.encoded_bytes, fragment_count=len(fragments),
                fragment_text_bytes=frame_bytes, max_fragment_text_bytes=max(map(len, fragments)),
                application_to_fragment_text_ratio=payload_bytes/frame_bytes,
                seal_local_elapsed=summary(seal_ns), open_local_elapsed=summary(open_ns)))
    sources = {}
    for module in (mesh_envelope, mesh_fragments):
        source = Path(module.__file__).resolve()
        sources[module.__name__] = dict(path=str(source), sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    return dict(schema='meshticulum.baseline.source-fixture.v1', evidence_class='source_fixture',
        synthetic=True, hardware_used=False, network_used=False,
        host=dict(system=platform.system(), machine=platform.machine(), python=platform.python_version()),
        sources=sources, cases=cases,
        framing=dict(chunk_bytes=mesh_fragments.CHUNK_BYTES, max_wire_bytes=mesh_fragments.MAX_MESSAGE_BYTES,
                     max_fragments=mesh_fragments.MAX_FRAGMENTS, assembly_seconds=mesh_fragments.ASSEMBLY_SECONDS),
        timing_scope='local codec elapsed time including host scheduling; excludes protected storage, broker, firmware, RF, reverse ACKs and contention',
        unmeasured_full_path_gates={stage: None for stage in (
            'fitted_radio_profile_and_power', 'protected_storage_identity_latency',
            'broker_dispatch_capture_latency', 'TLS_handshake_request_response_bytes',
            'RF_forward_reverse_airtime', 'loss_retries_and_multi_hop',
            'budget_wake_delay', 'HTTPS_goodput', 'authenticated_SAGE_admission',
            'verified_chunk_goodput_and_resume', 'interactive_latency_under_bulk', 'whole_device_energy')})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=25)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.samples <= 1000:
        parser.error('--samples must be between 1 and 1000')
    encoded = json.dumps(baseline(args.samples), indent=2, allow_nan=False)+'\n'
    if args.output is None:
        print(encoded, end='')
    else:
        args.output.write_text(encoded)


if __name__ == '__main__':
    main()
