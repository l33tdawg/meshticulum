"""Bounded existing-radio-contact inventory; no adoption or identity authority.

Pinned MeshCore d92964352441e53b93e8667b802e04f6e072b39e. The callback entry point
must run inside the existing client's exclusive transaction and shared deadline.
It is the public driver's delegate, not a second serial reader. No partial result
is usable. Names, GPS and route bytes are discarded, never contact consent.
"""

from dataclasses import dataclass
import math

from nacl.bindings import crypto_core_ed25519_is_valid_point


MAX_CONTACTS = 256
MAX_UNSOLICITED = 32
CONTACT_FRAME_BYTES = 148
# Codes this layer may carry through from the transport. Anything else - a raw
# timeout message, a caller's diagnostic - is sanitised to the historical code,
# so an arbitrary string never becomes the runtime's classification input.
CONTACT_FAILURE_CODES = frozenset(("unexpected_response", "serial_timeout", "serial_incomplete",
                                   "invalid_frame", "serial_unavailable"))


class MeshCoreContactsError(ValueError):
    def __init__(self, code=None):
        # The transport classifies a read-only failure by its first argument (or
        # by the class name when there is none). A refusal raised by the decoder
        # keeps the historical code; a failure that came from the wire carries the
        # transport's own code through, so the runtime can tell a complete but
        # unexpected frame ("unexpected_response") from an unclassifiable stall.
        super().__init__(code if type(code) is str and code else 'meshcore_contacts_unavailable')


def _key(value):
    if (type(value) is not bytes or len(value) != 32
            or not crypto_core_ed25519_is_valid_point(value)):
        raise MeshCoreContactsError()


@dataclass(frozen=True, slots=True, repr=False)
class RadioContact:
    public_key: bytes
    contact_type: int
    flags: int
    last_modified: int


@dataclass(frozen=True, slots=True, repr=False)
class ContactInventory:
    contacts: tuple[RadioContact, ...]
    watermark: int

    def resolve_prefix(self, prefix: bytes) -> RadioContact:
        """Unique observed full key, not authenticated sender or fresh send lease."""
        if type(prefix) is not bytes or len(prefix) != 6:
            raise MeshCoreContactsError()
        matches = [contact for contact in self.contacts if contact.public_key[:6] == prefix]
        if len(matches) != 1:
            raise MeshCoreContactsError()
        return matches[0]

    def unique_prefix(self, public_key: bytes) -> bytes:
        _key(public_key)
        if self.resolve_prefix(public_key[:6]).public_key != public_key:
            raise MeshCoreContactsError()
        return public_key[:6]


class ContactInventoryDecoder:
    """Single-use payload decoder. Exceptions poison it; finish requires EOF."""

    def __init__(self):
        self._count = None
        self._contacts = []
        self._keys = set()
        self._watermark = None
        self._pushes = 0
        self._failed = False

    def feed(self, frame: bytes) -> bool:
        try:
            if (self._failed or self._watermark is not None or type(frame) is not bytes
                    or not 1 <= len(frame) <= 176):
                raise MeshCoreContactsError()
            code = frame[0]
            if code >= 0x80:
                self._pushes += 1
                if code in (0x80, 0x81, 0x8a, 0x8f, 0x90) or self._pushes > MAX_UNSOLICITED:
                    raise MeshCoreContactsError()
                return False
            if self._count is None:
                if code != 2 or len(frame) != 5:
                    raise MeshCoreContactsError()
                self._count = int.from_bytes(frame[1:], 'little')
                if self._count > MAX_CONTACTS:
                    raise MeshCoreContactsError()
            elif code == 3:
                if len(frame) != CONTACT_FRAME_BYTES or len(self._contacts) >= self._count:
                    raise MeshCoreContactsError()
                public_key = frame[1:33]
                _key(public_key)
                modified = int.from_bytes(frame[144:148], 'little')
                if public_key in self._keys or modified == 0:
                    raise MeshCoreContactsError()
                self._keys.add(public_key)
                self._contacts.append(RadioContact(public_key, frame[33], frame[34], modified))
            elif code == 4:
                if len(frame) != 5 or len(self._contacts) != self._count:
                    raise MeshCoreContactsError()
                watermark = int.from_bytes(frame[1:], 'little')
                if watermark != max((contact.last_modified for contact in self._contacts), default=0):
                    raise MeshCoreContactsError()
                self._watermark = watermark
                return True
            else:
                raise MeshCoreContactsError()
            return False
        except Exception:
            self._failed = True
            self._contacts.clear()
            self._keys.clear()
            raise MeshCoreContactsError() from None

    def finish(self) -> ContactInventory:
        if self._failed or self._watermark is None:
            self._failed = True
            self._contacts.clear()
            self._keys.clear()
            raise MeshCoreContactsError()
        return ContactInventory(tuple(self._contacts), self._watermark)


def get_contacts(*, exchange, read_frame, deadline, authorize) -> ContactInventory:
    """Driver delegate: call ONLY under its handshake/lock/_run fence.

    exchange(payload, expected_code, deadline) and read_frame(deadline) are the
    same client's bounded methods. Exceptions must escape into its poison path.
    This function does not own a stream, thread, mutex or independent timeout.
    """
    try:
        if (not callable(exchange) or not callable(read_frame) or not callable(authorize)
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline < 0 or authorize() is not True):
            raise MeshCoreContactsError()
        decoder = ContactInventoryDecoder()
        decoder.feed(exchange(b'\x04', 2, deadline))
        for _ in range(MAX_CONTACTS + MAX_UNSOLICITED + 1):
            if authorize() is not True:
                raise MeshCoreContactsError()
            if decoder.feed(read_frame(deadline)):
                if authorize() is not True:
                    raise MeshCoreContactsError()
                return decoder.finish()
        raise MeshCoreContactsError()
    except Exception as error:
        # Carry the transport's own code through when it is one this layer knows.
        # The transport classifies a read-only failure by args[0] (or by the class
        # name when there is none), and the runtime's bounded re-read depends on
        # that classification: swallowing it here is what made a complete but
        # unexpected frame ("unexpected_response") look like an unclassifiable
        # contacts failure that could never be re-read.
        code = getattr(error, "code", None)
        raise MeshCoreContactsError(code if code in CONTACT_FAILURE_CODES else None) from None
