# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import base64
import json
from dataclasses import replace

from nacl.public import SealedBox
from nacl.signing import SigningKey
import pytest

from meshticulum.privacy.mesh_envelope import (
    MAX_CONTENT_BYTES, MAX_LIFETIME_SECONDS, MAX_WIRE_BYTES, MeshEnvelopeError, Peer, SIGN_DOMAIN,
    WIRE_PREFIX, correlate_receipt, open_envelope, seal_delivery_receipt, seal_message, seal_read_receipt,
)


SENDER_KEY = SigningKey(bytes(range(32)))
RECIPIENT_KEY = SigningKey(bytes(range(32, 64)))
SENDER = Peer("chain-a", bytes(SENDER_KEY.verify_key))
RECIPIENT = Peer("chain-b", bytes(RECIPIENT_KEY.verify_key))
MESSAGE_ID = "00000000-0000-4000-8000-000000000001"
RECEIPT_ID = "00000000-0000-4000-8000-000000000002"


def seal(**changes):
    args = dict(sender=SENDER, recipient=RECIPIENT, message_id=MESSAGE_ID,
                content="synthetic private message", issued_at=100, expires_at=200)
    args.update(changes)
    return seal_message(SENDER_KEY, **args)


def opened(wire, **changes):
    args = dict(expected_sender=SENDER, expected_recipient=RECIPIENT, now=150)
    args.update(changes)
    return open_envelope(wire, RECIPIENT_KEY, **args)


def inner_value(wire):
    encrypted = base64.b64decode(wire[len(WIRE_PREFIX):])
    return json.loads(SealedBox(RECIPIENT_KEY.to_curve25519_private_key()).decrypt(encrypted))


def wrap(value, *, raw=None):
    if raw is None:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    cipher = SealedBox(RECIPIENT_KEY.verify_key.to_curve25519_public_key()).encrypt(raw)
    return WIRE_PREFIX + base64.b64encode(cipher).decode()


def test_encrypted_roundtrip_and_randomized_wire_stable_replay_identity():
    first, second = seal(), seal()
    assert first.wire != second.wire and first.digest == second.digest
    assert "synthetic" not in first.wire and "chain-a" not in first.wire
    result = opened(first.wire)
    assert result.content == "synthetic private message" and result.kind == "message"
    assert result.message_id == MESSAGE_ID and result.digest == first.digest
    assert result.replay_identity == ("chain-a", SENDER.public_key.hex(), MESSAGE_ID)
    assert opened(second.wire).replay_identity == result.replay_identity
    assert "synthetic" not in repr(result) and first.wire not in repr(first)
    assert len(first.wire.encode()) <= MAX_WIRE_BYTES
    assert first.encoded_bytes == len(first.wire.encode("ascii"))


@pytest.mark.parametrize("change", [dict(expected_sender=RECIPIENT),
    dict(expected_sender=Peer("other", SENDER.public_key)),
    dict(expected_recipient=Peer("other", RECIPIENT.public_key)),
    dict(now=99), dict(now=200), dict(now=True)])
def test_wrong_peer_chain_and_expiry(change):
    with pytest.raises(MeshEnvelopeError):
        opened(seal().wire, **change)


def test_wrong_decryption_key_and_signing_identity():
    with pytest.raises(MeshEnvelopeError):
        open_envelope(seal().wire, SENDER_KEY, expected_sender=SENDER,
                      expected_recipient=RECIPIENT, now=150)
    with pytest.raises(MeshEnvelopeError):
        seal_message(RECIPIENT_KEY, sender=SENDER, recipient=RECIPIENT, message_id=MESSAGE_ID,
                     content="test", issued_at=100, expires_at=200)


@pytest.mark.parametrize("change", [dict(content=""), dict(content="\0secret"),
    dict(content="a" * (MAX_CONTENT_BYTES + 1)), dict(content="é" * 257), dict(content="\ud800"),
        dict(issued_at=True), dict(expires_at=True), dict(expires_at=100),
        dict(expires_at=100 + MAX_LIFETIME_SECONDS + 1),
    dict(message_id="not-uuid"), dict(message_id="00000000-0000-0000-0000-000000000000")])
def test_invalid_message_parameters(change):
    with pytest.raises(MeshEnvelopeError):
        seal(**change)


def test_max_unicode_message_roundtrip():
    assert opened(seal(content="é" * 256).wire).content == "é" * 256


@pytest.mark.parametrize("mutation", ["content", "sender", "recipient", "type", "uuid", "signature", "extra"])
def test_tampered_signed_data_rejected(mutation):
    value = inner_value(seal().wire)
    if mutation == "signature":
        value["signature"] = base64.b64encode(bytes(64)).decode()
    elif mutation == "extra":
        value["body"]["photo"] = "not allowed"
    else:
        field = {"content": "content", "sender": "sender", "recipient": "recipient",
                 "type": "kind", "uuid": "message_id"}[mutation]
        value["body"][field] = "tampered"
    with pytest.raises(MeshEnvelopeError):
        opened(wrap(value))


def test_other_signature_domain_rejected():
    value = inner_value(seal().wire)
    canonical = json.dumps(value["body"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    value["signature"] = base64.b64encode(SENDER_KEY.sign(b"different\0" + canonical).signature).decode()
    with pytest.raises(MeshEnvelopeError):
        opened(wrap(value))


@pytest.mark.parametrize("fault", ["duplicate", "noncanonical", "oversize", "truncated", "base64", "prefix"])
def test_malformed_wire_and_inner(fault):
    envelope = seal()
    value = inner_value(envelope.wire)
    if fault == "duplicate":
        raw = json.dumps(value, separators=(",", ":")).encode()
        wire = wrap(value, raw=b'{"body":{},' + raw[1:])
    elif fault == "noncanonical":
        wire = wrap(value, raw=json.dumps(value, indent=2).encode())
    elif fault == "oversize":
        wire = "a" * (MAX_WIRE_BYTES + 1)
    elif fault == "truncated":
        wire = envelope.wire[:-8]
    elif fault == "base64":
        wire = envelope.wire + "\n"
    else:
        wire = "WRONG" + envelope.wire
    with pytest.raises(MeshEnvelopeError):
        opened(wire)


@pytest.mark.parametrize("kind", ["delivery", "read"])
def test_explicit_receipts_bind_original_and_reverse_peers(kind):
    original = seal()
    args = dict(sender=RECIPIENT, recipient=SENDER, message_id=RECEIPT_ID,
                original_id=MESSAGE_ID, original_digest=original.digest, issued_at=150, expires_at=250)
    if kind == "read":
        envelope = seal_read_receipt(RECIPIENT_KEY, **args, explicit_user_read=True)
    else:
        envelope = seal_delivery_receipt(RECIPIENT_KEY, **args)
    result = open_envelope(envelope.wire, SENDER_KEY, expected_sender=RECIPIENT,
                           expected_recipient=SENDER, now=160)
    assert result.kind == kind and result.content is None
    assert result.original_id == MESSAGE_ID and result.original_digest == original.digest
    assert result.message_id == RECEIPT_ID


@pytest.mark.parametrize("explicit", [False, None, 1, "true"])
def test_read_requires_literal_explicit_operation(explicit):
    with pytest.raises(MeshEnvelopeError):
        seal_read_receipt(RECIPIENT_KEY, sender=RECIPIENT, recipient=SENDER,
                          message_id=RECEIPT_ID, original_id=MESSAGE_ID, original_digest=seal().digest,
                          issued_at=150, expires_at=250, explicit_user_read=explicit)


def test_same_uuid_changed_payload_exposes_replay_conflict():
    first = opened(seal().wire)
    second = opened(seal(content="different").wire)
    assert first.replay_identity == second.replay_identity and first.digest != second.digest


def test_invalid_peer_conversion_and_chain():
    for chain, key in [("../bad", SENDER.public_key), ("a", bytes(32)), ("a", b"short")]:
        with pytest.raises(MeshEnvelopeError):
            Peer(chain, key)


@pytest.mark.parametrize("chain", ["UPPER:Chain._-09", "A:" * 64, "Z" * 128])
def test_chain_id_matches_contact_contract(chain):
    sender = Peer(chain, SENDER.public_key)
    recipient = Peer(chain[::-1], RECIPIENT.public_key)
    envelope = seal(sender=sender, recipient=recipient, content="é" * 256)
    result = opened(envelope.wire, expected_sender=sender, expected_recipient=recipient)
    assert result.content == "é" * 256
    assert result.sender == sender and result.recipient == recipient
    assert envelope.encoded_bytes <= MAX_WIRE_BYTES


@pytest.mark.parametrize("chain", ["A" * 129, "A\n", " A", "A ", "A/B", "é", ""])
def test_chain_id_exact_ascii_fullmatch(chain):
    with pytest.raises(MeshEnvelopeError):
        Peer(chain, SENDER.public_key)


def test_inner_byte_bound_rejects_without_truncation(monkeypatch):
    sender = Peer("A" * 128, SENDER.public_key)
    recipient = Peer("B" * 128, RECIPIENT.public_key)
    content = "x" + "\t" * 511
    envelope = seal(sender=sender, recipient=recipient, content=content)
    assert opened(envelope.wire, expected_sender=sender, expected_recipient=recipient).content == content
    monkeypatch.setattr("meshticulum.privacy.mesh_envelope.MAX_INNER_BYTES", 1024)
    with pytest.raises(MeshEnvelopeError):
        seal(sender=sender, recipient=recipient, content=content)


def receipt_fixture(kind="delivery", issued_at=150):
    original = seal()
    args = dict(sender=RECIPIENT, recipient=SENDER, message_id=RECEIPT_ID,
                original_id=MESSAGE_ID, original_digest=original.digest,
                issued_at=issued_at, expires_at=250)
    encrypted = (seal_delivery_receipt(RECIPIENT_KEY, **args) if kind == "delivery" else
                 seal_read_receipt(RECIPIENT_KEY, **args, explicit_user_read=True))
    receipt = open_envelope(encrypted.wire, SENDER_KEY, expected_sender=RECIPIENT,
                            expected_recipient=SENDER, now=160)
    history = dict(original_sender=SENDER, original_recipient=RECIPIENT, original_id=MESSAGE_ID,
                   original_digest=original.digest, original_issued_at=100)
    return receipt, history


@pytest.mark.parametrize("kind,status", [("delivery", "peer_delivery_asserted"), ("read", "peer_read_asserted")])
def test_correlate_returns_only_peer_assertion(kind, status):
    receipt, history = receipt_fixture(kind)
    assert correlate_receipt(receipt, **history) == status
    assert correlate_receipt(receipt, **history) == status


@pytest.mark.parametrize("field,value", [
    ("original_sender", RECIPIENT), ("original_recipient", SENDER),
    ("original_sender", Peer("OTHER:CHAIN", SENDER.public_key)),
    ("original_recipient", Peer("OTHER:CHAIN", RECIPIENT.public_key)),
    ("original_id", RECEIPT_ID), ("original_id", "invalid"),
    ("original_digest", "f" * 64), ("original_digest", "F" * 64),
    ("original_kind", "delivery"), ("original_kind", "read"), ("original_kind", True),
    ("original_issued_at", 151), ("original_issued_at", True), ("original_issued_at", -1),
    ("original_sender", SENDER.to_dict()),
])
def test_correlation_rejects_mismatched_stored_history(field, value):
    receipt, history = receipt_fixture()
    history[field] = value
    with pytest.raises(MeshEnvelopeError):
        correlate_receipt(receipt, **history)


def test_receipt_may_be_issued_at_original_time_not_before():
    receipt, history = receipt_fixture(issued_at=100)
    assert correlate_receipt(receipt, **history) == "peer_delivery_asserted"
    receipt, history = receipt_fixture(issued_at=99)
    with pytest.raises(MeshEnvelopeError):
        correlate_receipt(receipt, **history)


def test_correlation_requires_authenticated_envelope_not_dict_or_replacement():
    receipt, history = receipt_fixture()
    for fake in ({}, replace(receipt), replace(receipt, kind="read"), opened(seal().wire)):
        with pytest.raises(MeshEnvelopeError):
            correlate_receipt(fake, **history)


def test_correlation_rechecks_signature_and_digest_even_after_object_tampering():
    receipt, history = receipt_fixture()
    object.__setattr__(receipt, "kind", "read")
    with pytest.raises(MeshEnvelopeError):
        correlate_receipt(receipt, **history)
    receipt, history = receipt_fixture()
    object.__setattr__(receipt, "_signature", bytes(64))
    with pytest.raises(MeshEnvelopeError):
        correlate_receipt(receipt, **history)
