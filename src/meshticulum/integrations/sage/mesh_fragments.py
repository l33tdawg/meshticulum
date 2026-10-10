"""Bounded, untrusted fragment transport; not authentication or durable delivery.

Only opaque protected envelopes/contact cards belong here, never raw photos or
private plaintext. The full radio sender key must come from a resolved contact,
not its ambiguous six-byte prefix. Persist incoming frames before reassembly;
verify the resulting envelope and durable replay record before displaying it.
Memory-only duplicate suppression expires and is not an application receipt.
"""

import base64
from dataclasses import dataclass, field
import hashlib
import math
import struct
import threading
import time


PREFIX = "SL1:"
HEADER = struct.Struct("!32sHHI")
CHUNK_BYTES = 48
MAX_MESSAGE_BYTES = 4096
MAX_FRAGMENTS = (MAX_MESSAGE_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
MAX_ASSEMBLIES = 8
MAX_PER_PEER = 2
MAX_ENTRIES_PER_PEER = 8
MAX_TERMINAL = 64
MAX_INGRESS_PEERS = 32
INGRESS_BURST = MAX_FRAGMENTS * 2
INGRESS_PER_SECOND = INGRESS_BURST / 60
ASSEMBLY_SECONDS = 900


class FragmentError(ValueError):
    def __init__(self):
        super().__init__("radio_fragment_unavailable")


@dataclass(frozen=True, repr=False)
class Fragment:
    digest: bytes
    index: int
    count: int
    size: int
    payload: bytes


def split_envelope(envelope):
    if type(envelope) is not bytes or not 1 <= len(envelope) <= MAX_MESSAGE_BYTES:
        raise FragmentError()
    digest = hashlib.sha256(envelope).digest()
    count = (len(envelope) + CHUNK_BYTES - 1) // CHUNK_BYTES
    return tuple(PREFIX + base64.b64encode(HEADER.pack(digest, index, count, len(envelope))
                 + envelope[index * CHUNK_BYTES:(index + 1) * CHUNK_BYTES]).decode("ascii")
                 for index in range(count))


def decode_fragment(text):
    try:
        if type(text) is not str or not text.startswith(PREFIX) or len(text) > 128:
            raise FragmentError()
        encoded = text[len(PREFIX):].encode("ascii")
        wire = base64.b64decode(encoded, validate=True)
        if base64.b64encode(wire) != encoded or len(wire) <= HEADER.size:
            raise FragmentError()
        digest, index, count, size = HEADER.unpack(wire[:HEADER.size])
        payload = wire[HEADER.size:]
        if (not 1 <= size <= MAX_MESSAGE_BYTES or count != (size + CHUNK_BYTES - 1) // CHUNK_BYTES
                or not 0 <= index < count or count > MAX_FRAGMENTS
                or len(payload) != min(CHUNK_BYTES, size - index * CHUNK_BYTES)):
            raise FragmentError()
        return Fragment(digest, index, count, size, payload)
    except (ValueError, UnicodeError, struct.error):
        raise FragmentError() from None


@dataclass(repr=False)
class _Assembly:
    count: int
    size: int
    expires: float
    parts: dict = field(default_factory=dict)


class FragmentAssembler:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._last = None
        self._failed = False
        self._assemblies = {}
        self._terminal = {}
        self._ingress = {}
        self._mutex = threading.Lock()

    def _now(self):
        try:
            now = self._clock()
            if (type(now) not in (int, float) or not math.isfinite(now) or now < 0
                    or self._last is not None and now < self._last):
                raise FragmentError()
            self._last = now
            return now
        except Exception:
            self._failed = True
            self._assemblies.clear()
            self._terminal.clear()
            self._ingress.clear()
            raise FragmentError() from None

    def accept(self, sender_key, text):
        if type(sender_key) is not bytes or len(sender_key) != 32:
            raise FragmentError()
        with self._mutex:
            if self._failed:
                raise FragmentError()
            now = self._now()
            self._ingress = {peer: bucket for peer, bucket in self._ingress.items()
                             if now - bucket[1] < ASSEMBLY_SECONDS}
            if sender_key not in self._ingress and len(self._ingress) >= MAX_INGRESS_PEERS:
                raise FragmentError()
            tokens, previous = self._ingress.get(sender_key, (INGRESS_BURST, now))
            tokens = min(INGRESS_BURST, tokens + (now - previous) * INGRESS_PER_SECOND)
            self._ingress[sender_key] = (max(0, tokens - 1), now)
            if tokens < 1:
                raise FragmentError()
            fragment = decode_fragment(text)
            self._assemblies = {key: value for key, value in self._assemblies.items() if now < value.expires}
            self._terminal = {key: expiry for key, expiry in self._terminal.items() if now < expiry}
            key = (sender_key, fragment.digest)
            if key in self._terminal:
                return None
            assembly = self._assemblies.get(key)
            if assembly is None:
                if (len(self._assemblies) >= MAX_ASSEMBLIES
                        or sum(peer == sender_key for peer, _ in self._assemblies) >= MAX_PER_PEER
                        or sum(peer == sender_key for peer, _ in (*self._assemblies, *self._terminal)) >= MAX_ENTRIES_PER_PEER
                        or len(self._assemblies) + len(self._terminal) >= MAX_TERMINAL):
                    raise FragmentError()
                assembly = _Assembly(fragment.count, fragment.size, now + ASSEMBLY_SECONDS)
                self._assemblies[key] = assembly
            if (assembly.count != fragment.count or assembly.size != fragment.size
                    or fragment.index in assembly.parts and assembly.parts[fragment.index] != fragment.payload):
                del self._assemblies[key]
                self._terminal[key] = assembly.expires
                raise FragmentError()
            assembly.parts[fragment.index] = fragment.payload
            if len(assembly.parts) != assembly.count:
                return None
            envelope = b"".join(assembly.parts[index] for index in range(assembly.count))
            del self._assemblies[key]
            self._terminal[key] = assembly.expires
            if len(envelope) != assembly.size or hashlib.sha256(envelope).digest() != fragment.digest:
                raise FragmentError()
            return envelope

    def close(self):
        with self._mutex:
            self._failed = True
            self._assemblies.clear()
            self._terminal.clear()
            self._ingress.clear()
