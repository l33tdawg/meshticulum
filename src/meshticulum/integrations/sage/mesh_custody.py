"""Content-blind custody for a sealed peer envelope held by another Lantern.

Store-and-forward of a message to an absent radio lives at the Lantern layer,
not inside MeshCore: the firmware's offline queue serves a disconnected app,
not an unreachable peer, and a repeater forwards in real time but keeps
nothing. A *custodian* is a Lantern whose owner opted in to hold deposits for
peers that are not currently reachable, and to hand them over when the peer is
next seen.

The custodian never opens a deposit. ``wrap_custody`` encloses an existing
``LME1.`` or ``LME2.`` sealed envelope (see ``mesh_envelope``) in a small clear header that
carries only what a courier needs to route and deduplicate it:

  * the recipient's six-byte radio key prefix (the same prefix the transport
    already uses to address a contact),
  * a digest of the message id for deduplication,
  * an expiry for the custody copy.

The envelope's sender, recipient, timestamps and content all stay inside the
sealed box; the custodian can neither read nor verify them. Admission is
therefore a *transport* trust decision, not a content decision: a deposit is
accepted only when it arrived from a verified contact whose owner is trusted
to use this node as a custodian. A custodian that drops a deposit can cost the
sender its copy, but it cannot forge one: the recipient verifies the original
sender's signature inside the sealed box, and a substituted wire simply fails
to open.

This module is the codec plus a bounded in-memory store. It performs no radio
or transport work, makes no delivery claim, and holds no key material.
"""

import base64
from dataclasses import dataclass
import hashlib
import struct


PREFIX = "SLC1."
ENVELOPE_PREFIX = "LME1."
ENVELOPE_PREFIXES = (ENVELOPE_PREFIX, "LME2.")
VERSION = 1
#: version, recipient radio-key prefix, message-id digest, expiry, wire length
HEADER = struct.Struct("<B6s16sIH")
MAX_ENVELOPE_BYTES = 4096
MAX_DEPOSIT_BYTES = HEADER.size + MAX_ENVELOPE_BYTES
# A custody copy must never outlive the sealed envelope it carries: the
# recipient re-checks the envelope's own lifetime on receipt, and today the
# attempt envelope ceiling is mesh_envelope.MAX_LIFETIME_SECONDS (24 h).
# Reapproval requires a fresh authenticated attempt and does not refresh a
# stored custody copy. wrap_custody additionally takes the specific attempt
# expiry so an earlier one wins.
MAX_TTL_SECONDS = 24 * 60 * 60
MIN_TTL_SECONDS = 60


class CustodyError(ValueError):
    def __init__(self):
        super().__init__("invalid_custody_deposit")


def _fail():
    raise CustodyError()


def _prefix(value):
    if type(value) is not bytes or len(value) != 6:
        _fail()
    return value


def message_digest(message_id):
    """The custodian-visible dedupe handle for a message id."""
    if type(message_id) is not str or not message_id:
        _fail()
    return hashlib.sha256(message_id.encode("utf-8")).digest()[:16]


@dataclass(frozen=True, repr=False)
class CustodyDeposit:
    recipient_prefix: bytes
    message_digest: bytes
    expires_at: int
    envelope_wire: str

    @property
    def key(self):
        return self.recipient_prefix, self.message_digest

    def to_wire(self):
        envelope = self.envelope_wire.encode("ascii")
        header = HEADER.pack(VERSION, self.recipient_prefix, self.message_digest,
                             self.expires_at, len(envelope))
        return PREFIX + base64.b64encode(header + envelope).decode("ascii")


def wrap_custody(*, recipient_prefix, message_id, envelope_wire, expires_at,
                 envelope_expires_at, now):
    """Wrap one sealed envelope for custody. Caller owns authorization."""
    try:
        _prefix(recipient_prefix)
        digest = message_digest(message_id)
        if (type(envelope_wire) is not str or not envelope_wire.startswith(ENVELOPE_PREFIXES)
                or len(envelope_wire) > MAX_ENVELOPE_BYTES):
            _fail()
        wire = envelope_wire.encode("ascii")
        if (type(expires_at) is not int or type(envelope_expires_at) is not int
                or type(now) is not int or not now < envelope_expires_at <= now + MAX_TTL_SECONDS
                or not now + MIN_TTL_SECONDS <= expires_at <= min(now + MAX_TTL_SECONDS,
                                                                 envelope_expires_at)):
            _fail()
        header = HEADER.pack(VERSION, recipient_prefix, digest, expires_at, len(wire))
        return PREFIX + base64.b64encode(header + wire).decode("ascii")
    except CustodyError:
        raise
    except Exception:
        raise CustodyError() from None


def parse_custody(wire):
    """Validate a custody wire. Expiry is reported, not enforced, so a store
    can distinguish an expired deposit from a malformed one."""
    try:
        if (type(wire) is not str or not wire.startswith(PREFIX)
                or not len(wire) <= len(PREFIX) + 4 * ((MAX_DEPOSIT_BYTES + 2) // 3) + 4):
            _fail()
        encoded = wire[len(PREFIX):].encode("ascii")
        raw = base64.b64decode(encoded, validate=True)
        if base64.b64encode(raw) != encoded or len(raw) <= HEADER.size:
            _fail()
        version, recipient_prefix, digest, expires_at, size = HEADER.unpack(raw[:HEADER.size])
        if version != VERSION or size != len(raw) - HEADER.size or not 0 < size <= MAX_ENVELOPE_BYTES:
            _fail()
        envelope_wire = raw[HEADER.size:].decode("ascii")
        if not envelope_wire.startswith(ENVELOPE_PREFIXES):
            _fail()
        return CustodyDeposit(_prefix(recipient_prefix), digest, expires_at, envelope_wire)
    except CustodyError:
        raise
    except Exception:
        raise CustodyError() from None


class CustodyStore:
    """Bounded custody storage. No transport, no receipts, no delivery claims.

    ``source`` is the caller's authenticated name for the depositing contact
    (for audit and per-source accounting). It is never invented here.
    """

    def __init__(self, *, max_entries=256, max_bytes=1 << 20, max_per_recipient=16,
                 max_per_source=64):
        if (not 1 <= max_entries <= 4096 or not HEADER.size + 16 <= max_bytes <= 1 << 24
                or not 1 <= max_per_recipient <= max_entries
                or not 1 <= max_per_source <= 4096):
            raise CustodyError()
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_per_recipient = max_per_recipient
        self._max_per_source = max_per_source
        self._records = {}

    def admit(self, deposit, *, source, now):
        """Store a deposit; returns 'stored', 'duplicate', 'expired' or
        'rejected'. A duplicate refreshes nothing: the first copy wins."""
        if (type(deposit) is not CustodyDeposit or type(source) is not str or not 0 < len(source) <= 64
                or type(now) is not int):
            raise CustodyError()
        self.purge(now=now)
        if deposit.expires_at <= now:
            return "expired"
        if deposit.key in self._records:
            return "duplicate"
        stored = [record for record in self._records.values()
                  if record["deposit"].recipient_prefix == deposit.recipient_prefix]
        sourced = [record for record in self._records.values() if record["source"] == source]
        used = sum(record["bytes"] for record in self._records.values())
        size = len(deposit.envelope_wire)
        if (len(self._records) >= self._max_entries or len(stored) >= self._max_per_recipient
                or len(sourced) >= self._max_per_source or used + size > self._max_bytes):
            return "rejected"
        self._records[deposit.key] = {"deposit": deposit, "source": source,
                                      "stored_at": now, "bytes": size}
        return "stored"

    def pending_for(self, recipient_prefix, *, now):
        """Oldest first, so a recipient hears a conversation in send order."""
        _prefix(recipient_prefix)
        self.purge(now=now)
        return tuple(record["deposit"] for record in self._records.values()
                     if record["deposit"].recipient_prefix == recipient_prefix)

    def confirm_delivered(self, recipient_prefix, digest):
        """Drop a held copy after caller-verified delivery evidence."""
        _prefix(recipient_prefix)
        if type(digest) is not bytes or len(digest) != 16:
            raise CustodyError()
        return self._records.pop((recipient_prefix, digest), None) is not None

    def purge(self, *, now):
        expired = [key for key, record in self._records.items()
                   if record["deposit"].expires_at <= now]
        for key in expired:
            del self._records[key]
        return len(expired)

    def stats(self, *, now):
        self.purge(now=now)
        recipients = {}
        for record in self._records.values():
            prefix = record["deposit"].recipient_prefix.hex()
            recipients[prefix] = recipients.get(prefix, 0) + 1
        return {"entries": len(self._records),
                "bytes": sum(record["bytes"] for record in self._records.values()),
                "recipients": recipients}

    def records(self):
        """Serializable audit view. Persistence stays with the caller and must
        use the same protected storage as the message vault."""
        return tuple({"source": record["source"], "stored_at": record["stored_at"],
                      "wire": record["deposit"].to_wire()}
                      for record in self._records.values())

    @classmethod
    def load(cls, records, **bounds):
        store = cls(**bounds)
        for record in records:
            deposit = parse_custody(record["wire"])
            if store.admit(deposit, source=record["source"], now=record["stored_at"]) != "stored":
                raise CustodyError()
        return store
