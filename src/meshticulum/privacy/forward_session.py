# SPDX-License-Identifier: Apache-2.0
"""Experimental end-to-end session over Noise KK and private binary envelopes.

No device, enrollment, key persistence, authority or application admission is
provided. Both Ed25519 peers and dedicated Noise X25519 public keys are pinned.
Signing identities authenticate the separate Noise keys in final confirmations.
Application records require the complete handshake plus bidirectional key
confirmation. A caller-owned durable counter namespace is mandatory; restart
requires a fresh session ID/exchange under the ORIGINAL application deadline.

Noise KK handshake bytes follow the Noise specification. The MPF2 record layer
is separate: it authenticates its header as AEAD associated data and carries an
unchanged LM1/LM2 sealed envelope. This is not NoiseSocket wire compatibility.
Dropping Python key references does not prove forensic memory erasure.
"""
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import hmac
import json
import os
import threading
import struct
import sys

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from nacl.signing import SigningKey, VerifyKey

from .limits import MAX_WINDOW_SECONDS
from .mesh_envelope import Peer
from .mesh_binary_envelope import MAX_BINARY_BYTES, to_text

PROTOCOL = b'Noise_KK_25519_ChaChaPoly_SHA256'
DOMAIN = b'meshticulum.forward-private.v2\x00'
MAGIC = b'MPF2'
# Keep retired v1 claims fenced across this experimental wire revision.
COUNTER_CLAIM_DOMAIN = b'meshticulum.forward-private.v1\x00counters\x00'
HEADER = struct.Struct('!4sB16sQ')
H1, H2, CONFIRM_I, CONFIRM_R, DATA = 1, 2, 3, 4, 5
MAX_PACKET_BYTES = HEADER.size + MAX_BINARY_BYTES + 16


class ForwardSessionError(ValueError):
    def __init__(self):
        super().__init__('forward_private_session_unavailable')


def require(condition):
    if not condition:
        raise ForwardSessionError()


def inspect_record(packet):
    """Bounded MPF2 record shape, NOT authentication or private admission.

    Service grants still bind exact bytes/hash; endpoints must verify the full
    exchange/context/pins and commit DATA through their protected application.
    No decrypted bytes or caller authority are returned by this inspection.
    """
    require(type(packet) is bytes and HEADER.size + 16 <= len(packet) <= MAX_PACKET_BYTES)
    magic, kind, session_id, counter = HEADER.unpack_from(packet)
    require(magic == MAGIC and any(session_id))
    if kind in (H1, H2):
        require(counter == 0 and len(packet) == HEADER.size + 48)
    elif kind in (CONFIRM_I, CONFIRM_R):
        require(counter == 1 and len(packet) == HEADER.size + 112)
    else:
        require(kind == DATA and 2 <= counter <= 4096
                and len(packet) > HEADER.size + 16 + 51)
    return kind, session_id, counter


def hkdf(chaining_key, material):
    key = hmac.digest(chaining_key, material, 'sha256')
    one = hmac.digest(key, b'\x01', 'sha256')
    return one, hmac.digest(key, one + b'\x02', 'sha256')


def nonce(number):
    require(type(number) is int and 0 <= number < 2**64 - 1)
    return bytes(4) + struct.pack('<Q', number)


def public(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def canonical_x25519(value):
    # Reject equivalent encodings that could disguise reuse of a pinned key.
    return (type(value) is bytes and len(value) == 32 and any(value)
            and int.from_bytes(value, 'little') < 2**255 - 19)


@dataclass(frozen=True)
class ForwardContext:
    initiator: Peer
    responder: Peer
    session_id: bytes
    binding_digest: bytes
    issued_epoch_us: int
    expires_epoch_us: int
    max_records: int
    initiator_noise_public_key: bytes
    responder_noise_public_key: bytes

    def __post_init__(self):
        require(type(self.initiator) is Peer and type(self.responder) is Peer
                and self.initiator != self.responder
                and self.initiator.public_key != self.responder.public_key
                and type(self.session_id) is bytes and len(self.session_id) == 16 and any(self.session_id)
                and type(self.binding_digest) is bytes and len(self.binding_digest) == 32 and any(self.binding_digest)
                and type(self.issued_epoch_us) is int and type(self.expires_epoch_us) is int
                and 0 <= self.issued_epoch_us < self.expires_epoch_us < 2**63
                and self.expires_epoch_us - self.issued_epoch_us <= MAX_WINDOW_SECONDS * 1000000
                and type(self.max_records) is int and 2 <= self.max_records <= 4096)
        require(canonical_x25519(self.initiator_noise_public_key)
                and canonical_x25519(self.responder_noise_public_key)
                and self.initiator_noise_public_key != self.responder_noise_public_key)
        try:
            legacy_keys = {bytes(VerifyKey(peer.public_key).to_curve25519_public_key())
                           for peer in (self.initiator, self.responder)}
            require(self.initiator_noise_public_key not in legacy_keys
                    and self.responder_noise_public_key not in legacy_keys)
        except Exception:
            raise ForwardSessionError() from None

    @property
    def prologue(self):
        value = dict(initiator=self.initiator.to_dict(), responder=self.responder.to_dict(),
            session_id=self.session_id.hex(), binding_digest=self.binding_digest.hex(),
            issued_epoch_us=self.issued_epoch_us, expires_epoch_us=self.expires_epoch_us,
            max_records=self.max_records,
            initiator_noise_public_key=self.initiator_noise_public_key.hex(),
            responder_noise_public_key=self.responder_noise_public_key.hex())
        return DOMAIN + json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')

    @property
    def digest(self):
        return hashlib.sha256(self.prologue).digest()


class _Symmetric:
    def __init__(self, prologue, initiator_static, responder_static):
        self.h = PROTOCOL.ljust(32, b'\0') if len(PROTOCOL) <= 32 else hashlib.sha256(PROTOCOL).digest()
        self.ck, self.k, self.n = self.h, None, 0
        for data in (prologue, initiator_static, responder_static):
            self.mix_hash(data)

    def mix_hash(self, data):
        self.h = hashlib.sha256(self.h + data).digest()

    def mix_key(self, material):
        self.ck, self.k = hkdf(self.ck, material)
        self.n = 0

    def encrypt(self, data):
        encrypted = ChaCha20Poly1305(self.k).encrypt(nonce(self.n), data, self.h)
        self.n += 1
        self.mix_hash(encrypted)
        return encrypted

    def decrypt(self, data):
        plain = ChaCha20Poly1305(self.k).decrypt(nonce(self.n), data, self.h)
        self.n += 1
        self.mix_hash(data)
        return plain


@dataclass(frozen=True)
class ReceiveCandidate:
    """Authenticated sealed candidate for one caller-owned atomic commit.

    This contains no decrypted application content or session keys. The commit
    provider must create replay acceptance and recoverable sealed bytes in one
    authoritative transaction, validating its original prepared intention.
    """
    context: ForwardContext
    local_initiator: bool
    counter_binding: bytes
    counter: int
    handshake_hash: bytes
    packet_sha256: bytes
    application_deadline_monotonic_us: int
    observed_epoch_us: int
    observed_monotonic_us: int
    sealed_record: bytes = field(repr=False)

    @property
    def sealed_sha256(self):
        return hashlib.sha256(self.sealed_record).digest()


def _operation(method):
    """Callbacks may revoke/close, but cannot enter a second operation."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        entered = False
        try:
            require(not self._busy)
            self._busy = True
            entered = True
            result = method(self, *args, **kwargs)
            self._owned()
            return result
        except BaseException:
            self._failure()
        finally:
            if entered:
                self._busy = False
    return guarded


class ForwardSession:
    """One non-resumable crypto incarnation with explicit caller-owned controls.

    The caller must independently generate and reserve its static key for Noise.
    Pin matching and refusal of known legacy keys do not prove that provenance.
    Protected key storage, usage ownership and retirement remain caller duties.

    ``counters.claim(binding)`` must commit a unique session-ID/local-role claim and
    return True; reuse after restart must fail. ``reserve_transmit()`` commits
    a monotonically increasing counter BEFORE it returns. ``record_receive(n)``
    commits replay acceptance only after authentication. All must be independent
    of adjacent-link counters. A failed/ambiguous callback fences this session;
    already committed debt/counters are never refunded.
    """
    def __init__(self, *, context, initiator, signing_key, noise_static_key, counters, authorize,
                 clock, application_deadline_monotonic_us, receive_mode='return'):
        self._closed = False
        self._busy = True  # constructor callbacks cannot enter a public operation
        self._owner = (os.getpid(), threading.get_ident())
        self._s = self._rs = self._e = self._re = self._sym = None
        self._tx = self._rx = None
        self._signer = None
        self._last_clock = None
        self._last_tx = 0
        self._receive_mode = receive_mode
        self._counter_binding = None
        self.handshake_hash = None
        self.state = 'new'
        try:
            require(type(context) is ForwardContext and type(initiator) is bool
                    and isinstance(signing_key, SigningKey) and isinstance(noise_static_key, X25519PrivateKey)
                    and callable(authorize) and callable(clock)
                    and type(application_deadline_monotonic_us) is int
                    and application_deadline_monotonic_us > 0
                    and all(callable(getattr(counters, name, None)) for name in
                            ('claim', 'reserve_transmit', 'record_receive'))
                    and type(receive_mode) is str and receive_mode in ('return', 'commit')
                    and (receive_mode != 'commit' or callable(getattr(counters, 'commit_receive', None))))
            own = context.initiator if initiator else context.responder
            own_noise = context.initiator_noise_public_key if initiator else context.responder_noise_public_key
            peer_noise = context.responder_noise_public_key if initiator else context.initiator_noise_public_key
            require(bytes(signing_key.verify_key) == own.public_key)
            require(public(noise_static_key) == own_noise)
            self.context, self.initiator, self.counters = context, initiator, counters
            self._signer = signing_key
            self.authorize, self.clock = authorize, clock
            self.deadline = application_deadline_monotonic_us
            self._live()
            binding = hashlib.sha256(COUNTER_CLAIM_DOMAIN + context.session_id + bytes([initiator])).digest()
            require(counters.claim(binding) is True)
            self._counter_binding = binding
            self._live()
            self._s = noise_static_key
            self._rs = X25519PublicKey.from_public_bytes(peer_noise)
            self._sym = _Symmetric(context.prologue, context.initiator_noise_public_key,
                                   context.responder_noise_public_key)
            self._busy = False
        except BaseException:
            self._failure()

    def __repr__(self):
        return f'<ForwardSession state={self.state!r}>'

    def close(self):
        self._closed = True
        self.state = 'closed'
        self._s = self._rs = self._e = self._re = self._sym = None
        self._tx = self._rx = None
        self._signer = None

    def _owned(self):
        require(not self._closed and self._owner == (os.getpid(), threading.get_ident()))

    def _live(self):
        self._owned()
        authorized = self.authorize()
        self._owned()
        require(authorized is True)
        now = self.clock()
        self._owned()
        require(type(now) is tuple and len(now) == 2 and all(type(v) is int for v in now)
                and self.context.issued_epoch_us <= now[0] < self.context.expires_epoch_us
                and 0 <= now[1] < self.deadline
                and (self._last_clock is None or (now[0] >= self._last_clock[0] and now[1] >= self._last_clock[1])))
        self._last_clock = now

    def _failure(self):
        self.close()
        error = sys.exception()
        if error is not None and not isinstance(error, Exception):
            raise error
        raise ForwardSessionError() from None

    def _header(self, kind, counter):
        return HEADER.pack(MAGIC, kind, self.context.session_id, counter)

    def _parse(self, packet, kind):
        require(type(packet) is bytes and HEADER.size + 16 <= len(packet) <= MAX_PACKET_BYTES)
        magic, actual_kind, session, counter = HEADER.unpack_from(packet)
        require(magic == MAGIC and actual_kind == kind and session == self.context.session_id)
        return counter, packet[:HEADER.size], packet[HEADER.size:]

    def _split(self):
        one, two = hkdf(self._sym.ck, b'')
        self.handshake_hash = self._sym.h
        self._tx, self._rx = (one, two) if self.initiator else (two, one)
        self._s = self._rs = self._e = self._re = self._sym = None

    @_operation
    def start(self):
        try:
            self._live(); require(self.initiator and self.state == 'new')
            self._e = X25519PrivateKey.generate()
            ep = public(self._e)
            self._sym.mix_hash(ep)
            self._sym.mix_key(self._e.exchange(self._rs))
            self._sym.mix_key(self._s.exchange(self._rs))
            body = ep + self._sym.encrypt(b'')
            self._live()
            self.state = 'await_h2'
            return self._header(H1, 0) + body
        except BaseException:
            self._failure()

    @_operation
    def respond(self, packet):
        try:
            self._live(); require(not self.initiator and self.state == 'new')
            counter, _, body = self._parse(packet, H1)
            require(counter == 0 and len(body) == 48)
            self._re = X25519PublicKey.from_public_bytes(body[:32])
            self._sym.mix_hash(body[:32])
            self._sym.mix_key(self._s.exchange(self._re))
            self._sym.mix_key(self._s.exchange(self._rs))
            require(self._sym.decrypt(body[32:]) == b'')
            self._e = X25519PrivateKey.generate()
            ep = public(self._e)
            self._sym.mix_hash(ep)
            self._sym.mix_key(self._e.exchange(self._re))
            self._sym.mix_key(self._e.exchange(self._rs))
            response = ep + self._sym.encrypt(b'')
            self._split(); self._live(); self.state = 'await_confirm_i'
            return self._header(H2, 0) + response
        except BaseException:
            self._failure()

    @_operation
    def finish(self, packet):
        try:
            self._live(); require(self.initiator and self.state == 'await_h2')
            counter, _, body = self._parse(packet, H2)
            require(counter == 0 and len(body) == 48)
            self._re = X25519PublicKey.from_public_bytes(body[:32])
            self._sym.mix_hash(body[:32])
            self._sym.mix_key(self._e.exchange(self._re))
            self._sym.mix_key(self._s.exchange(self._re))
            require(self._sym.decrypt(body[32:]) == b'')
            self._split(); self._live(); self.state = 'send_confirm_i'
        except BaseException:
            self._failure()

    def _confirmation_digest(self, role):
        return hashlib.sha256(DOMAIN + b'confirmation\0' + self.context.digest + self.handshake_hash + bytes([role])).digest()

    def _confirmation(self, role):
        digest = self._confirmation_digest(role)
        return digest + self._signer.sign(DOMAIN + b'confirmation-signature\0' + digest).signature

    def _verify_confirmation(self, role, plain):
        require(len(plain) == 96 and hmac.compare_digest(plain[:32], self._confirmation_digest(role)))
        peer = self.context.initiator if role else self.context.responder
        VerifyKey(peer.public_key).verify(DOMAIN + b'confirmation-signature\0' + plain[:32], plain[32:])

    def _seal(self, kind, plain):
        self._live()
        counter = self.counters.reserve_transmit()
        require(type(counter) is int and self._last_tx < counter <= self.context.max_records)
        self._last_tx = counter
        self._live()
        header = self._header(kind, counter)
        encrypted = header + ChaCha20Poly1305(self._tx).encrypt(nonce(counter - 1), plain, DOMAIN + header)
        self._live()
        return encrypted

    def _decrypt(self, packet, kind):
        self._live()
        counter, header, body = self._parse(packet, kind)
        require(1 <= counter <= self.context.max_records)
        plain = ChaCha20Poly1305(self._rx).decrypt(nonce(counter - 1), body, DOMAIN + header)
        return counter, plain

    def _accept(self, counter):
        self._live()
        require(self.counters.record_receive(counter) is True)
        self._live()

    @_operation
    def confirm(self):
        try:
            require(self.initiator and self.state == 'send_confirm_i')
            packet = self._seal(CONFIRM_I, self._confirmation(True))
            require(HEADER.unpack_from(packet)[3] == 1)
            self.state = 'await_confirm_r'
            return packet
        except BaseException:
            self._failure()

    @_operation
    def accept_confirmation(self, packet):
        try:
            require(not self.initiator and self.state == 'await_confirm_i')
            counter, plain = self._decrypt(packet, CONFIRM_I)
            require(counter == 1)
            self._verify_confirmation(True, plain)
            self._accept(counter)
            response = self._seal(CONFIRM_R, self._confirmation(False))
            require(HEADER.unpack_from(response)[3] == 1)
            self.state = 'ready'
            self._signer = None
            return response
        except BaseException:
            self._failure()

    @_operation
    def verify_confirmation(self, packet):
        try:
            require(self.initiator and self.state == 'await_confirm_r')
            counter, plain = self._decrypt(packet, CONFIRM_R)
            require(counter == 1)
            self._verify_confirmation(False, plain)
            self._accept(counter); self.state = 'ready'; self._signer = None
        except BaseException:
            self._failure()

    @_operation
    def seal(self, binary_envelope):
        try:
            require(self.state == 'ready')
            to_text(binary_envelope)  # canonical private-envelope shape, not authentication
            return self._seal(DATA, binary_envelope)
        except BaseException:
            self._failure()

    @_operation
    def open(self, packet):
        try:
            require(self.state == 'ready')
            require(self._receive_mode == 'return')
            counter, plain = self._decrypt(packet, DATA)
            require(counter >= 2)
            to_text(plain)
            self._accept(counter)
            return plain  # caller still MUST authenticate/admit the inner envelope
        except BaseException:
            self._failure()

    @_operation
    def receive_into(self, packet):
        """Commit DATA acceptance with recoverable sealed bytes; return no bytes.

        ``counters.commit_receive(candidate)`` MUST use the same claimed replay
        namespace as confirmations and atomically create the receive entry plus
        its exact sealed recovery capsule. Literal True means that commit
        completed; every other result/error is an unknown/refused outcome and
        closes this incarnation. No separate record_receive call follows it.
        Native protected storage, original intention and recovery are supplied
        by the consuming application. This API cannot prove those properties.
        """
        try:
            require(self.state == 'ready' and self._receive_mode == 'commit')
            commit = getattr(self.counters, 'commit_receive', None)
            require(callable(commit))
            counter, plain = self._decrypt(packet, DATA)
            require(counter >= 2)
            to_text(plain)
            self._live()
            candidate = ReceiveCandidate(self.context, self.initiator, self._counter_binding,
                counter, self.handshake_hash, hashlib.sha256(packet).digest(), self.deadline,
                self._last_clock[0], self._last_clock[1], plain)
            require(commit(candidate) is True)
            self._live()
            return None  # commit is authenticated quarantine, not application admission
        except BaseException:
            self._failure()
