# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Binary transport must preserve the existing encrypted identity contract."""
import base64

from nacl.signing import SigningKey
import pytest

from meshticulum.privacy.mesh_binary_envelope import (
    BinaryEnvelopeError, MAX_BINARY_BYTES, MAX_CIPHERTEXT_BYTES, to_binary, to_text,
)
from meshticulum.privacy.mesh_envelope import (
    MAX_WIRE_BYTES, MeshEnvelopeError, Peer, correlate_receipt, open_envelope,
    seal_delivery_receipt, seal_message, seal_read_receipt,
)


SENDER_KEY = SigningKey(bytes(range(32)))
RECIPIENT_KEY = SigningKey(bytes(range(32, 64)))
SENDER = Peer("binary-test-a", bytes(SENDER_KEY.verify_key))
RECIPIENT = Peer("binary-test-c", bytes(RECIPIENT_KEY.verify_key))
MESSAGE_ID = "00000000-0000-4000-8000-000000000001"
ATTEMPT_ID = "00000000-0000-4000-8000-000000000002"
RECEIPT_ID = "00000000-0000-4000-8000-000000000003"


def message(version=1, content="synthetic private message", sender=SENDER, recipient=RECIPIENT):
    renewal = {} if version == 1 else dict(
        attempt_id=ATTEMPT_ID, logical_issued_at=1, logical_expires_at=99)
    return seal_message(SENDER_KEY, sender=sender, recipient=recipient,
        message_id=MESSAGE_ID, content=content, issued_at=100, expires_at=200, **renewal)


def opened(packet, **changes):
    args = dict(expected_sender=SENDER, expected_recipient=RECIPIENT, now=150)
    args.update(changes)
    return open_envelope(to_text(packet), RECIPIENT_KEY, **args)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("content", ["x", "x" * 512, "é" * 256, "\n\t\\\"" * 128])
def test_exact_ciphertext_signature_and_replay_identity_survive(version, content):
    sealed = message(version, content)
    packet = to_binary(sealed.wire)
    assert packet[:3] == b"LM" + bytes([version])
    assert packet[3:] == base64.b64decode(sealed.wire[5:])
    assert to_text(packet) == sealed.wire
    original = open_envelope(sealed.wire, RECIPIENT_KEY,
        expected_sender=SENDER, expected_recipient=RECIPIENT, now=150)
    result = opened(packet)
    assert result == original and result._signature == original._signature
    assert result.replay_identity == original.replay_identity
    assert result.logical_digest == original.logical_digest
    assert result.digest == sealed.digest and result.content == content
    assert len(packet) < sealed.encoded_bytes


@pytest.mark.parametrize("version", [1, 2])
def test_maximum_peer_names_and_escaped_content_stay_lossless(version):
    sender = Peer("a" * 128, SENDER.public_key)
    recipient = Peer("c" * 128, RECIPIENT.public_key)
    sealed = message(version, "\t" * 511 + "x", sender, recipient)
    packet = to_binary(sealed.wire)
    assert to_text(packet) == sealed.wire and len(packet) <= MAX_BINARY_BYTES
    assert opened(packet, expected_sender=sender, expected_recipient=recipient).digest == sealed.digest


@pytest.mark.parametrize("kind", ["delivery", "read"])
def test_receipt_still_requires_original_reversed_peer_binding(kind):
    original = message()
    fields = dict(sender=RECIPIENT, recipient=SENDER, message_id=RECEIPT_ID,
        original_id=MESSAGE_ID, original_digest=original.digest, issued_at=150, expires_at=200)
    sealed = (seal_delivery_receipt(RECIPIENT_KEY, **fields) if kind == "delivery"
              else seal_read_receipt(RECIPIENT_KEY, **fields, explicit_user_read=True))
    packet = to_binary(sealed.wire)
    assert to_text(packet) == sealed.wire
    result = open_envelope(to_text(packet), SENDER_KEY,
        expected_sender=RECIPIENT, expected_recipient=SENDER, now=160)
    binding = dict(original_sender=SENDER, original_recipient=RECIPIENT,
        original_id=MESSAGE_ID, original_digest=original.digest, original_issued_at=100)
    assert correlate_receipt(result, **binding) == "peer_" + kind + "_asserted"
    with pytest.raises(MeshEnvelopeError):
        correlate_receipt(result, **{**binding, "original_digest": "0" * 64})


@pytest.mark.parametrize("wire", [None, True, b"LME1.payload", "private plaintext",
    "LME0." + "A" * 80, "LME3." + "A" * 80, "LME1.", "LME1." + "A" * (MAX_WIRE_BYTES + 1),
    "LME1." + "é" * 80, "LME1." + "A" * 80 + "\n", "LME1." + "A" * 65])
def test_text_refuses_unknown_malformed_oversized_or_plaintext_input(wire):
    with pytest.raises(BinaryEnvelopeError, match="^invalid_binary_mesh_envelope$"):
        to_binary(wire)


def test_noncanonical_padding_bits_are_not_silently_normalized():
    canonical = base64.b64encode(bytes(49)).decode("ascii")
    noncanonical = canonical[:-3] + "B=="
    assert base64.b64decode(noncanonical, validate=True) == bytes(49)
    with pytest.raises(BinaryEnvelopeError):
        to_binary("LME1." + noncanonical)


@pytest.mark.parametrize("ciphertext_bytes", [0, 1, 47, 48, MAX_CIPHERTEXT_BYTES + 1])
def test_ciphertext_bounds_apply_in_both_directions(ciphertext_bytes):
    ciphertext = bytes(ciphertext_bytes)
    with pytest.raises(BinaryEnvelopeError):
        to_binary("LME1." + base64.b64encode(ciphertext).decode("ascii"))
    with pytest.raises(BinaryEnvelopeError):
        to_text(b"LM\x01" + ciphertext)


@pytest.mark.parametrize("packet", [None, True, "LM1text", b"", b"LM\x00" + bytes(60),
    b"LM\x03" + bytes(60), b"XX\x01" + bytes(60), b"private plaintext" * 10,
    bytearray(b"LM\x01" + bytes(60))])
def test_binary_refuses_unknown_or_non_record_input(packet):
    with pytest.raises(BinaryEnvelopeError, match="^invalid_binary_mesh_envelope$"):
        to_text(packet)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("fault", ["ciphertext", "version", "truncated", "appended"])
def test_format_conversion_cannot_bypass_authentication(version, fault):
    packet = to_binary(message(version).wire)
    if fault == "ciphertext":
        packet = packet[:-1] + bytes([packet[-1] ^ 1])
    elif fault == "version":
        packet = b"LM" + bytes([3 - version]) + packet[3:]
    elif fault == "truncated":
        packet = packet[:-1]
    else:
        packet += b"\x00"
    # These are structurally bounded records. Only recipient authentication
    # can accept them, and it must reject every mutation.
    assert to_text(packet)
    with pytest.raises(MeshEnvelopeError):
        opened(packet)


@pytest.mark.parametrize("change", [dict(now=99), dict(now=200),
    dict(expected_sender=Peer("substituted", SENDER.public_key)),
    dict(expected_sender=Peer(SENDER.chain_id, RECIPIENT.public_key)),
    dict(expected_recipient=Peer("substituted", RECIPIENT.public_key))])
def test_binary_does_not_relax_pins_or_original_window(change):
    with pytest.raises(MeshEnvelopeError):
        opened(to_binary(message().wire), **change)


def test_codec_is_not_replay_admission_or_authentication():
    first, resealed = message(), message()
    assert to_binary(first.wire) != to_binary(resealed.wire)
    assert opened(to_binary(first.wire)).replay_identity == opened(to_binary(resealed.wire)).replay_identity
    # Valid shape alone is deliberately not an assertion of valid ciphertext.
    shaped = b"LM\x01" + bytes(MAX_CIPHERTEXT_BYTES)
    assert to_binary(to_text(shaped)) == shaped
    with pytest.raises(MeshEnvelopeError):
        opened(shaped)
