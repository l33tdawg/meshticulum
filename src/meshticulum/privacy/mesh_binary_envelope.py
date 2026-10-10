"""Bounded binary representation of an already sealed LME1/LME2 envelope.

This removes only the outer Base64 encoding. It does not change encryption,
signatures, peer identity, or the original ciphertext, and has no text fallback.
The caller must admit a known-length private record on a binary-capable carrier;
this is not stream framing, public content, or an automatic format negotiation.

Conversion does not authenticate anything. After reassembly, call
``open_envelope(to_text(packet), ...)`` with independently pinned peers, then
enforce the existing protected replay, expiry and consent admission before use.
Relays forward the opaque record without calling the recipient's opener.
"""

import base64
import binascii

from nacl.bindings import crypto_box_SEALBYTES

from .mesh_envelope import (
    ATTEMPT_WIRE_PREFIX,
    MAX_INNER_BYTES,
    MAX_WIRE_BYTES,
    WIRE_PREFIX,
)


_TEXT_TO_BINARY = {WIRE_PREFIX: b"LM\x01", ATTEMPT_WIRE_PREFIX: b"LM\x02"}
_BINARY_TO_TEXT = {binary: text for text, binary in _TEXT_TO_BINARY.items()}
BINARY_HEADER_BYTES = 3
MAX_CIPHERTEXT_BYTES = MAX_INNER_BYTES + crypto_box_SEALBYTES
MAX_BINARY_BYTES = BINARY_HEADER_BYTES + MAX_CIPHERTEXT_BYTES


class BinaryEnvelopeError(ValueError):
    """A fixed refusal that never interpolates envelope content."""

    def __init__(self):
        super().__init__("invalid_binary_mesh_envelope")


def to_binary(wire: str) -> bytes:
    """Remove canonical outer Base64, preserving the exact sealed ciphertext."""
    if type(wire) is not str or not 5 < len(wire) <= MAX_WIRE_BYTES:
        raise BinaryEnvelopeError()
    header = _TEXT_TO_BINARY.get(wire[:5])
    if header is None:
        raise BinaryEnvelopeError()
    try:
        encoded = wire[5:].encode("ascii")
        ciphertext = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        raise BinaryEnvelopeError() from None
    if (not crypto_box_SEALBYTES < len(ciphertext) <= MAX_CIPHERTEXT_BYTES
            or base64.b64encode(ciphertext) != encoded):
        raise BinaryEnvelopeError()
    return header + ciphertext


def to_text(packet: bytes) -> str:
    """Restore canonical LME text for the existing authenticated opener.

    The header is only a format selector. The opener checks that it agrees with
    the encrypted, signed schema. Never treat successful conversion as delivery
    or permission to display, store, forward or execute the enclosed message.
    """
    if (type(packet) is not bytes
            or not BINARY_HEADER_BYTES + crypto_box_SEALBYTES < len(packet) <= MAX_BINARY_BYTES):
        raise BinaryEnvelopeError()
    prefix = _BINARY_TO_TEXT.get(packet[:BINARY_HEADER_BYTES])
    if prefix is None:
        raise BinaryEnvelopeError()
    return prefix + base64.b64encode(packet[BINARY_HEADER_BYTES:]).decode("ascii")
