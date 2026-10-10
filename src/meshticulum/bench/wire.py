# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Authenticated binary frames for explicitly provisioned bench sessions.

The operator provisions independent pairwise bench keys over the existing pinned
management channel. This module neither enrolls a peer nor reads SAGE keys. Full
chain-qualified identities, session incarnation, service, route/profile binding,
limits and expiry are in the key derivation context. The compact on-air session
reference alone grants nothing. There is no unauthenticated fallback.

ChaCha20-Poly1305 and HKDF are supplied by cryptography. Per-direction keys and
durably reserved counters prevent nonce reuse across process restarts. A reboot,
missing state or retired session requires a fresh provisioned session. The local
state directory must be protected against deletion/rollback by the deployment;
ordinary SQLite does not establish hardware rollback protection. Frame admission
is separate from actual worker/broker leases and application SAGE/HTTPS authority.
"""
from dataclasses import dataclass, field
from enum import IntEnum
import hashlib
import json
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..contracts.identity import PeerID, SessionKey

MAGIC = b'MT'
VERSION = 1
HEADER = struct.Struct('!2sBBB16sQH')
TAG_BYTES = 16
MAX_RADIO_FRAME = 255
MAX_BODY = MAX_RADIO_FRAME - HEADER.size - TAG_BYTES
DOMAIN = b'lantern.meshticulum.bench.v1\x00'


class WireError(ValueError):
    """A payload-free refusal suitable for a bounded status record."""


def require(value, code):
    if not value:
        raise WireError(code)


class FrameKind(IntEnum):
    DATA = 1
    ACK = 2
    CONTROL = 3


@dataclass(frozen=True, slots=True)
class BenchGrant:
    session: SessionKey
    service: str
    route_binding_digest: bytes
    issued_epoch_us: int
    expires_epoch_us: int
    max_plaintext: int
    max_frames: int
    secret: bytes = field(repr=False)

    def __post_init__(self):
        require(type(self.session) is SessionKey, 'invalid_session')
        require(self.service == 'bench_transfer', 'unsupported_bench_service')
        require(type(self.route_binding_digest) is bytes and len(self.route_binding_digest) == 32,
                'invalid_route_binding')
        require(type(self.secret) is bytes and len(self.secret) == 32, 'invalid_key')
        require(type(self.issued_epoch_us) is int and type(self.expires_epoch_us) is int
                and 0 < self.issued_epoch_us < self.expires_epoch_us < 2**63, 'invalid_expiry')
        require(type(self.max_plaintext) is int and 1 <= self.max_plaintext <= MAX_BODY,
                'invalid_plaintext_limit')
        require(type(self.max_frames) is int and 1 <= self.max_frames <= 1_000_000,
                'invalid_frame_limit')

    def context(self):
        def peer(p):
            return {'chain_id': p.chain_id, 'agent_id': p.agent_id}
        return json.dumps({
            'protocol': 'lantern.meshticulum.bench.v1',
            'initiator': peer(self.session.initiator),
            'responder': peer(self.session.responder),
            'session_id': self.session.session_id.hex(),
            'session_generation': self.session.session_generation.hex(),
            'service': self.service, 'route': self.route_binding_digest.hex(),
            'issued_epoch_us': self.issued_epoch_us, 'expires_epoch_us': self.expires_epoch_us,
            'max_plaintext': self.max_plaintext, 'max_frames': self.max_frames},
            sort_keys=True, separators=(',', ':')).encode('ascii')

    @property
    def digest(self):
        return hashlib.sha256(self.context()).digest()

    def direction(self, local_peer):
        require(type(local_peer) is PeerID, 'invalid_local_peer')
        if local_peer == self.session.initiator:
            return 0
        require(local_peer == self.session.responder, 'local_peer_not_in_session')
        return 1


@dataclass(frozen=True, slots=True)
class VerifiedFrame:
    kind: FrameKind
    counter: int
    payload: bytes = field(repr=False)


class SessionCipher:
    """One direction at a pinned endpoint, using a durable session store.

    Every retry gets a fresh outer counter. The inner reliable-flow sequence is
    independent. The caller may retain a sealed packet through a single exact
    carrier attempt, but must not treat replay refusal as inner-flow completion.
    A crash after replay commit may lose a delivery; the inner flow must retry.
    """
    def __init__(self, grant, local_peer, store):
        require(type(grant) is BenchGrant, 'invalid_grant')
        self.grant = grant
        self.direction = grant.direction(local_peer)
        self.store = store
        require(store.binding == (grant.digest, self.direction), 'state_binding_mismatch')
        keys = [HKDF(algorithm=hashes.SHA256(), length=32,
                     salt=grant.session.session_generation,
                     info=DOMAIN + bytes([direction]) + grant.context()).derive(grant.secret)
                for direction in (0, 1)]
        self._tx = ChaCha20Poly1305(keys[self.direction])
        self._rx = ChaCha20Poly1305(keys[1 - self.direction])

    def seal(self, payload, kind, *, authorize):
        require(type(payload) is bytes and 0 < len(payload) <= self.grant.max_plaintext,
                'invalid_payload_size')
        require(type(kind) is FrameKind, 'invalid_frame_kind')
        counter = self.store.reserve_transmit(authorize=authorize)
        header = HEADER.pack(MAGIC, VERSION, int(kind), self.direction,
                             self.grant.session.session_id, counter, len(payload))
        nonce = bytes(4) + counter.to_bytes(8, 'big')
        return header + self._tx.encrypt(nonce, payload, DOMAIN + self.grant.digest + header)

    def open(self, packet, *, authorize):
        require(type(packet) is bytes and HEADER.size + TAG_BYTES < len(packet)
                <= HEADER.size + TAG_BYTES + self.grant.max_plaintext, 'invalid_frame_size')
        header = packet[:HEADER.size]
        magic, version, kind, direction, session_id, counter, length = HEADER.unpack(header)
        require(magic == MAGIC and version == VERSION, 'unsupported_wire_version')
        require(direction == 1 - self.direction, 'wrong_direction')
        require(session_id == self.grant.session.session_id, 'unknown_session')
        require(1 <= counter <= self.grant.max_frames, 'invalid_counter')
        require(kind in FrameKind._value2member_map_, 'invalid_frame_kind')
        require(length == len(packet) - HEADER.size - TAG_BYTES, 'length_mismatch')
        self.store.check_live(authorize=authorize)
        nonce = bytes(4) + counter.to_bytes(8, 'big')
        try:
            payload = self._rx.decrypt(nonce, packet[HEADER.size:], DOMAIN + self.grant.digest + header)
        except InvalidTag:
            raise WireError('authentication_failed') from None
        # Persist replay admission before returning any plaintext. A forged frame
        # never moves the window; authority and expiry are checked again here.
        self.store.record_receive(counter, authorize=authorize)
        return VerifiedFrame(FrameKind(kind), counter, payload)
