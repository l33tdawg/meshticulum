# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Offline peer codec: existing Ed25519 identities, signed inner data, SealedBox.

No identity enrollment, transport, fragmentation, replay database or key storage.
Caller pins both peers independently, protects the decrypted result, and persists
replay decisions before acting. Receipt creation is an explicit caller operation,
not evidence generated from a modem ACK or from decoding an incoming message.
"""

import base64
from dataclasses import dataclass, field
import hashlib
import json
import re
import uuid

from nacl.public import SealedBox
from nacl.signing import SigningKey, VerifyKey

from .limits import MAX_WINDOW_SECONDS


SCHEMA = "sage.lantern.mesh-peer.v1"
SIGN_DOMAIN = b"sage.lantern.mesh-peer.v1/signature\x00"
WIRE_PREFIX = "LME1."
ATTEMPT_SCHEMA = "sage.lantern.mesh-peer.v2"
ATTEMPT_SIGN_DOMAIN = b"sage.lantern.mesh-peer.v2/signature\x00"
ATTEMPT_WIRE_PREFIX = "LME2."
MAX_CONTENT_BYTES = 512
MAX_INNER_BYTES = 2048
MAX_WIRE_BYTES = 4096
# A sealed envelope is opened with the *receiver's* clock and re-checked against
# this ceiling, so it must not be lower than the approval window that sealed it.
MAX_LIFETIME_SECONDS = MAX_WINDOW_SECONDS


class MeshEnvelopeError(ValueError):
    def __init__(self):
        super().__init__("invalid_mesh_envelope")


def _fail():
    raise MeshEnvelopeError()


@dataclass(frozen=True)
class Peer:
    chain_id: str
    public_key: bytes

    def __post_init__(self):
        if (type(self.chain_id) is not str
                or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.chain_id) is None
                or type(self.public_key) is not bytes or len(self.public_key) != 32):
            _fail()
        try:
            VerifyKey(self.public_key).to_curve25519_public_key()
        except Exception:
            _fail()

    def to_dict(self):
        return {"chain_id": self.chain_id, "public_key": self.public_key.hex()}


@dataclass(frozen=True, repr=False)
class OpenedEnvelope:
    sender: Peer
    recipient: Peer
    message_id: str
    kind: str
    issued_at: int
    expires_at: int
    content: str | None
    original_id: str | None
    original_digest: str | None
    digest: str
    attempt_id: str | None = None
    logical_issued_at: int | None = None
    logical_expires_at: int | None = None
    _signature: bytes = field(default=b"", init=False, repr=False, compare=False)

    @property
    def replay_identity(self):
        return self.sender.chain_id, self.sender.public_key.hex(), self.message_id

    @property
    def logical_digest(self):
        """Original v1 body commitment, including its immutable approval window."""
        if self.attempt_id is None:
            return self.digest
        return message_digest(sender=self.sender, recipient=self.recipient, message_id=self.message_id,
            content=self.content, issued_at=self.logical_issued_at, expires_at=self.logical_expires_at)


@dataclass(frozen=True, repr=False)
class SealedEnvelope:
    wire: str
    message_id: str
    digest: str

    @property
    def encoded_bytes(self):
        return len(self.wire.encode("ascii"))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value or uuid.UUID(value).int == 0:
        _fail()


def _identity(key, peer):
    if type(peer) is not Peer or not isinstance(key, SigningKey) or bytes(key.verify_key) != peer.public_key:
        _fail()


def _validate(body, sender, recipient, now):
    fields = {"schema", "sender", "recipient", "message_id", "kind", "issued_at", "expires_at",
              "content", "original_id", "original_digest"}
    if type(body) is dict and body.get("schema") == ATTEMPT_SCHEMA:
        fields |= {"attempt_id", "logical_issued_at", "logical_expires_at"}
        _uuid(body.get("attempt_id"))
        if (body.get("kind") != "message" or body["attempt_id"] == body.get("message_id")
                or type(body.get("logical_issued_at")) is not int
                or type(body.get("logical_expires_at")) is not int
                or type(body.get("issued_at")) is not int
                or not 0 <= body["logical_issued_at"] < body["logical_expires_at"] <= body["issued_at"]
                or body["logical_expires_at"] - body["logical_issued_at"] > MAX_LIFETIME_SECONDS):
            _fail()
    if (type(body) is not dict or set(body) != fields or body["schema"] not in (SCHEMA, ATTEMPT_SCHEMA)
            or body["sender"] != sender.to_dict() or body["recipient"] != recipient.to_dict()
            or type(now) is not int or not 0 <= now <= 2 ** 53 - 1
            or type(body["issued_at"]) is not int or type(body["expires_at"]) is not int
            or not 0 <= body["issued_at"] <= now < body["expires_at"] <= 2 ** 53 - 1
            or not 1 <= body["expires_at"] - body["issued_at"] <= MAX_LIFETIME_SECONDS):
        _fail()
    _uuid(body["message_id"])
    if body["kind"] == "message":
        content = body["content"]
        if (type(content) is not str or not content.strip()
                or not 1 <= len(content.encode("utf-8")) <= MAX_CONTENT_BYTES
                or any(ord(character) < 32 and character not in "\n\t" for character in content)
                or body["original_id"] is not None or body["original_digest"] is not None):
            _fail()
    elif body["kind"] in ("delivery", "read"):
        if body["content"] is not None:
            _fail()
        _uuid(body["original_id"])
        if (body["original_id"] == body["message_id"] or type(body["original_digest"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", body["original_digest"]) is None):
            _fail()
    else:
        _fail()


def _body(*, sender, recipient, message_id, issued_at, expires_at, kind="message",
          content=None, original_id=None, original_digest=None, attempt_id=None,
          logical_issued_at=None, logical_expires_at=None):
    body = dict(schema=SCHEMA, sender=sender.to_dict(), recipient=recipient.to_dict(),
                message_id=message_id, kind=kind, issued_at=issued_at, expires_at=expires_at,
                content=content, original_id=original_id, original_digest=original_digest)
    if any(value is not None for value in (attempt_id, logical_issued_at, logical_expires_at)):
        body.update(schema=ATTEMPT_SCHEMA, attempt_id=attempt_id, logical_issued_at=logical_issued_at,
                    logical_expires_at=logical_expires_at)
    _validate(body, sender, recipient, issued_at)
    return body


def message_digest(**fields):
    """Reconstruct a message commitment from trusted history; grants no authority."""
    try:
        return hashlib.sha256(_canonical(_body(**fields))).hexdigest()
    except Exception:
        raise MeshEnvelopeError() from None


def _seal(signing_key, *, sender, recipient, message_id, issued_at, expires_at,
          kind, content=None, original_id=None, original_digest=None, attempt_id=None,
          logical_issued_at=None, logical_expires_at=None):
    try:
        _identity(signing_key, sender)
        if type(recipient) is not Peer:
            _fail()
        body = _body(sender=sender, recipient=recipient, message_id=message_id, kind=kind,
            issued_at=issued_at, expires_at=expires_at, content=content, original_id=original_id,
            original_digest=original_digest, attempt_id=attempt_id, logical_issued_at=logical_issued_at,
            logical_expires_at=logical_expires_at)
        canonical = _canonical(body)
        domain, prefix = (SIGN_DOMAIN, WIRE_PREFIX) if body["schema"] == SCHEMA else (ATTEMPT_SIGN_DOMAIN, ATTEMPT_WIRE_PREFIX)
        signature = signing_key.sign(domain + canonical).signature
        inner = _canonical({"body": body, "signature": base64.b64encode(signature).decode("ascii")})
        if len(inner) > MAX_INNER_BYTES:
            _fail()
        ciphertext = SealedBox(VerifyKey(recipient.public_key).to_curve25519_public_key()).encrypt(inner)
        wire = prefix + base64.b64encode(ciphertext).decode("ascii")
        if len(wire) > MAX_WIRE_BYTES:
            _fail()
        return SealedEnvelope(wire, message_id, hashlib.sha256(canonical).hexdigest())
    except Exception:
        raise MeshEnvelopeError() from None


def seal_message(signing_key, *, sender, recipient, message_id, content, issued_at, expires_at,
                 attempt_id=None, logical_issued_at=None, logical_expires_at=None):
    return _seal(signing_key, sender=sender, recipient=recipient, message_id=message_id,
                 content=content, issued_at=issued_at, expires_at=expires_at, kind="message",
                 attempt_id=attempt_id, logical_issued_at=logical_issued_at, logical_expires_at=logical_expires_at)


def seal_delivery_receipt(signing_key, *, sender, recipient, message_id, original_id,
                          original_digest, issued_at, expires_at):
    """Caller asserts original application message was verified and durably stored."""
    return _seal(signing_key, sender=sender, recipient=recipient, message_id=message_id,
                 original_id=original_id, original_digest=original_digest,
                 issued_at=issued_at, expires_at=expires_at, kind="delivery")


def seal_read_receipt(signing_key, *, sender, recipient, message_id, original_id,
                      original_digest, issued_at, expires_at, explicit_user_read):
    """Trusted caller must bind this operation to explicit authenticated user reading."""
    if explicit_user_read is not True:
        _fail()
    return _seal(signing_key, sender=sender, recipient=recipient, message_id=message_id,
                 original_id=original_id, original_digest=original_digest,
                 issued_at=issued_at, expires_at=expires_at, kind="read")


def open_envelope(wire, recipient_signing_key, *, expected_sender, expected_recipient, now):
    """Decrypt/verify only. Does not accept replay, send receipts, or execute text."""
    try:
        _identity(recipient_signing_key, expected_recipient)
        prefix = wire[:5] if type(wire) is str else None
        formats = {WIRE_PREFIX: (SCHEMA, SIGN_DOMAIN), ATTEMPT_WIRE_PREFIX: (ATTEMPT_SCHEMA, ATTEMPT_SIGN_DOMAIN)}
        if (type(expected_sender) is not Peer or type(wire) is not str
                or not len(WIRE_PREFIX) < len(wire) <= MAX_WIRE_BYTES or prefix not in formats):
            _fail()
        schema, domain = formats[prefix]
        encoded = wire[len(WIRE_PREFIX):].encode("ascii")
        ciphertext = base64.b64decode(encoded, validate=True)
        if base64.b64encode(ciphertext) != encoded or len(ciphertext) > MAX_INNER_BYTES + 48:
            _fail()
        inner = SealedBox(recipient_signing_key.to_curve25519_private_key()).decrypt(ciphertext)
        if len(inner) > MAX_INNER_BYTES:
            _fail()
        value = json.loads(inner, object_pairs_hook=_unique)
        if type(value) is not dict or set(value) != {"body", "signature"} or _canonical(value) != inner:
            _fail()
        body = value["body"]
        _validate(body, expected_sender, expected_recipient, now)
        if body["schema"] != schema:
            _fail()
        signature_text = value["signature"]
        if type(signature_text) is not str or len(signature_text) != 88:
            _fail()
        signature = base64.b64decode(signature_text, validate=True)
        if len(signature) != 64 or base64.b64encode(signature).decode("ascii") != signature_text:
            _fail()
        canonical = _canonical(body)
        VerifyKey(expected_sender.public_key).verify(domain + canonical, signature)
        result = OpenedEnvelope(expected_sender, expected_recipient, body["message_id"], body["kind"],
                                body["issued_at"], body["expires_at"], body["content"], body["original_id"],
                                body["original_digest"], hashlib.sha256(canonical).hexdigest(),
                                body.get("attempt_id"), body.get("logical_issued_at"), body.get("logical_expires_at"))
        object.__setattr__(result, "_signature", signature)
        return result
    except Exception:
        raise MeshEnvelopeError() from None


def correlate_receipt(receipt: OpenedEnvelope, *, original_sender: Peer,
                      original_recipient: Peer, original_id: str, original_digest: str,
                      original_issued_at: int, original_kind="message"):
    """Return a signed peer assertion, not history acceptance or proof of reading.

    Stored original fields must come from authenticated protected history.
    Revalidates signature; constructing/replacing an OpenedEnvelope is not trust.
    Caller separately enforces current expiry, durable replay and history CAS.
    """
    try:
        if (type(receipt) is not OpenedEnvelope or type(original_sender) is not Peer
                or type(original_recipient) is not Peer or type(original_kind) is not str
                or original_kind != "message" or type(original_issued_at) is not int
                or not 0 <= original_issued_at <= 2 ** 53 - 1
                or type(original_digest) is not str
                or re.fullmatch(r"[0-9a-f]{64}", original_digest) is None
                or type(receipt.sender) is not Peer or type(receipt.recipient) is not Peer
                or receipt.sender != original_recipient or receipt.recipient != original_sender
                or receipt.kind not in ("delivery", "read")
                or receipt.attempt_id is not None or receipt.logical_issued_at is not None
                or receipt.logical_expires_at is not None
                or receipt.original_id != original_id or receipt.original_digest != original_digest
                or type(receipt.issued_at) is not int or receipt.issued_at < original_issued_at):
            _fail()
        _uuid(original_id)
        body = dict(schema=SCHEMA, sender=receipt.sender.to_dict(), recipient=receipt.recipient.to_dict(),
                    message_id=receipt.message_id, kind=receipt.kind, issued_at=receipt.issued_at,
                    expires_at=receipt.expires_at, content=receipt.content,
                    original_id=receipt.original_id, original_digest=receipt.original_digest)
        _validate(body, original_recipient, original_sender, receipt.issued_at)
        canonical = _canonical(body)
        if (type(receipt._signature) is not bytes or len(receipt._signature) != 64
                or receipt.digest != hashlib.sha256(canonical).hexdigest()):
            _fail()
        VerifyKey(original_recipient.public_key).verify(SIGN_DOMAIN + canonical, receipt._signature)
        return "peer_delivery_asserted" if receipt.kind == "delivery" else "peer_read_asserted"
    except Exception:
        raise MeshEnvelopeError() from None
