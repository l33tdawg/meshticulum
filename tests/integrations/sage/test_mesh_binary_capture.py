# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import pytest
pytest.importorskip("sage_sdk", reason="optional SAGE adapter requires compatible SDK")

"""Real signed capture/inbox adapters; SAGE backend and native lease are models."""
from contextlib import contextmanager
import copy
import asyncio
import hashlib
import json
import threading
import uuid

import pytest

from meshticulum.integrations.sage.mesh_binary_capture import BinaryCapture, CaptureError, SCHEMA
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.integrations.sage.mesh_inbox import INBOX_SCHEMA, InboxError
from tests.integrations.sage.test_mesh_inbox import rig, sealed, records, SENDER, RECIPIENT, KEY, OPERATION, REMOTE

BOOT = '21f9c178-6090-40d9-9359-ecf1c7b950b1'


@pytest.fixture
def capture_rig(rig):
    rig.lease_active = False
    rig.lease_events = []
    rig.monotonic = 100

    @contextmanager
    def lease():
        assert not rig.lease_active
        rig.lease_active = True
        rig.lease_events.append('enter')
        try:
            yield
        finally:
            rig.lease_events.append('exit')
            rig.lease_active = False

    rig.new_capture = lambda boot=BOOT: BinaryCapture(rig.store, recipient=RECIPIENT, recipient_signing_key=KEY,
        owner_lease=lease, access_check=lambda: rig.live, storage_check=lambda: rig.storage,
        peer_check=lambda sender: sender == rig.peer, clock=lambda: rig.now,
        boot_id=boot, monotonic_clock=lambda: rig.monotonic)
    rig.capture = rig.new_capture()
    return rig


def prepare(rig, packet, *, operation=OPERATION, expires=180):
    return rig.capture.prepare(operation, sender=SENDER,
        packet_sha256=hashlib.sha256(packet).hexdigest(), packet_bytes=len(packet),
        application_expires_epoch_us=expires * 1000000, application_deadline_monotonic_us=130000000,
        host_boot_id=BOOT, authority_digest='a' * 64, permission_check=lambda: True)


def capture(rig, *, wire=None, operation=OPERATION, expires=180):
    packet = to_binary((wire or sealed()).decode())
    prepare(rig, packet, operation=operation, expires=expires)
    assert rig.capture.complete(operation, packet, authority_digest='a' * 64,
                                permission_check=lambda: True) == 'captured'
    return packet


def body(rig):
    return rig.rows[rig.capture.slot_id]['payload']['body']


def process(rig, *, owner=None, inbox=None, operation=OPERATION):
    return (owner or rig.capture).process_pending(inbox or rig.inbox, operation_id=operation)


def fail_phase(rig, phase, mode):
    original = rig.store.put
    def put(identifier, kind, revision, payload, **kwargs):
        candidate = None
        if payload['schema'] == SCHEMA:
            state = payload['body']['state']
            candidate = ('ack' if state == 'idle' and payload['body']['last_completed']
                         and payload['body']['last_completed']['acknowledged'] else
                         'clear' if state == 'idle' and revision > 0 else state)
        previous = rig.failure
        if candidate == phase:
            rig.failure = (SCHEMA, mode)
        try:
            return original(identifier, kind, revision, payload, **kwargs)
        finally:
            rig.failure = previous
    rig.store.put = put
    return original


def test_capture_restart_replay_clear_and_explicit_owner_ack(capture_rig):
    rig = capture_rig
    packet = capture(rig)
    assert body(rig)['state'] == 'captured'
    assert to_binary(body(rig)['capture']['wire']) == packet
    assert 'private synthetic' not in json.dumps(rig.rows)
    restored = rig.new_capture()
    result = process(rig, owner=restored, inbox=rig.new())
    assert result.status == 'admitted' and len(records(rig)) == 1
    assert body(rig)['state'] == 'idle' and body(rig)['capture'] is None
    assert body(rig)['last_completed']['wire'] is not None
    assert body(rig)['last_completed']['acknowledged'] is False
    assert process(rig, owner=rig.new_capture(), inbox=rig.new()).status == 'admitted'
    assert rig.new_capture().acknowledge(OPERATION) is True
    assert body(rig)['last_completed']['wire'] is None
    assert body(rig)['last_completed']['acknowledged'] is True
    assert records(rig)[0]['payload']['operation_id'] == OPERATION
    assert rig.lease_events and not rig.lease_active


@pytest.mark.parametrize('mode', ['before', 'after', 'conflict'])
def test_unknown_inbox_write_preserves_capture_and_expiry_cannot_renew_it(capture_rig, mode):
    rig = capture_rig
    packet = capture(rig)
    rig.failure = (INBOX_SCHEMA, mode)
    with pytest.raises(CaptureError):
        process(rig)
    assert rig.capture._uncertain
    assert body(rig)['state'] == 'captured'
    assert to_binary(body(rig)['capture']['wire']) == packet
    assert body(rig)['capture']['operation_id'] == OPERATION
    rig.failure = None
    rig.now = 181
    puts = rig.puts
    with pytest.raises(CaptureError):
        process(rig)
    assert rig.puts == puts
    result = process(rig, owner=rig.new_capture(), inbox=rig.new())
    assert result.status == ('duplicate' if mode == 'after' else 'rejected')
    if mode != 'after':
        assert result.reason == 'application_expired' and not records(rig)
    else:
        assert len(records(rig)) == 1 and records(rig)[0]['revision'] == 1
    assert body(rig)['last_completed']['operation_id'] == OPERATION


@pytest.mark.parametrize('phase', ['receiving', 'captured', 'processed', 'clear', 'ack'])
@pytest.mark.parametrize('mode', ['before', 'after', 'conflict'])
def test_every_capture_cas_uncertainty_is_preserved_for_explicit_recovery(capture_rig, phase, mode):
    rig = capture_rig
    packet = to_binary(sealed().decode())
    if phase != 'receiving':
        prepare(rig, packet)
    if phase not in ('receiving', 'captured'):
        rig.capture.complete(OPERATION, packet, authority_digest='a' * 64, permission_check=lambda: True)
    if phase == 'ack':
        process(rig)
    original = fail_phase(rig, phase, mode)
    with pytest.raises(CaptureError):
        if phase == 'receiving':
            prepare(rig, packet)
        elif phase == 'captured':
            rig.capture.complete(OPERATION, packet, authority_digest='a' * 64, permission_check=lambda: True)
        elif phase == 'ack':
            rig.capture.acknowledge(OPERATION)
        else:
            process(rig)
    assert rig.capture._uncertain and rig.capture.close(0) is False
    rig.store.put = original
    restored = rig.new_capture()
    state = restored.state()
    if phase == 'receiving':
        assert state == ('receiving' if mode == 'after' else 'idle')
        if state == 'receiving':
            assert body(rig)['capture']['operation_id'] == OPERATION
        return
    if phase == 'captured' and mode != 'after':
        assert state == 'receiving'
        restored.complete(OPERATION, packet, authority_digest='a' * 64, permission_check=lambda: True)
    result = process(rig, owner=restored, inbox=rig.new())
    assert result.status in ('admitted', 'duplicate') and len(records(rig)) == 1
    assert records(rig)[0]['payload']['operation_id'] == OPERATION


def test_unacknowledged_completion_cannot_be_displaced_by_next_intention(capture_rig):
    rig = capture_rig
    capture(rig)
    process(rig)
    next_id = str(uuid.UUID(int=81))
    packet = to_binary(sealed(message_id=next_id).decode())
    with pytest.raises(CaptureError):
        prepare(rig, packet, operation=next_id)
    assert body(rig)['last_completed']['operation_id'] == OPERATION
    restored = rig.new_capture()
    assert restored.acknowledge(OPERATION)
    rig.capture = restored
    assert prepare(rig, packet, operation=next_id) == 'receiving'


def test_live_application_does_not_override_expired_signed_envelope(capture_rig):
    rig = capture_rig
    capture(rig, wire=sealed(expires_at=151), expires=180)
    rig.now = 152
    result = process(rig)
    assert result.status == 'rejected' and result.reason == 'invalid_envelope'
    assert not records(rig)


def test_expired_application_cannot_create_inbox_at_historical_capture_time(capture_rig):
    rig = capture_rig
    capture(rig, expires=180)
    rig.now = 180
    result = process(rig)
    assert result.status == 'rejected' and result.reason == 'application_expired'
    assert not records(rig)


@pytest.mark.parametrize('stage', ['receiving', 'captured', 'processed', 'idle'])
def test_signed_slot_detects_mutation_and_cross_restart_clock_rollback(capture_rig, stage):
    rig = capture_rig
    packet = to_binary(sealed().decode())
    prepare(rig, packet)
    if stage != 'receiving':
        rig.capture.complete(OPERATION, packet, authority_digest='a' * 64, permission_check=lambda: True)
    if stage in ('processed', 'idle'):
        if stage == 'processed':
            original = fail_phase(rig, 'clear', 'before')
            with pytest.raises(CaptureError):
                process(rig)
            rig.store.put = original
        else:
            process(rig)
    saved = copy.deepcopy(rig.rows)
    rig.now = 149.999
    with pytest.raises(CaptureError):
        rig.new_capture().state()
    rig.now = 150
    rig.rows = saved
    body(rig)['last_epoch_us'] = 0
    with pytest.raises(CaptureError):
        rig.new_capture().state()


@pytest.mark.parametrize('gate', ['live', 'storage', 'peer'])
def test_completed_capture_never_bypasses_current_private_guards(capture_rig, gate):
    rig = capture_rig
    capture(rig)
    before = copy.deepcopy(rig.rows)
    if gate == 'peer':
        rig.peer = None
    else:
        setattr(rig, gate, False)
    with pytest.raises(CaptureError):
        process(rig)
    assert rig.rows == before and not records(rig)


def test_processed_positive_decision_rechecks_current_signed_inbox_before_clear(capture_rig):
    rig = capture_rig
    capture(rig)
    original = fail_phase(rig, 'clear', 'before')
    with pytest.raises(CaptureError):
        process(rig)
    rig.store.put = original
    assert body(rig)['state'] == 'processed'
    records(rig)[0]['payload']['attestation']['body']['envelope_digest'] = 'b' * 64
    with pytest.raises(CaptureError):
        process(rig, owner=rig.new_capture(), inbox=rig.new())
    assert body(rig)['state'] == 'processed'


def test_receiving_intention_is_not_clean_drain_or_permission_to_start_again(capture_rig):
    rig = capture_rig
    packet = to_binary(sealed().decode())
    prepare(rig, packet)
    restored = rig.new_capture()
    assert restored.state() == 'receiving'
    assert restored.close(0) is False
    with pytest.raises(CaptureError):
        process(rig, owner=rig.new_capture())
    assert body(rig)['state'] == 'receiving'


def test_close_waits_for_capture_writer_and_never_closes_borrowed_store(capture_rig):
    rig = capture_rig
    packet = to_binary(sealed().decode())
    prepare(rig, packet)
    entered, release = threading.Event(), threading.Event()
    original = rig.store.put
    def put(identifier, kind, revision, payload, **kwargs):
        if payload['schema'] == SCHEMA and payload['body']['state'] == 'captured':
            entered.set()
            assert release.wait(5)
        return original(identifier, kind, revision, payload, **kwargs)
    rig.store.put = put
    errors = []
    def write():
        try:
            rig.capture.complete(OPERATION, packet, authority_digest='a' * 64, permission_check=lambda: True)
        except CaptureError:
            errors.append('contained')
    worker = threading.Thread(target=write)
    worker.start()
    assert entered.wait(5)
    assert rig.capture.close(0) is False
    release.set(); worker.join(5)
    assert not worker.is_alive() and errors == ['contained']
    assert rig.capture.close(0) is False
    assert body(rig)['state'] == 'captured'
    assert rig.store.storage.snapshot()


def test_native_lease_cannot_suppress_failed_capture(capture_rig):
    rig = capture_rig
    class Suppress:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return True
    rig.capture._lease = Suppress
    with pytest.raises(CaptureError):
        rig.capture.state()
    assert rig.capture._uncertain


def test_read_only_inbox_reconciliation_cannot_admit_absent_expired_message(capture_rig):
    rig = capture_rig
    packet = to_binary(sealed(expires_at=151).decode())
    rig.now = 152
    result = rig.inbox.reconcile_binary(packet, sender=SENDER, observed_at=150,
                                       peer_check=lambda peer: peer == rig.peer)
    assert result is None and rig.puts == 0 and not rig.rows
    with pytest.raises(InboxError):
        rig.inbox.reconcile_binary(packet, sender=SENDER, observed_at=153, peer_check=lambda peer: True)


def test_new_unread_capture_instance_cannot_certify_clean_join(capture_rig):
    assert capture_rig.new_capture().close(0) is False


def test_original_monotonic_deadline_cannot_be_extended_by_valid_epoch(capture_rig):
    rig = capture_rig
    capture(rig)
    rig.monotonic = 130
    rig.now = 151
    result = process(rig)
    assert result.status == 'rejected' and result.reason == 'application_expired'
    assert not records(rig)


def test_same_boot_monotonic_rollback_is_detected_after_restart(capture_rig):
    rig = capture_rig
    capture(rig)
    rig.monotonic = 99.999
    with pytest.raises(CaptureError):
        rig.new_capture().state()
    assert body(rig)['state'] == 'captured'


@pytest.mark.parametrize('committed', [False, True])
def test_new_boot_can_probe_old_commit_but_cannot_admit_fresh_work(capture_rig, committed):
    rig = capture_rig
    packet = capture(rig)
    if committed:
        assert rig.inbox.ingest_binary(OPERATION, packet, sender=SENDER,
            peer_check=lambda peer: peer == rig.peer).status == 'admitted'
    rig.monotonic = 5
    rig.now = 151
    restored = rig.new_capture('00000000-0000-4000-8000-000000000082')
    result = process(rig, owner=restored, inbox=rig.new())
    assert result.status == ('duplicate' if committed else 'rejected')
    if not committed:
        assert result.reason == 'original_boot_changed' and not records(rig)
    else:
        assert len(records(rig)) == 1 and records(rig)[0]['revision'] == 1


def test_new_boot_does_not_settle_interrupted_receiving_intention(capture_rig):
    rig = capture_rig
    packet = to_binary(sealed().decode())
    prepare(rig, packet)
    rig.monotonic = 5
    rig.now = 151
    restored = rig.new_capture('00000000-0000-4000-8000-000000000083')
    with pytest.raises(CaptureError):
        process(rig, owner=restored, inbox=rig.new())
    assert body(rig)['state'] == 'receiving' and restored.close(0) is False


@pytest.mark.parametrize('kind', [KeyboardInterrupt, asyncio.CancelledError])
@pytest.mark.parametrize('after_commit', [False, True])
def test_task_interruption_fences_capture_and_retains_original_operation(capture_rig, kind, after_commit):
    rig = capture_rig
    packet = to_binary(sealed().decode())
    prepare(rig, packet)
    original = rig.store.put
    def interrupt(identifier, record_kind, revision, payload, **kwargs):
        if payload['schema'] == SCHEMA and payload['body']['state'] == 'captured':
            if after_commit:
                original(identifier, record_kind, revision, payload, **kwargs)
            raise kind()
        return original(identifier, record_kind, revision, payload, **kwargs)
    rig.store.put = interrupt
    with pytest.raises(kind):
        rig.capture.complete(OPERATION, packet, authority_digest='a' * 64, permission_check=lambda: True)
    assert rig.capture._uncertain and rig.capture.close(0) is False
    assert body(rig)['state'] == ('captured' if after_commit else 'receiving')
    assert body(rig)['capture']['operation_id'] == OPERATION
    rig.store.put = original
    restored = rig.new_capture()
    assert restored.state() in ('receiving', 'captured')
    if not after_commit:
        restored.complete(OPERATION, packet, authority_digest='a' * 64, permission_check=lambda: True)
    result = process(rig, owner=restored, inbox=rig.new())
    assert result.status == 'admitted' and len(records(rig)) == 1
