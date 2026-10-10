"""Offline self-consent cards, not enrollment, name authority or radio delivery.

Sign only through an already loaded SAGE identity. No key creation, persistence,
advertising or transmission occurs. See docs/lantern/MESH_CONTACTS.md for the
wire contract and required independent enrollment/radio-possession checks.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
import struct
import unicodedata
from typing import Protocol

from nacl.bindings import crypto_core_ed25519_is_valid_point
from nacl.signing import VerifyKey

from .message_contacts import ContactClaim, RecipientID


SCHEMA = 'sage.lantern.mesh-contact.v1'
SIGN_METHOD = 'LANTERN-CONTACT'
SIGN_PATH = '/sage-lantern/mesh-contact/v1'
MAX_CARD_BYTES = 4096
# The single ceiling for every Lantern mesh artifact that carries its own
# issued_at/expires_at: the self-consent contact card, the owner's message
# approval, the sealed wire envelope and the fragment assembly window all read
# this value. The owner's decision (2026-09-13) is a 24-hour window for an
# explicitly approved message, and the message approval is bound to the card it
# was consented from, so the card cap has to move with it. Raising any one of
# them alone silently breaks delivery: the peer re-validates the envelope
# lifetime on receipt. Keep them equal; tests/test_mesh_window.py pins that.
MAX_WINDOW_SECONDS = 24 * 60 * 60
MAX_TTL_SECONDS = MAX_WINDOW_SECONDS
MAX_INTEGER = (1 << 53) - 1
_FIELDS = {'schema', 'chain_id', 'agent_id', 'radio_public_key', 'display_name',
           'revision', 'issued_at', 'expires_at', 'findable', 'messaging_allowed'}


class MeshContactError(ValueError):
    def __init__(self):
        super().__init__('mesh_contact_unavailable')


class ContactSigner(Protocol):
    @property
    def agent_id(self) -> str: ...

    def sign_request(self, method: str, path: str, body: bytes | None = None,
                     timestamp: int | None = None) -> dict[str, str]: ...


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedMeshContact:
    chain_id: str
    agent_id: str
    radio_public_key: str
    display_name: str
    revision: int
    issued_at: int
    expires_at: int
    findable: bool
    messaging_allowed: bool

    @property
    def recipient(self) -> RecipientID:
        return RecipientID(self.chain_id, self.agent_id)

    def contact_claim(self, *, caller: RecipientID, issuer: str, policy_binding: str) -> ContactClaim:
        """Project self-consent under caller-supplied trust policy, NOT enrollment."""
        return ContactClaim(caller, self.recipient, self.display_name, issuer, policy_binding,
                            self.revision, self.issued_at, self.expires_at,
                            self.findable, self.messaging_allowed)


@dataclass(frozen=True, slots=True, repr=False)
class HistoricalMeshContact:
    """Signature/structure only. No freshness, send authority or claim conversion."""

    chain_id: str
    agent_id: str
    radio_public_key: str
    display_name: str
    revision: int
    issued_at: int
    expires_at: int
    findable: bool
    messaging_allowed: bool

    @property
    def recipient(self) -> RecipientID:
        return RecipientID(self.chain_id, self.agent_id)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _hex(value, size):
    if type(value) is not str or re.fullmatch('[0-9a-f]{' + str(size * 2) + '}', value) is None:
        raise MeshContactError()
    return bytes.fromhex(value)


def _unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise MeshContactError()
        result[name] = value
    return result


def _invalid_constant(value):
    raise MeshContactError()


def _historical_payload(payload):
    if type(payload) is not dict or set(payload) != _FIELDS or payload['schema'] != SCHEMA:
        raise MeshContactError()
    RecipientID(payload['chain_id'], payload['agent_id'])
    for field in ('agent_id', 'radio_public_key'):
        if not crypto_core_ed25519_is_valid_point(_hex(payload[field], 32)):
            raise MeshContactError()
    if payload['agent_id'] == payload['radio_public_key']:
        raise MeshContactError()
    name = payload['display_name']
    if (type(name) is not str or not 1 <= len(name) <= 128 or len(name.encode('utf-8')) > 512
            or name != name.strip() or unicodedata.normalize('NFC', name) != name
            or any(unicodedata.category(char).startswith('C')
                   or unicodedata.category(char) in ('Zl', 'Zp') for char in name)):
        raise MeshContactError()
    for field in ('revision', 'issued_at', 'expires_at'):
        if type(payload[field]) is not int or not 0 <= payload[field] <= MAX_INTEGER:
            raise MeshContactError()
    if (not 0 < payload['issued_at'] < payload['expires_at']
            or not 0 < payload['expires_at'] - payload['issued_at'] <= MAX_TTL_SECONDS
            or type(payload['findable']) is not bool or type(payload['messaging_allowed']) is not bool):
        raise MeshContactError()
    return HistoricalMeshContact(**{key: value for key, value in payload.items() if key != 'schema'})


def _current(contact, now):
    if (type(now) not in (int, float) or not math.isfinite(now) or not 0 < now <= MAX_INTEGER
            or not contact.issued_at <= now < contact.expires_at):
        raise MeshContactError()
    return VerifiedMeshContact(**asdict(contact))


def _validate(payload, now):
    return _current(_historical_payload(payload), now)


def verify_contact_card_signature(card: bytes, *, known_actor: RecipientID | None = None) -> HistoricalMeshContact:
    """Authenticate historical bytes without asserting current validity or admission time."""
    try:
        if type(card) is not bytes or not 1 <= len(card) <= MAX_CARD_BYTES:
            raise MeshContactError()
        envelope = json.loads(card.decode('utf-8'), object_pairs_hook=_unique,
                              parse_constant=_invalid_constant)
        if (type(envelope) is not dict or set(envelope) != {'payload', 'nonce', 'signature'}
                or _canonical(envelope) != card):
            raise MeshContactError()
        contact = _historical_payload(envelope['payload'])
        if known_actor is not None and (type(known_actor) is not RecipientID or contact.recipient != known_actor):
            raise MeshContactError()
        nonce = _hex(envelope['nonce'], 8)
        signature = _hex(envelope['signature'], 64)
        canonical = SIGN_METHOD.encode() + b' ' + SIGN_PATH.encode() + b'\n' + _canonical(envelope['payload'])
        message = hashlib.sha256(canonical).digest() + struct.pack('>q', contact.issued_at) + nonce
        VerifyKey(bytes.fromhex(contact.agent_id)).verify(message, signature)
        return contact
    except Exception:
        raise MeshContactError() from None


def verify_contact_card(card: bytes, *, now: int | float, known_actor: RecipientID | None = None,
                        previous: VerifiedMeshContact | HistoricalMeshContact | None = None) -> VerifiedMeshContact:
    """Current self-consent; a verified historical previous value only fences rollback."""
    try:
        contact = _current(verify_contact_card_signature(card, known_actor=known_actor), now)
        if previous is not None:
            if (type(previous) not in (VerifiedMeshContact, HistoricalMeshContact) or previous.recipient != contact.recipient
                    or contact.revision < previous.revision
                    or (contact.revision == previous.revision and asdict(contact) != asdict(previous))
                    or contact.issued_at < previous.issued_at):
                raise MeshContactError()
        return contact
    except Exception:
        raise MeshContactError() from None


def sign_contact_card(identity: ContactSigner, *, chain_id: str, radio_public_key: str,
                      display_name: str, revision: int, issued_at: int, expires_at: int,
                      now: int | float, findable: bool = False, messaging_allowed: bool = False) -> bytes:
    """Return bytes only; opt-in requires explicit flags. Never load/save keys."""
    try:
        payload = dict(schema=SCHEMA, chain_id=chain_id, agent_id=identity.agent_id,
                       radio_public_key=radio_public_key, display_name=display_name,
                       revision=revision, issued_at=issued_at, expires_at=expires_at,
                       findable=findable, messaging_allowed=messaging_allowed)
        contact = _validate(payload, now)
        headers = identity.sign_request(SIGN_METHOD, SIGN_PATH, _canonical(payload), timestamp=issued_at)
        if (type(headers) is not dict or headers.get('X-Agent-ID') != contact.agent_id
                or headers.get('X-Timestamp') != str(issued_at)):
            raise MeshContactError()
        card = _canonical(dict(payload=payload, nonce=headers['X-Nonce'], signature=headers['X-Signature']))
        verify_contact_card(card, now=now, known_actor=contact.recipient)
        return card
    except Exception:
        raise MeshContactError() from None
