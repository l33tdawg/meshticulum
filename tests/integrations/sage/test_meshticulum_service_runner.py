# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import pytest
pytest.importorskip("sage_sdk", reason="optional SAGE adapter requires compatible SDK")

"""Actual finite loop and private capture composition; native/RF/SAGE are models."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from meshticulum.integrations.sage.mesh_binary_capture import BinaryCapture, CaptureError
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.integrations.sage.mesh_inbox import INBOX_SCHEMA
from meshticulum.bench.service_runner import (
    PrivateCaptureSink, PrivatePolicy, ServiceRunner,
)
from meshticulum.bench.services import ServiceTransfer, resource
from meshticulum.bench.wire import FrameKind, WireError
from tests.test_meshticulum_bench_runtime import BOOT, Clock
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.service_helpers import scope
from tests.integrations.sage.test_mesh_inbox import rig, sealed, records, SENDER, RECIPIENT, KEY, OPERATION


def setup_private(rig, clock):
    rig.lease_held = False
    rig.policy_live = True
    @contextmanager
    def lease():
        assert not rig.lease_held
        rig.lease_held = True
        try:
            yield
        finally:
            rig.lease_held = False
    capture = BinaryCapture(rig.store, recipient=RECIPIENT, recipient_signing_key=KEY,
        owner_lease=lease, access_check=lambda: rig.live, storage_check=lambda: rig.storage,
        peer_check=lambda peer: peer == rig.peer, boot_id=BOOT,
        clock=lambda: clock()[0] / 1000000, monotonic_clock=lambda: clock()[1] / 1000000)
    return capture, lease


def run_pair(sender, receiver, clock, rig=None):
    for _ in range(1600):
        if rig is not None:
            rig.now = clock()[0] / 1000000
        sender.step(); receiver.step()
        clock.advance(50000)
        if sender.wire_complete and receiver.wire_complete:
            if rig is not None:
                rig.now = clock()[0] / 1000000
            return
    raise AssertionError('modeled_service_runner_did_not_complete')


@pytest.mark.parametrize('fault', ['none', 'loss', 'corrupt', 'replay'])
def test_private_runner_persists_intention_before_ack_then_captures_and_settles(tmp_path, rig, fault):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    capture, lease = setup_private(rig, clock)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 81,
                      service=scope('LME.binary', clock)) as pair:
        src, dst = ServiceTransfer(pair.sender), ServiceTransfer(pair.receiver)
        sender = ServiceRunner(src, payload=payload,
            private_policy=PrivatePolicy(grant_digest=src.grant_digest, lease=lease,
                check=lambda binding: rig.policy_live))
        sink = PrivateCaptureSink(capture, rig.inbox, operation_id=OPERATION, sender=SENDER)
        receiver = ServiceRunner(dst, private_sink=sink,
            private_policy=PrivatePolicy(grant_digest=dst.grant_digest, lease=lease,
                check=lambda binding: rig.policy_live))
        sender.start(); receiver.start()
        assert capture.state() == 'receiving'
        assert not pair.workers[0].sent and not pair.workers[1].sent
        if fault == 'loss':
            pair.workers[0].drop_kind = FrameKind.DATA
        elif fault == 'corrupt':
            pair.workers[0].corrupt_once = True
        elif fault == 'replay':
            pair.workers[0].replay_once = True
        run_pair(sender, receiver, clock, rig)
        assert capture.state() == 'captured' and not records(rig)
        assert receiver.snapshot()['application_status'] is None
        result = receiver.settle_private()
        assert result.status == 'admitted' and len(records(rig)) == 1
        assert receiver.snapshot()['application_status'] == 'admitted'
        assert 'private synthetic' not in json.dumps(rig.rows)
        assert all(b'private synthetic' not in frame for worker in pair.workers for frame in worker.sent)
        assert sink.acknowledge()
        assert receiver.close() is True
        assert pair.receiver.journal.snapshot()['attempts'] > 0
        assert not rig.lease_held


def test_private_dispatch_is_refused_without_real_provider_binding(tmp_path):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 82,
                      service=scope('LME.binary', clock)) as pair:
        with pytest.raises(WireError):
            ServiceRunner(ServiceTransfer(pair.sender), payload=payload)
        assert not pair.workers[0].sent and not pair.workers[1].sent


def test_sender_policy_revocation_prevents_fresh_carrier_attempt(tmp_path, rig):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    capture, lease = setup_private(rig, clock)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 83,
                      service=scope('LME.binary', clock)) as pair:
        src = ServiceTransfer(pair.sender)
        runner = ServiceRunner(src, payload=payload,
            private_policy=PrivatePolicy(grant_digest=src.grant_digest, lease=lease,
                check=lambda binding: rig.policy_live))
        runner.start()
        rig.policy_live = False
        before = pair.sender.journal.snapshot()
        with pytest.raises(WireError):
            runner.step()
        assert not pair.workers[0].sent and pair.sender.journal.snapshot() == before
        assert runner.state == 'failed' and pair.sender.closed


def test_receiver_policy_refuses_intention_before_first_dispatch(tmp_path, rig):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    capture, lease = setup_private(rig, clock)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 84,
                      service=scope('LME.binary', clock)) as pair:
        dst = ServiceTransfer(pair.receiver)
        runner = ServiceRunner(dst, private_sink=PrivateCaptureSink(capture, rig.inbox,
            operation_id=OPERATION, sender=SENDER),
            private_policy=PrivatePolicy(grant_digest=dst.grant_digest, lease=lease, check=lambda binding: False))
        with pytest.raises(CaptureError):
            runner.start()
        assert not rig.rows and not pair.workers[0].sent and not pair.workers[1].sent


@pytest.mark.parametrize('fault', ['before', 'after', 'conflict'])
def test_runner_fences_unknown_inbox_then_explicit_recovery_uses_original_capture(tmp_path, rig, fault):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    capture, lease = setup_private(rig, clock)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 85,
                      service=scope('LME.binary', clock)) as pair:
        src, dst = ServiceTransfer(pair.sender), ServiceTransfer(pair.receiver)
        policy = lambda transfer: PrivatePolicy(grant_digest=transfer.grant_digest, lease=lease,
                                               check=lambda binding: True)
        sender = ServiceRunner(src, payload=payload, private_policy=policy(src))
        receiver = ServiceRunner(dst, private_policy=policy(dst),
            private_sink=PrivateCaptureSink(capture, rig.inbox, operation_id=OPERATION, sender=SENDER))
        sender.start(); receiver.start(); run_pair(sender, receiver, clock, rig)
        rig.failure = (INBOX_SCHEMA, fault)
        with pytest.raises(CaptureError):
            receiver.settle_private()
        assert receiver.state == 'failed' and receiver.close() is False
        before = pair.receiver.journal.snapshot()
        rig.failure = None
        clock.advance(60000000)
        rig.now = clock()[0] / 1000000
        restored, _ = setup_private(rig, clock)
        sink = PrivateCaptureSink(restored, rig.new(), operation_id=OPERATION, sender=SENDER)
        result = sink.reconcile()
        assert result.status == ('duplicate' if fault == 'after' else 'rejected')
        assert pair.receiver.journal.snapshot() == before
        if fault != 'after':
            assert result.reason == 'application_expired' and not records(rig)
        else:
            assert len(records(rig)) == 1 and records(rig)[0]['payload']['operation_id'] == OPERATION


def test_public_runner_uses_same_flow_and_does_not_claim_publisher_verification(tmp_path):
    clock = Clock()
    payload = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 86,
                      service=scope('MR1.request', clock)) as pair:
        sender = ServiceRunner(ServiceTransfer(pair.sender), payload=payload)
        receiver = ServiceRunner(ServiceTransfer(pair.receiver))
        sender.start(); receiver.start(); run_pair(sender, receiver, clock)
        assert receiver.public_payload == payload
        assert receiver.application_result is None
        assert receiver.snapshot()['service_kind'] == 'knowledge'
        before = pair.receiver.journal.snapshot()
        assert receiver.close() is True
        assert receiver.public_payload is None and pair.receiver.journal.snapshot() == before


def test_public_runner_module_does_not_load_private_runtime_or_sdk():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, '-c',
        'import sys; from meshticulum.bench.service_runner import ServiceRunner; '
        'assert not any(n == "sage_lantern" or n.startswith("sage_lantern.") '
        'or n == "sage_sdk" or n.startswith("sage_sdk.") for n in sys.modules)'],
        cwd=root, env={**os.environ, 'PYTHONPATH': str(root)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
