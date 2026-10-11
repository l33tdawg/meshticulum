# SPDX-License-Identifier: Apache-2.0
"""Opt-in source prototype; no native admission or existing wire integration.

X-Wing is draft-connolly-cfrg-xwing-kem-11, not TLS or Noise. All asymmetric
primitives use cryptography's OpenSSL backend. The caller supplies pinned dual
identities, trust/revocation policy and an admitted original lifetime. Python
key deletion is not secure erasure; this module makes no deployed PFS claim.
"""
from dataclasses import dataclass
import hashlib
import os
import secrets
import struct
import threading

SUITE = b'meshticulum-pq-prototype-v1/xwing-draft11/ed25519+mldsa65/hkdf-sha256/chacha20poly1305'
BACKEND_VERSION = '50.0.2'
PK_BYTES, CT_BYTES, SIGNATURE_BYTES, IDENTITY_BYTES = 1216, 1120, 3373, 1984
MAX_LIFETIME_US = 300_000_000
_LABEL = bytes.fromhex('5c2e2f2f5e5c')


class PQError(ValueError):
    """Invalid or unconfirmed prototype exchange; no classical fallback."""


class PQUnavailable(RuntimeError):
    """The explicitly selected provider is unavailable."""


def _need(value):
    if not value:
        raise PQError('invalid_post_quantum_prototype_input')


def _bytes(value, length):
    _need(type(value) is bytes and len(value) == length)
    return value


def _owner():
    return os.getpid(), threading.get_ident()


def _backend():
    try:
        import cryptography
        if cryptography.__version__ != BACKEND_VERSION:
            raise PQUnavailable('cryptography_50_0_2_required')
        from cryptography.hazmat.primitives.asymmetric import ed25519, mlkem, mldsa, x25519
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        return ed25519, mlkem, mldsa, x25519, ChaCha20Poly1305, HKDF, hashes
    except ImportError as error:
        raise PQUnavailable('post_quantum_backend_unavailable') from error


def _combine(ss_m, ss_x, ct_x, pk_x):
    # Exact draft11 section5.3 combiner, using both independent secrets.
    return hashlib.sha3_256(b''.join(_bytes(v, 32) for v in
        (ss_m, ss_x, ct_x, pk_x)) + _LABEL).digest()


class XWingPrivateKey:
    """Fresh per-exchange X-Wing key; seed loading exists for published vectors."""
    __slots__ = ('_mlkem', '_x25519', '_public')

    def __init__(self, seed):
        _, mlkem, _, x25519, *_ = _backend()
        expanded = hashlib.shake_256(_bytes(seed, 32)).digest(96)
        self._mlkem = mlkem.MLKEM768PrivateKey.from_seed_bytes(expanded[:64])
        self._x25519 = x25519.X25519PrivateKey.from_private_bytes(expanded[64:])
        self._public = (self._mlkem.public_key().public_bytes_raw()
                        + self._x25519.public_key().public_bytes_raw())

    @classmethod
    def generate(cls):
        return cls(secrets.token_bytes(32))

    @property
    def public_bytes(self):
        return self._public

    def decapsulate(self, ciphertext):
        _, _, _, x25519, *_ = _backend()
        ct = _bytes(ciphertext, CT_BYTES)
        try:
            ss_m = self._mlkem.decapsulate(ct[:1088])
            ss_x = self._x25519.exchange(x25519.X25519PublicKey.from_public_bytes(ct[1088:]))
        except ValueError as error:
            raise PQError('invalid_hybrid_ciphertext') from error
        # ML-KEM implicit rejection can return a different secret. Confirmation
        # below, rather than a promised decapsulation exception, detects it.
        return _combine(ss_m, ss_x, ct[1088:], self._public[1184:])


def encapsulate(public_key):
    _, mlkem, _, x25519, *_ = _backend()
    pk = _bytes(public_key, PK_BYTES)
    try:
        ephemeral = x25519.X25519PrivateKey.generate()
        ct_x = ephemeral.public_key().public_bytes_raw()
        ss_x = ephemeral.exchange(x25519.X25519PublicKey.from_public_bytes(pk[1184:]))
        ss_m, ct_m = mlkem.MLKEM768PublicKey.from_public_bytes(pk[:1184]).encapsulate()
    except ValueError as error:
        raise PQError('invalid_hybrid_public_key') from error
    return _combine(ss_m, ss_x, ct_x, pk[1184:]), ct_m + ct_x


def _signed(purpose, message):
    _need(type(purpose) is bytes and 1 <= len(purpose) <= 64
          and type(message) is bytes and len(message) <= 1_000_000)
    context = b'meshticulum.pq.v1/' + purpose
    return context, (SUITE + b'\0' + bytes((len(purpose),)) + purpose
                     + struct.pack('!I', len(message)) + message)


@dataclass(frozen=True, slots=True)
class PinnedIdentity:
    """Caller-pinned Ed25519 AND ML-DSA65 public keys; never implicit TOFU."""
    public_bytes: bytes

    def __post_init__(self):
        _bytes(self.public_bytes, IDENTITY_BYTES)

    @property
    def identifier(self):
        return hashlib.sha256(SUITE + b'/identity\0' + self.public_bytes).digest()

    def verify(self, purpose, message, signature):
        ed, _, mldsa, *_ = _backend()
        context, bound = _signed(purpose, message)
        signature = _bytes(signature, SIGNATURE_BYTES)
        from cryptography.exceptions import InvalidSignature
        try:
            ed.Ed25519PublicKey.from_public_bytes(self.public_bytes[:32]).verify(signature[:64], bound)
            mldsa.MLDSA65PublicKey.from_public_bytes(self.public_bytes[32:]).verify(
                signature[64:], bound, context=context)
        except (ValueError, InvalidSignature) as error:
            raise PQError('dual_authentication_failed') from error


class DualIdentity:
    __slots__ = ('_ed', '_ml', '_public')

    def __init__(self):
        ed, _, mldsa, *_ = _backend()
        self._ed, self._ml = ed.Ed25519PrivateKey.generate(), mldsa.MLDSA65PrivateKey.generate()
        self._public = PinnedIdentity(self._ed.public_key().public_bytes_raw()
                                      + self._ml.public_key().public_bytes_raw())

    @property
    def public(self):
        return self._public

    def sign(self, purpose, message):
        context, bound = _signed(purpose, message)
        return self._ed.sign(bound) + self._ml.sign(bound, context=context)


def _header(kind):
    return b'MPQ1' + bytes((11, kind))


@dataclass(frozen=True, slots=True)
class Context:
    source: bytes
    destination: bytes
    service: bytes
    object_sha256: bytes
    issued_epoch_us: int
    expires_epoch_us: int
    nonce: bytes

    def __post_init__(self):
        for value in (self.source, self.destination, self.object_sha256, self.nonce):
            _bytes(value, 32)
        _need(self.source != self.destination and type(self.service) is bytes
              and 1 <= len(self.service) <= 64 and all(33 <= v <= 126 for v in self.service)
              and type(self.issued_epoch_us) is int and type(self.expires_epoch_us) is int
              and 0 < self.issued_epoch_us < self.expires_epoch_us < 2**63
              and self.expires_epoch_us - self.issued_epoch_us <= MAX_LIFETIME_US)

    def live(self, now):
        _need(type(now) is int and self.issued_epoch_us <= now < self.expires_epoch_us)

    def encode(self):
        return (struct.pack('!QQ', self.issued_epoch_us, self.expires_epoch_us)
                + self.nonce + self.source + self.destination + self.object_sha256
                + bytes((len(self.service),)) + self.service)


def _offer(packet, expected, peer, now):
    _need(type(packet) is bytes and type(expected) is Context and type(peer) is PinnedIdentity)
    expected.live(now)
    body_size = 6 + len(expected.encode()) + PK_BYTES
    _bytes(packet, body_size + SIGNATURE_BYTES)
    body, signature = packet[:body_size], packet[body_size:]
    _need(body[:6] == _header(1) and body[6:body_size-PK_BYTES] == expected.encode()
          and expected.source == peer.identifier)
    peer.verify(b'offer', body, signature)
    return body[-PK_BYTES:]


def _keys(secret, transcript):
    *_, HKDF, hashes = _backend()
    return HKDF(algorithm=hashes.SHA256(), length=96, salt=transcript,
                info=SUITE + b'/confirmed-session').derive(_bytes(secret, 32))


def _tag(key, transcript, role):
    *_, AEAD, _hkdf, _hashes = _backend()
    nonce = hashlib.sha256(SUITE + role + transcript).digest()[:12]
    return AEAD(key).encrypt(nonce, b'', SUITE + role + transcript)


def _confirm(key, transcript, role, tag):
    *_, AEAD, _hkdf, _hashes = _backend()
    from cryptography.exceptions import InvalidTag
    nonce = hashlib.sha256(SUITE + role + transcript).digest()[:12]
    try:
        _need(AEAD(key).decrypt(nonce, _bytes(tag, 16), SUITE + role + transcript) == b'')
    except InvalidTag as error:
        raise PQError('hybrid_secret_not_confirmed') from error


@dataclass(frozen=True, slots=True)
class SessionKeys:
    outgoing: bytes
    incoming: bytes
    transcript_sha256: bytes
    context: Context


class OfferState:
    __slots__ = ('_key', '_context', '_packet', '_used', '_owner')

    def __init__(self, identity, context, now):
        _need(type(identity) is DualIdentity and type(context) is Context
              and identity.public.identifier == context.source)
        context.live(now)
        self._owner = _owner()
        self._key, self._context, self._used = XWingPrivateKey.generate(), context, False
        body = _header(1) + context.encode() + self._key.public_bytes
        self._packet = body + identity.sign(b'offer', body)

    @property
    def packet(self):
        return self._packet

    def finish(self, packet, peer, now):
        _need(self._owner == _owner() and not self._used and type(peer) is PinnedIdentity)
        self._used = True
        context = self._context; context.live(now)
        _need(peer.identifier == context.destination)
        _bytes(packet, 6 + 32 + CT_BYTES + 16 + SIGNATURE_BYTES)
        body, signature = packet[:-SIGNATURE_BYTES], packet[-SIGNATURE_BYTES:]
        _need(body[:6] == _header(2) and body[6:38] == hashlib.sha256(self._packet).digest())
        peer.verify(b'reply', body, signature)
        core = body[:-16]; transcript = hashlib.sha256(SUITE + self._packet + core).digest()
        keys = _keys(self._key.decapsulate(core[38:]), transcript)
        _confirm(keys[:32], transcript, b'/responder', body[-16:])
        confirmation = _header(3) + transcript + _tag(keys[:32], transcript, b'/initiator')
        return SessionKeys(keys[32:64], keys[64:96], transcript, context), confirmation


class ReplayWindow:
    """Bounded in-process nonce refusal; caller must preserve it across offers.

    This is not persistent native replay protection. It never expires entries
    or grants fresh credit; replacing it is a caller trust-lifecycle decision.
    """
    __slots__ = ('_destination', '_seen', '_owner')

    def __init__(self, destination):
        self._destination, self._seen = _bytes(destination, 32), set()
        self._owner = _owner()

    def consume(self, context):
        _need(self._owner == _owner() and type(context) is Context
              and context.destination == self._destination)
        nonce = context.source + context.nonce
        _need(nonce not in self._seen and len(self._seen) < 256)
        self._seen.add(nonce)


class ReplyState:
    __slots__ = ('_context', '_keys', '_transcript', '_packet', '_used', '_owner')

    def __init__(self, identity, offer, context, peer, now, replay):
        _need(type(identity) is DualIdentity and type(context) is Context
              and identity.public.identifier == context.destination and type(replay) is ReplayWindow)
        pk = _offer(offer, context, peer, now)
        replay.consume(context)
        self._owner = _owner()
        secret, ciphertext = encapsulate(pk)
        core = _header(2) + hashlib.sha256(offer).digest() + ciphertext
        self._transcript = hashlib.sha256(SUITE + offer + core).digest()
        self._keys, self._context, self._used = _keys(secret, self._transcript), context, False
        body = core + _tag(self._keys[:32], self._transcript, b'/responder')
        self._packet = body + identity.sign(b'reply', body)

    @property
    def packet(self):
        return self._packet

    def finish(self, confirmation, now):
        _need(self._owner == _owner() and not self._used); self._used = True
        self._context.live(now)
        _bytes(confirmation, 54)
        _need(confirmation[:6] == _header(3) and confirmation[6:38] == self._transcript)
        _confirm(self._keys[:32], self._transcript, b'/initiator', confirmation[38:])
        return SessionKeys(self._keys[64:96], self._keys[32:64], self._transcript, self._context)
