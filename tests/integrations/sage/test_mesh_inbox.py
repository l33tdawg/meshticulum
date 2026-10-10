# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import pytest
pytest.importorskip("sage_sdk", reason="optional SAGE adapter requires compatible SDK")

"""Real SDK MockTransport plus offline crypto and synthetic radio frames."""

import base64
import json
import uuid
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from meshticulum.privacy.mesh_envelope import Peer, seal_delivery_receipt, seal_message
from meshticulum.integrations.sage.mesh_fragments import split_envelope
from meshticulum.integrations.sage.mesh_inbox import ASSEMBLY_SCHEMA, INBOX_SCHEMA, REJECTION_SCHEMA, InboxError, MeshInbox, inbox_id
from meshticulum.integrations.sage.mesh_receiver import MeshReceiver, ReceiverError
from meshticulum.integrations.sage.meshcore_transport import decode_incoming
from meshticulum.integrations.sage.message_contacts import RecipientID
from meshticulum.integrations.sage.message_storage import MessageStorageProbe
from meshticulum.integrations.sage.workflow_store import WorkflowStore


SENDER_KEY = SigningKey(bytes(range(32)))
KEY = SigningKey(bytes(range(32, 64)))
SENDER = Peer("sender", bytes(SENDER_KEY.verify_key))
RECIPIENT = Peer("recipient", bytes(KEY.verify_key))
REMOTE = "00000000-0000-4000-8000-000000000001"
OPERATION = "00000000-0000-4000-8000-000000000002"


def sealed(**changes):
    values = dict(sender=SENDER, recipient=RECIPIENT, message_id=REMOTE,
                  content="private synthetic", issued_at=100, expires_at=1000)
    values.update(changes)
    return seal_message(SENDER_KEY, **values).wire.encode()


def frame(text):
    return decode_incoming(b"\x07" + b"abcdef" + bytes(6) + text.encode("ascii"))


@pytest.fixture
def rig():
    fixture = SimpleNamespace(rows={}, now=150, live=True, storage=True, peer=SENDER,
                              failure=None, puts=0, instance="b" * 64, after_put=None)
    identity = AgentIdentity(KEY)
    def handler(request):
        assert request.headers["X-Agent-ID"] == identity.agent_id and request.headers["X-Signature"]
        if request.url.path == "/v1/messages/storage":
            return httpx.Response(200, json=dict(schema="sage.message-storage.v1", instance_id=fixture.instance,
                agent_id=identity.agent_id, encryption_expected=True, vault_active=True,
                vault_generation="1", stable=True, canonical_send_idempotency=True,
                encrypted_send_admission=True))
        if request.url.path == "/v1/workflows":
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1", items=[],
                                                 next_after=None, has_more=False))
        identifier = request.url.path.rsplit("/", 1)[1]
        if request.method == "GET":
            return httpx.Response(200 if identifier in fixture.rows else 404,
                                  json=fixture.rows.get(identifier, {}))
        body = json.loads(request.content)
        fixture.puts += 1
        revision = fixture.rows.get(identifier, {}).get("revision", 0)
        target = body["payload"]["schema"]
        if body["payload"].get("state") == "complete":
            target = "complete"
        if fixture.failure == (target, "before"):
            raise httpx.ReadTimeout("synthetic")
        if revision != body["expected_revision"] or fixture.failure == (target, "conflict"):
            return httpx.Response(409, json={})
        fixture.rows[identifier] = dict(schema="sage.workflow-journal.v1", agent_id=identity.agent_id,
            record_id=identifier, revision=revision + 1, kind=body["kind"], payload=body["payload"],
            trust="untrusted_auxiliary")
        if fixture.after_put:
            fixture.after_put()
        if fixture.failure == (target, "after"):
            raise httpx.ReadTimeout("synthetic")
        return httpx.Response(200, json=fixture.rows[identifier])
    endpoint = "http://127.0.0.1:19100"
    client = SageClient(endpoint, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, timeout=2, trust_env=False,
        follow_redirects=False, transport=httpx.MockTransport(handler))
    probe = MessageStorageProbe(client, actor=RecipientID("recipient", identity.agent_id), endpoint=endpoint)
    store = WorkflowStore(client, storage=probe)
    fixture.store = store
    fixture.new = lambda: MeshInbox(store, recipient=RECIPIENT, recipient_signing_key=KEY,
        resolve_peer=lambda message: fixture.peer, access_check=lambda: fixture.live,
        storage_check=lambda: fixture.storage, clock=lambda: fixture.now)
    fixture.inbox = fixture.new()
    yield fixture
    client._client.close()


def feed(inbox, wire):
    return [inbox.ingest(str(uuid.uuid5(uuid.UUID(OPERATION), text)), frame(text))
            for text in split_envelope(wire)]


def test_custody_deposit_is_stored_without_opening(rig):
    from meshticulum.integrations.sage.mesh_custody import wrap_custody
    calls = []

    class Sink:
        def admit(self, deposit, *, source, operation_id=None):
            calls.append((deposit, source, operation_id))
            return "stored"

    inbox = MeshInbox(rig.store, recipient=RECIPIENT, recipient_signing_key=KEY,
        resolve_peer=lambda message: rig.peer, access_check=lambda: rig.live,
        storage_check=lambda: rig.storage, clock=lambda: rig.now, custody=Sink())
    final = bytes.fromhex("aabbccddeeff")
    wire = wrap_custody(recipient_prefix=final, message_id=REMOTE, envelope_wire=sealed().decode(),
                        expires_at=900, envelope_expires_at=900, now=rig.now).encode()
    results = feed(inbox, wire)
    assert results[-1].status == "custodied"
    assert len(calls) == 1
    deposit, source, operation_id = calls[0]
    assert deposit.recipient_prefix == final
    assert source == f"{SENDER.chain_id}:{SENDER.public_key.hex()}"
    assert uuid.UUID(operation_id)
    # Custody off: the same shape is refused, not silently admitted.
    other = "00000000-0000-4000-8000-000000000009"
    wire = wrap_custody(recipient_prefix=final, message_id=other, envelope_wire=sealed().decode(),
                        expires_at=900, envelope_expires_at=900, now=rig.now).encode()
    results = feed(rig.new(), wire)
    assert results[-1].status == "rejected" and results[-1].reason == "unsupported"


def records(rig):
    return [record for record in rig.rows.values() if record["payload"]["schema"] == INBOX_SCHEMA]


def test_restart_partial_complete_and_same_wire_duplicate(rig):
    wire = sealed()
    parts = split_envelope(wire)
    assert rig.inbox.ingest(OPERATION, frame(parts[0])).status == "partial"
    assert rig.new().ingest(OPERATION, frame(parts[0])).status == "partial"
    for part in parts[1:]:
        result = rig.new().ingest(OPERATION, frame(part))
    assert result.status == "admitted"
    assert result.record_id == inbox_id(SENDER, REMOTE) and result.remote_id == REMOTE
    assert rig.new().ingest(OPERATION, frame(parts[-1])).status == "duplicate"
    assert len(records(rig)) == 1 and records(rig)[0]["revision"] == 1
    payload = records(rig)[0]["payload"]
    assert payload["wire"].encode() == wire and payload["operation_id"] == OPERATION
    assert "private synthetic" not in json.dumps(rig.rows)


def test_receiver_callable_literal_true_only_after_persist(rig):
    part = frame(split_envelope(sealed())[0])
    assert rig.inbox(OPERATION, part) is True
    rig.failure = (ASSEMBLY_SCHEMA, "before")
    with pytest.raises(InboxError):
        rig.inbox(OPERATION, frame(split_envelope(sealed())[0]))


@pytest.mark.parametrize("mode", ["before", "after", "conflict"])
def test_partial_cas_uncertain_explicit_recovery(rig, mode):
    part = frame(split_envelope(sealed())[0])
    rig.failure = (ASSEMBLY_SCHEMA, mode)
    with pytest.raises(InboxError):
        rig.inbox.ingest(OPERATION, part)
    assert rig.puts == 1
    assert rig.inbox._uncertain
    rig.failure = None
    assert rig.new().ingest(OPERATION, part).status == "partial"


@pytest.mark.parametrize("target", [INBOX_SCHEMA, "complete"])
@pytest.mark.parametrize("mode", ["before", "after", "conflict"])
def test_final_cas_unknown_keeps_replayable_assembly(rig, target, mode):
    parts = split_envelope(sealed())
    for text in parts[:-1]:
        rig.inbox.ingest(OPERATION, frame(text))
    rig.failure = (target, mode)
    with pytest.raises(InboxError):
        rig.inbox.ingest(OPERATION, frame(parts[-1]))
    assert rig.inbox._uncertain
    rig.failure = None
    result = rig.new().ingest(OPERATION, frame(parts[-1]))
    assert result.status in ("admitted", "duplicate")
    assert len(records(rig)) == 1 and records(rig)[0]["revision"] == 1


def test_randomized_reencryption_same_body_dedups(rig):
    assert feed(rig.inbox, sealed())[-1].status == "admitted"
    assert feed(rig.inbox, sealed())[-1].status == "duplicate"
    assert len(records(rig)) == 1


def test_authenticated_uuid_equivocation_never_overwrites(rig):
    feed(rig.inbox, sealed())
    before = json.dumps(records(rig))
    assert feed(rig.inbox, sealed(content="different"))[-1].reason == "equivocation"
    assert json.dumps(records(rig)) == before


@pytest.mark.parametrize("peer", [None, Peer("wrong-chain", SENDER.public_key), RECIPIENT])
def test_full_contact_and_signature_binding_required(rig, peer):
    rig.peer = peer
    assert feed(rig.inbox, sealed())[-1].status == "rejected"
    assert not records(rig)


def test_per_peer_bound_and_expiry_reuse_preserves_inbox(rig):
    feed(rig.inbox, sealed())
    for identifier in (str(uuid.UUID(int=10)), str(uuid.UUID(int=11))):
        rig.inbox.ingest(OPERATION, frame(split_envelope(sealed(message_id=identifier))[0]))
    with pytest.raises(InboxError):
        rig.inbox.ingest(OPERATION, frame(split_envelope(sealed(message_id=str(uuid.UUID(int=12))))[0]))
    rig.now = 1051
    assert rig.new().ingest(OPERATION, frame(split_envelope(sealed(issued_at=1050, expires_at=2000))[0])).status == "partial"
    assert len(records(rig)) == 1 and len(rig.rows) <= 9


def test_completed_slots_reusable_for_many_messages(rig):
    for number in range(4):
        assert feed(rig.inbox, sealed(message_id=str(uuid.UUID(int=number + 1))))[-1].status == "admitted"
    assert len(records(rig)) == 4


@pytest.mark.parametrize("change", [dict(schema="other"), dict(recipient=SENDER.to_dict()),
    dict(operation_id="bad"), dict(parts={}), dict(expires_at=151), dict(extra=True)])
def test_unrelated_or_corrupt_slot_not_message(rig, change):
    part = frame(split_envelope(sealed())[0])
    rig.inbox.ingest(OPERATION, part)
    next(iter(rig.rows.values()))["payload"].update(change)
    with pytest.raises(InboxError):
        rig.new().ingest(OPERATION, part)
    assert not records(rig)


@pytest.mark.parametrize("gate", ["live", "storage"])
def test_revoked_guard_no_ack(rig, gate):
    setattr(rig, gate, False)
    with pytest.raises(InboxError):
        rig.inbox(OPERATION, frame(split_envelope(sealed())[0]))
    assert not rig.rows


def test_expired_envelope_never_committed(rig):
    rig.now = 1000
    assert feed(rig.inbox, sealed())[-1].reason == "invalid_envelope"
    assert not records(rig)


def test_qualified_id_and_namespaces(rig):
    assert inbox_id(SENDER, REMOTE) != inbox_id(Peer("another", SENDER.public_key), REMOTE)
    assert inbox_id(SENDER, REMOTE) not in rig.inbox._slots


def test_clock_rollback_restart_detected_from_slot(rig):
    part = frame(split_envelope(sealed())[0])
    rig.inbox.ingest(OPERATION, part)
    rig.now = 149
    with pytest.raises(InboxError):
        rig.new().ingest(OPERATION, part)


def test_global_bound_even_with_distinct_verified_contacts(rig):
    part = frame(split_envelope(sealed())[0])
    for number in range(8):
        rig.peer = Peer("peer-" + str(number // 2), SENDER.public_key)
        wire = sealed(message_id=str(uuid.UUID(int=number + 1)))
        assert rig.inbox.ingest(OPERATION, frame(split_envelope(wire)[0])).status == "partial"
    rig.peer = Peer("peer-fifth", SENDER.public_key)
    with pytest.raises(InboxError):
        rig.inbox.ingest(OPERATION, part)
    assert len(rig.rows) == 8


def test_max_wire_partial_storage_stays_below_sdk_limit(rig):
    parts = split_envelope(b"x" * 4096)
    for text in parts[:-1]:
        assert rig.inbox.ingest(OPERATION, frame(text)).status == "partial"
    assert rig.inbox.ingest(OPERATION, frame(parts[-1])).status == "rejected"
    assert len(rig.rows) == 2 and not records(rig)
    payload = next(iter(rig.rows.values()))["payload"]
    assert len(payload["parts"]) == 86
    assert len(json.dumps(payload, ensure_ascii=True).encode()) < 16384


def test_conflicting_fragment_never_replaces_durable_partial(rig):
    text = split_envelope(sealed())[0]
    rig.inbox.ingest(OPERATION, frame(text))
    before = json.dumps([row for row in rig.rows.values() if row["payload"]["schema"] == ASSEMBLY_SCHEMA])
    binary = base64.b64decode(text[4:])
    tampered = "SL1:" + base64.b64encode(binary[:-1] + bytes([binary[-1] ^ 1])).decode()
    assert rig.inbox.ingest(OPERATION, frame(tampered)).reason == "fragment_conflict"
    assert json.dumps([row for row in rig.rows.values() if row["payload"]["schema"] == ASSEMBLY_SCHEMA]) == before


def test_final_fragment_digest_mismatch_no_commit(rig):
    parts = list(split_envelope(sealed()))
    binary = base64.b64decode(parts[-1][4:])
    parts[-1] = "SL1:" + base64.b64encode(binary[:-1] + bytes([binary[-1] ^ 1])).decode()
    for text in parts[:-1]:
        rig.inbox.ingest(OPERATION, frame(text))
    assert rig.inbox.ingest(OPERATION, frame(parts[-1])).status == "rejected"
    assert not records(rig)


def test_unrelated_inbox_id_collision_is_not_duplicate(rig):
    wire = sealed()
    feed(rig.inbox, wire)
    rig.rows[inbox_id(SENDER, REMOTE)]["payload"]["schema"] = "sage.lantern.mesh-receiver.v1"
    with pytest.raises(InboxError):
        feed(rig.new(), wire)


def test_no_silent_inbox_revision_mutation(rig):
    wire = sealed()
    feed(rig.inbox, wire)
    rig.rows[inbox_id(SENDER, REMOTE)]["revision"] = 2
    with pytest.raises(InboxError):
        feed(rig.new(), wire)


def test_binary_and_channel_frames_not_treated_as_peer_fragments(rig):
    binary = decode_incoming(bytes((27, 0, 0, 0, 1, 0, 2, 0, 0)))
    assert rig.inbox.ingest(OPERATION, binary).reason == "unsupported"
    assert not records(rig)


def test_lost_live_access_after_contact_resolution(rig):
    def resolve(message):
        rig.live = False
        return SENDER
    rig.inbox._resolve = resolve
    with pytest.raises(InboxError):
        rig.inbox.ingest(OPERATION, frame(split_envelope(sealed())[0]))
    assert not rig.rows


def test_receipt_is_stored_as_assertion_not_interpreted(rig):
    wire = seal_delivery_receipt(SENDER_KEY, sender=SENDER, recipient=RECIPIENT,
        message_id=REMOTE, original_id=OPERATION, original_digest="a" * 64,
        issued_at=100, expires_at=1000).wire.encode()
    result = feed(rig.inbox, wire)[-1]
    assert result.status == "admitted" and result.envelope_kind == "delivery"
    assert not hasattr(result, "delivered") and not hasattr(result, "read")
    assert len(records(rig)) == 1


@pytest.mark.parametrize("change", ["instance", "live", "peer"])
def test_revocation_during_partial_write_keeps_capture_unacknowledged(rig, change):
    value = "c" * 64 if change == "instance" else False if change == "live" else None
    rig.after_put = lambda: setattr(rig, change, value)
    with pytest.raises(InboxError):
        rig.inbox(OPERATION, frame(split_envelope(sealed())[0]))
    assert len(rig.rows) == 1


def receiver(rig, messages):
    class Driver:
        def receive_next(self, *, authorize, persist):
            assert authorize() is True
            message = messages.pop(0)
            assert persist(message) is True
            return message
    return MeshReceiver(rig.store, Driver(), slot_id=str(uuid.UUID(int=999)),
        access_check=lambda: rig.live, storage_check=lambda: rig.storage)


def test_invalid_capture_disposed_then_next_valid_message_commits(rig):
    messages = [frame("not-a-fragment SECRET")] + [frame(text) for text in split_envelope(sealed())]
    worker = receiver(rig, messages)
    assert worker.receive_next() == "captured"
    assert worker.process_pending(rig.inbox) is True
    assert worker.state() == "idle"
    dispositions = [row for row in rig.rows.values() if row["payload"]["schema"] == REJECTION_SCHEMA]
    assert len(dispositions) == 1
    assert dispositions[0]["payload"]["reason"] == "invalid_fragment"
    assert "SECRET" not in json.dumps(dispositions)
    while messages:
        assert worker.receive_next() == "captured"
        assert worker.process_pending(rig.inbox) is True
    assert len(records(rig)) == 1 and worker.close(0)


@pytest.mark.parametrize("mode", ["before", "after", "conflict"])
def test_uncertain_rejection_retains_capture_until_explicit_reconcile(rig, mode):
    messages = [frame("invalid")]
    worker = receiver(rig, messages)
    worker.receive_next()
    rig.failure = (REJECTION_SCHEMA, mode)
    with pytest.raises(ReceiverError):
        worker.process_pending(rig.inbox)
    assert rig.inbox._uncertain
    assert worker.state() == "captured"
    with pytest.raises(ReceiverError):
        worker.receive_next()
    rig.failure = None
    restarted = receiver(rig, messages)
    assert restarted.process_pending(rig.new()) is True
    assert restarted.state() == "idle"
    assert len([row for row in rig.rows.values() if row["payload"]["schema"] == REJECTION_SCHEMA]) == 1


def test_disposition_ring_bounded_and_exact_operation_bound(rig):
    message = frame("invalid")
    for number in range(1, 41):
        assert rig.inbox.ingest(str(uuid.UUID(int=number)), message).status == "rejected"
    assert len(rig.rows) == 16
    before = rig.puts
    assert rig.new().ingest(str(uuid.UUID(int=40)), message).status == "rejected"
    assert rig.puts == before
    with pytest.raises(InboxError):
        rig.inbox.ingest(str(uuid.UUID(int=40)), frame("different"))


def test_unknown_peer_disposition_but_temporary_lookup_failure_not_acked(rig):
    message = frame(split_envelope(sealed())[0])
    def unavailable(message):
        raise TimeoutError()
    rig.inbox._resolve = unavailable
    with pytest.raises(InboxError):
        rig.inbox(OPERATION, message)
    assert not rig.rows
    rig.peer = None
    assert rig.new().ingest(OPERATION, message).reason == "unknown_peer"


@pytest.mark.parametrize("gate", ["live", "storage"])
def test_access_revoked_during_disposition_never_clears_capture(rig, gate):
    worker = receiver(rig, [frame("invalid")])
    worker.receive_next()
    rig.after_put = lambda: setattr(rig, gate, False)
    with pytest.raises(ReceiverError):
        worker.process_pending(rig.inbox)
    rig.after_put = None
    setattr(rig, gate, True)
    assert worker.state() == "captured"
    assert worker.process_pending(rig.new()) is True


def test_capacity_retains_capture_without_reject_disposition(rig):
    for number in (10, 11):
        rig.inbox.ingest(str(uuid.UUID(int=number)),
            frame(split_envelope(sealed(message_id=str(uuid.UUID(int=number))))[0]))
    worker = receiver(rig, [frame(split_envelope(sealed())[0])])
    worker.receive_next()
    with pytest.raises(ReceiverError):
        worker.process_pending(rig.inbox)
    assert worker.state() == "captured"
    assert not any(row["payload"]["schema"] == REJECTION_SCHEMA for row in rig.rows.values())


def test_corrupt_disposition_not_treated_as_durable_reject(rig):
    message = frame("invalid")
    rig.inbox.ingest(OPERATION, message)
    next(iter(rig.rows.values()))["payload"]["reason"] = "arbitrary"
    with pytest.raises(InboxError):
        rig.new()(OPERATION, message)


def test_invalid_caller_message_not_blanket_acked(rig):
    with pytest.raises(InboxError):
        rig.inbox(OPERATION, object())
    assert not rig.rows


def test_historical_after_expiry_does_not_allow_live_reingestion(rig):
    wire = sealed()
    admitted = feed(rig.inbox, wire)[-1]
    rig.now = 1000
    before = rig.puts
    opened = rig.new().historical_read(admitted.record_id)
    assert opened.message_id == REMOTE and opened.content == "private synthetic"
    assert rig.puts == before
    assert feed(rig.new(), wire)[-1].status == "rejected"
    assert rig.new().historical_read(admitted.record_id).digest == opened.digest


def test_duplicate_preserves_first_attestation_and_wire(rig):
    first = feed(rig.inbox, sealed())[-1]
    before = json.dumps(rig.rows[first.record_id])
    rig.now = 200
    assert feed(rig.new(), sealed())[-1].status == "duplicate"
    assert json.dumps(rig.rows[first.record_id]) == before
    assert rig.rows[first.record_id]["payload"]["attestation"]["body"]["admitted_at"] == 150


@pytest.mark.parametrize("field,value", [("admitted_at", 100), ("admitted_at", True),
    ("sender", RECIPIENT.to_dict()), ("recipient", SENDER.to_dict()), ("remote_id", OPERATION),
    ("local_id", OPERATION), ("wire_sha256", "a" * 64), ("envelope_digest", "b" * 64),
    ("envelope_kind", "read"), ("schema", "other"), ("operation_id", REMOTE)])
def test_altered_attestation_rejected_before_decrypt(rig, monkeypatch, field, value):
    result = feed(rig.inbox, sealed())[-1]
    rig.rows[result.record_id]["payload"]["attestation"]["body"][field] = value
    def no_decrypt(*args, **kwargs):
        pytest.fail("unverified attestation reached envelope decryption")
    monkeypatch.setattr("meshticulum.integrations.sage.mesh_inbox.open_envelope", no_decrypt)
    with pytest.raises(InboxError):
        rig.new().historical_read(result.record_id)


@pytest.mark.parametrize("field", ["wire", "sender", "recipient", "remote_id", "digest", "operation_id"])
def test_altered_record_binding_rejected(rig, field):
    result = feed(rig.inbox, sealed())[-1]
    value = rig.rows[result.record_id]["payload"]
    replacements = dict(wire=sealed().decode(), sender=RECIPIENT.to_dict(), recipient=SENDER.to_dict(),
                        remote_id=OPERATION, digest="a" * 64, operation_id=REMOTE)
    value[field] = replacements[field]
    with pytest.raises(InboxError):
        rig.new().historical_read(result.record_id)


@pytest.mark.parametrize("change", ["missing", "forged", "cross_row"])
def test_no_legacy_backfill_or_foreign_signature(rig, change):
    result = feed(rig.inbox, sealed())[-1]
    payload = rig.rows[result.record_id]["payload"]
    if change == "missing":
        del payload["attestation"]
    elif change == "forged":
        from meshticulum.integrations.sage.mesh_inbox import ADMISSION_DOMAIN
        body = payload["attestation"]["body"]
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        payload["attestation"]["signature"] = base64.b64encode(SENDER_KEY.sign(ADMISSION_DOMAIN + canonical).signature).decode()
    else:
        other = feed(rig.inbox, sealed(message_id=OPERATION))[-1]
        payload["attestation"] = rig.rows[other.record_id]["payload"]["attestation"]
    before = rig.puts
    with pytest.raises(InboxError):
        rig.new().historical_read(result.record_id)
    assert rig.puts == before


def test_copied_row_at_different_local_uuid_unavailable(rig):
    result = feed(rig.inbox, sealed())[-1]
    copied = json.loads(json.dumps(rig.rows[result.record_id]))
    copied["record_id"] = OPERATION
    rig.rows[OPERATION] = copied
    with pytest.raises(InboxError):
        rig.inbox.historical_read(OPERATION)


@pytest.mark.parametrize("gate", ["live", "storage"])
def test_historical_read_requires_current_gates(rig, gate):
    result = feed(rig.inbox, sealed())[-1]
    setattr(rig, gate, False)
    with pytest.raises(InboxError):
        rig.new().historical_read(result.record_id)


def test_historical_read_last_moment_lock_fences_disclosure(rig, monkeypatch):
    result = feed(rig.inbox, sealed())[-1]
    from meshticulum.privacy.mesh_envelope import open_envelope
    def revoke(*args, **kwargs):
        opened = open_envelope(*args, **kwargs)
        rig.live = False
        return opened
    monkeypatch.setattr("meshticulum.integrations.sage.mesh_inbox.open_envelope", revoke)
    with pytest.raises(InboxError):
        rig.new().historical_read(result.record_id)


def test_uncertain_admission_reconcile_preserves_signed_time(rig):
    parts = split_envelope(sealed())
    for text in parts[:-1]:
        rig.inbox.ingest(OPERATION, frame(text))
    rig.failure = (INBOX_SCHEMA, "after")
    with pytest.raises(InboxError):
        rig.inbox.ingest(OPERATION, frame(parts[-1]))
    record = records(rig)[0]
    before = json.dumps(record)
    rig.failure = None
    rig.now = 175
    assert rig.new().ingest(OPERATION, frame(parts[-1])).status == "duplicate"
    assert json.dumps(records(rig)[0]) == before
    rig.now = 1000
    assert rig.new().historical_read(record["record_id"]).message_id == REMOTE


def test_historical_receipt_read_is_assertion_only(rig):
    wire = seal_delivery_receipt(SENDER_KEY, sender=SENDER, recipient=RECIPIENT,
        message_id=REMOTE, original_id=OPERATION, original_digest="a" * 64,
        issued_at=100, expires_at=1000).wire.encode()
    result = feed(rig.inbox, wire)[-1]
    rig.now = 1001
    opened = rig.new().historical_read(result.record_id)
    assert opened.kind == "delivery" and opened.content is None
    assert not hasattr(opened, "delivered")


def test_live_duplicate_does_not_backfill_legacy_attestation(rig):
    result = feed(rig.inbox, sealed())[-1]
    del rig.rows[result.record_id]["payload"]["attestation"]
    before = json.dumps(rig.rows[result.record_id])
    with pytest.raises(InboxError):
        feed(rig.new(), sealed())
    assert json.dumps(rig.rows[result.record_id]) == before


def test_expiry_during_assembly_persistence_never_signed(rig):
    parts = split_envelope(sealed())
    for text in parts[:-1]:
        rig.inbox.ingest(OPERATION, frame(text))
    rig.after_put = lambda: setattr(rig, "now", 1000)
    result = rig.inbox.ingest(OPERATION, frame(parts[-1]))
    assert result.status == "rejected" and not records(rig)
