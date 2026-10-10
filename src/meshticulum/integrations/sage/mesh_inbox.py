# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Durable, bounded fragment ingestion into protected auxiliary workflow storage.

One exclusive owner per actor/recipient is required, including across processes.
Eight fixed CAS assembly slots avoid unbounded expired-partial records; at most
two live assemblies per qualified peer. Expiry reuses slots, never inbox records.
Persisted epoch seconds require a trusted wall clock; rollback fails closed.

resolve_peer(IncomingMessage) must return an independently verified full Peer
from the contact registry, or None. A radio prefix alone is not authentication.
Legacy ingress accepts plain contact-text fragment frames. ingest_binary is
separate, opt-in complete private ingress under a caller-held service lease and
independent peer/consent guard. Both use the same create-only replay admission.
Signature verification authenticates the envelope, not individual radio frames.

ingest returns metadata only; __call__ is the literal-True MeshReceiver handler.
Neither sends receipts nor interprets receipt assertions as delivery/read facts.
Inbox records retain sealed wire, original UUID and first capture provenance;
they are create-only here, not canonical SAGE memories. A receipt producer must
separately verify committed inbox state and use an explicit workflow operation.
Uncertain writes latch root admission closed; captured frames require explicit
reconciliation, not ordinary worker resume. Root-owned storage_check must remain
valid until all handlers join.

Definitive application rejects use sixteen reusable disposition slots containing
only operation UUID, frame SHA256/length and a fixed reason code. They are not
receipts or an indefinite audit history. One serialized captured-slot producer
must own this handler: a disposition cannot be displaced before capture clears.
None from resolve_peer means definitively unknown; temporary registry failure
must raise. Storage/capacity/access failures never become rejection dispositions.
Malformed firmware replies still remain driver/receiver unknown outcomes; only
structurally captured IncomingMessage frames reach this application handler.

New inbox rows atomically include an existing-local-key admission signature.
historical_read verifies it before opening the sealed envelope at signed time;
this is private historical evidence, not fresh ingestion, transmit, receipt,
enrollment or public-acceptance authority. Legacy rows are never re-attested.
"""

import hashlib
import base64
import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Literal

from nacl.signing import SigningKey

from .mesh_custody import CustodyError, PREFIX as CUSTODY_PREFIX, parse_custody
from meshticulum.privacy.mesh_envelope import MeshEnvelopeError, Peer, open_envelope
from .mesh_fragments import ASSEMBLY_SECONDS, MAX_ASSEMBLIES, MAX_PER_PEER, FragmentError, decode_fragment
from .meshcore_transport import IncomingMessage, decode_incoming
from .workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


ASSEMBLY_SCHEMA = "sage.lantern.mesh-assembly.v1"
INBOX_SCHEMA = "sage.lantern.mesh-inbox.v1"
ADMISSION_SCHEMA = "sage.lantern.mesh-admission.v1"
ADMISSION_DOMAIN = b"sage.lantern.mesh-admission.v1/signature\x00"
REJECTION_SCHEMA = "sage.lantern.mesh-rejection.v1"
REASONS = frozenset(("unsupported", "unknown_peer", "invalid_fragment", "fragment_conflict",
                     "invalid_envelope", "equivocation", "custody_refused"))


class _Reject(Exception):
    def __init__(self, reason):
        self.reason = reason


class InboxError(RuntimeError):
    def __init__(self):
        super().__init__("mesh_inbox_unavailable")


@dataclass(frozen=True)
class IngestResult:
    status: Literal["partial", "admitted", "duplicate", "custodied", "rejected"]
    record_id: str | None = None
    remote_id: str | None = None
    envelope_kind: str | None = None
    reason: str | None = None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _id(namespace, value):
    digest = hashlib.sha256(namespace.encode() + b"\x00" + _canonical(value)).digest()
    return str(uuid.UUID(bytes=digest[:16], version=5))


def inbox_id(sender, original_id):
    """Domain-separated local UUID; remote UUID is preserved, never overwritten."""
    return _id(INBOX_SCHEMA, [sender.to_dict(), _uuid(original_id)])


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        raise InboxError()
    parsed = uuid.UUID(value)
    if str(parsed) != value or not parsed.int:
        raise InboxError()
    return value


def _peer(value):
    if type(value) is not dict or set(value) != {"chain_id", "public_key"}:
        raise InboxError()
    result = Peer(value["chain_id"], bytes.fromhex(value["public_key"]))
    if result.to_dict() != value:
        raise InboxError()
    return result


class MeshInbox:
    def __init__(self, store, *, recipient, recipient_signing_key, resolve_peer,
                 access_check, storage_check, clock=time.time, custody=None):
        if (type(store) is not WorkflowStore or type(recipient) is not Peer
                or not isinstance(recipient_signing_key, SigningKey)
                or bytes(recipient_signing_key.verify_key) != recipient.public_key
                or store.storage.actor.chain_id != recipient.chain_id
                or store.storage.actor.agent_id != recipient.public_key.hex()
                or not all(callable(callback) for callback in (resolve_peer, access_check, storage_check, clock))):
            raise InboxError()
        if custody is not None and not callable(getattr(custody, "admit", None)):
            raise InboxError()
        self._store, self._recipient, self._key = store, recipient, recipient_signing_key
        self._resolve, self._access, self._storage, self._clock = resolve_peer, access_check, storage_check, clock
        self._custody = custody
        self._mutex = threading.Lock()
        self._last = None
        self._uncertain = False
        self._slots = tuple(_id(ASSEMBLY_SCHEMA, [recipient.to_dict(), index]) for index in range(MAX_ASSEMBLIES))

    def _get(self, identifier):
        try:
            return self._store.get(identifier, kind="mesh_inbound", access_check=self._storage)
        except WorkflowNotFound:
            return None

    def _put(self, identifier, revision, payload):
        try:
            return self._store.put(identifier, "mesh_inbound", revision, payload, access_check=self._storage)
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise

    def _now(self):
        clock = self._clock()
        if type(clock) not in (int, float) or not 0 <= clock <= 2 ** 53 - 1 - ASSEMBLY_SECONDS:
            raise InboxError()
        now = int(clock)
        if self._last is not None and now < self._last:
            raise InboxError()
        self._last = now
        return now

    def _historical(self, local_id, record):
        if not record or record["record_id"] != local_id or record["revision"] != 1:
            raise InboxError()
        stored = record["payload"]
        fields = {"schema", "sender", "recipient", "remote_id", "digest", "wire", "operation_id", "attestation"}
        if type(stored) is not dict or set(stored) != fields or stored["schema"] != INBOX_SCHEMA:
            raise InboxError()
        attestation = stored["attestation"]
        if type(attestation) is not dict or set(attestation) != {"body", "signature"}:
            raise InboxError()
        body = attestation["body"]
        signature = attestation["signature"]
        if type(signature) is not str or len(signature) != 88:
            raise InboxError()
        signature_bytes = base64.b64decode(signature, validate=True)
        if base64.b64encode(signature_bytes).decode("ascii") != signature:
            raise InboxError()
        self._key.verify_key.verify(ADMISSION_DOMAIN + _canonical(body), signature_bytes)
        fields = {"schema", "local_id", "remote_id", "sender", "recipient", "wire_sha256",
                  "envelope_digest", "envelope_kind", "admitted_at", "operation_id"}
        if (type(body) is not dict or set(body) != fields or body["schema"] != ADMISSION_SCHEMA
                or body["local_id"] != local_id or body["remote_id"] != stored["remote_id"]
                or body["sender"] != stored["sender"] or body["recipient"] != self._recipient.to_dict()
                or body["recipient"] != stored["recipient"] or body["envelope_digest"] != stored["digest"]
                or body["operation_id"] != stored["operation_id"]
                or type(body["admitted_at"]) is not int or not 0 <= body["admitted_at"] <= 2 ** 53 - 1
                or type(stored["wire"]) is not str or not 1 <= len(stored["wire"]) <= 4096
                or hashlib.sha256(stored["wire"].encode("ascii")).hexdigest() != body["wire_sha256"]):
            raise InboxError()
        sender = _peer(body["sender"])
        _uuid(body["operation_id"])
        if inbox_id(sender, body["remote_id"]) != local_id:
            raise InboxError()
        opened = open_envelope(stored["wire"], self._key, expected_sender=sender,
                               expected_recipient=self._recipient, now=body["admitted_at"])
        if (opened.message_id != body["remote_id"] or opened.digest != body["envelope_digest"]
                or opened.kind != body["envelope_kind"]):
            raise InboxError()
        return opened

    def historical_read(self, local_id):
        """Current-auth read returning OpenedEnvelope, not fresh admission authority.

        Verify the expected local recipient's attestation before decrypting at
        its authenticated admission time. No legacy backfill or write occurs.
        Receipt kinds are returned as assertions; callers must exclude them from
        message UI listings and must not use this read as receipt authorization.
        """
        if not self._mutex.acquire(blocking=False):
            raise InboxError()
        try:
            _uuid(local_id)
            if self._access() is not True or self._storage() is not True:
                raise InboxError()
            epoch = self._store.storage.snapshot().epoch
            record = self._get(local_id)
            if self._access() is not True:
                raise InboxError()
            result = self._historical(local_id, record)
            if (self._storage() is not True or self._store.storage.snapshot().epoch != epoch
                    or self._access() is not True):
                raise InboxError()
            return result
        except Exception:
            raise InboxError() from None
        finally:
            self._mutex.release()

    def _assembly(self, record, now):
        value = record["payload"]
        fields = {"schema", "recipient", "sender", "digest", "created_at", "expires_at", "parts", "operation_id", "state"}
        if (type(value) is not dict or set(value) != fields or value["schema"] != ASSEMBLY_SCHEMA
                or value["recipient"] != self._recipient.to_dict()
                or type(value["created_at"]) is not int or type(value["expires_at"]) is not int
                or not 0 <= value["created_at"] <= now
                or value["expires_at"] != value["created_at"] + ASSEMBLY_SECONDS
                or value["state"] not in ("collecting", "complete")
                or type(value["parts"]) is not dict or not 1 <= len(value["parts"]) <= 86):
            raise InboxError()
        _peer(value["sender"])
        _uuid(value["operation_id"])
        fragments = []
        for index, text in value["parts"].items():
            fragment = decode_fragment(text)
            if str(fragment.index) != index or fragment.digest.hex() != value["digest"]:
                raise InboxError()
            if fragments and (fragment.count, fragment.size) != (fragments[0].count, fragments[0].size):
                raise InboxError()
            fragments.append(fragment)
        if value["state"] == "complete" and len(fragments) != fragments[0].count:
            raise InboxError()
        return value

    def __call__(self, operation_id, message):
        self.ingest(operation_id, message)
        return True

    def _disposition(self, operation_id, message, reason=None):
        identifier = _id(REJECTION_SCHEMA, [self._recipient.to_dict(), uuid.UUID(operation_id).int % 16])
        record = self._get(identifier)
        digest = hashlib.sha256(message.raw_frame).hexdigest()
        if record:
            value = record["payload"]
            if (type(value) is not dict or set(value) != {"schema", "operation_id", "frame_digest", "frame_bytes", "reason"}
                    or value["schema"] != REJECTION_SCHEMA or value["reason"] not in REASONS
                    or type(value["frame_bytes"]) is not int or not 1 <= value["frame_bytes"] <= 176
                    or type(value["frame_digest"]) is not str or len(value["frame_digest"]) != 64
                    or any(character not in "0123456789abcdef" for character in value["frame_digest"])):
                raise InboxError()
            _uuid(value["operation_id"])
            if uuid.UUID(value["operation_id"]).int % 16 != uuid.UUID(operation_id).int % 16:
                raise InboxError()
            if value["operation_id"] == operation_id:
                if value["frame_digest"] != digest or value["frame_bytes"] != len(message.raw_frame):
                    raise InboxError()
                return IngestResult("rejected", identifier, reason=value["reason"])
        if reason is None:
            return None
        if reason not in REASONS or self._access() is not True:
            raise InboxError()
        self._put(identifier, record["revision"] if record else 0,
                  dict(schema=REJECTION_SCHEMA, operation_id=operation_id, frame_digest=digest,
                       frame_bytes=len(message.raw_frame), reason=reason))
        return IngestResult("rejected", identifier, reason=reason)

    def ingest(self, operation_id, message):
        if not self._mutex.acquire(blocking=False):
            raise InboxError()
        try:
            if self._uncertain or self._access() is not True or self._storage() is not True:
                raise InboxError()
            epoch = self._store.storage.snapshot().epoch
            _uuid(operation_id)
            if type(message) is not IncomingMessage or decode_incoming(message.raw_frame) != message:
                raise InboxError()
            result = self._disposition(operation_id, message)
            if result is None:
                try:
                    result = self._ingest(operation_id, message)
                except _Reject as rejected:
                    result = self._disposition(operation_id, message, rejected.reason)
            if (self._storage() is not True or self._store.storage.snapshot().epoch != epoch
                    or self._access() is not True):
                raise InboxError()
            return result
        except Exception:
            raise InboxError() from None
        finally:
            self._mutex.release()

    def ingest_binary(self, operation_id, packet, *, sender, peer_check):
        """Admit a complete private record from a separately admitted carrier.

        The caller holds the private service lease and an original capture UUID.
        peer_check(full Peer) must revalidate independent pinning and consent,
        including the carrier's current application authority, or raise when
        unavailable. A link identity or a successful codec is insufficient.
        No fragment, custody, public cache or text fallback is involved here.
        Invalid content returns metadata only; it does not settle a captured
        slot or generate a receipt. Uncertain writes require reconciliation by
        a fresh handler using the original operation UUID and sealed record.
        """
        if not self._mutex.acquire(blocking=False):
            raise InboxError()
        try:
            # Keep the binary codec an opt-in dependency. Existing fragment
            # deployments do not need it until this separate API is invoked.
            from meshticulum.privacy.mesh_binary_envelope import BinaryEnvelopeError, to_text
            if (self._uncertain or type(sender) is not Peer or not callable(peer_check)
                    or self._access() is not True or self._storage() is not True):
                raise InboxError()
            _uuid(operation_id)
            epoch = self._store.storage.snapshot().epoch
            if peer_check(sender) is not True:
                raise InboxError()
            try:
                wire = to_text(packet).encode('ascii')
                result = self._admit_envelope(wire, sender, operation_id,
                                              lambda: peer_check(sender) is True)
            except BinaryEnvelopeError:
                result = IngestResult('rejected', reason='invalid_envelope')
            except _Reject as rejected:
                result = IngestResult('rejected', reason=rejected.reason)
            if (self._storage() is not True or self._store.storage.snapshot().epoch != epoch
                    or self._access() is not True or peer_check(sender) is not True):
                raise InboxError()
            return result
        except Exception:
            raise InboxError() from None
        finally:
            self._mutex.release()

    def reconcile_binary(self, packet, *, sender, observed_at, peer_check):
        """Read-only probe for a possibly committed complete-envelope admission.

        A trusted capture owner supplies its authenticated observation time.
        This only compares against an existing recipient-signed inbox row; it
        creates nothing and cannot renew an expired application or envelope.
        Return None when absent, duplicate for matching logical content, or a
        fixed rejection for invalid/equivocating content. Current private read,
        storage and independently pinned peer permission remain mandatory.
        """
        if not self._mutex.acquire(blocking=False):
            raise InboxError()
        try:
            from meshticulum.privacy.mesh_binary_envelope import BinaryEnvelopeError, to_text
            if (self._uncertain or type(sender) is not Peer or not callable(peer_check)
                    or self._access() is not True or self._storage() is not True
                    or peer_check(sender) is not True):
                raise InboxError()
            epoch = self._store.storage.snapshot().epoch
            now = self._now()
            if type(observed_at) is not int or not 0 <= observed_at <= now:
                raise InboxError()
            try:
                opened = open_envelope(to_text(packet), self._key, expected_sender=sender,
                    expected_recipient=self._recipient, now=observed_at)
            except (BinaryEnvelopeError, MeshEnvelopeError):
                result = IngestResult('rejected', reason='invalid_envelope')
            else:
                identifier = inbox_id(sender, opened.message_id)
                record = self._get(identifier)
                result = None
                if record is not None:
                    prior = self._historical(identifier, record)
                    if record['payload']['attestation']['body']['admitted_at'] > now:
                        raise InboxError()
                    result = (IngestResult('duplicate', identifier, opened.message_id, opened.kind)
                        if prior.kind == opened.kind and prior.logical_digest == opened.logical_digest
                        else IngestResult('rejected', reason='equivocation'))
            if (self._access() is not True or self._storage() is not True
                    or self._store.storage.snapshot().epoch != epoch or peer_check(sender) is not True):
                raise InboxError()
            return result
        except Exception:
            raise InboxError() from None
        finally:
            self._mutex.release()

    def _ingest(self, operation_id, message):
        _uuid(operation_id)
        if self._access() is not True:
            raise InboxError()
        if message.kind != "contact" or message.text_type != 0:
            raise _Reject("unsupported")
        sender = self._resolve(message)
        if self._access() is not True:
            raise InboxError()
        if sender is None:
            raise _Reject("unknown_peer")
        if type(sender) is not Peer:
            raise InboxError()
        try:
            fragment = decode_fragment(message.payload.decode("ascii"))
        except (FragmentError, UnicodeError):
            raise _Reject("invalid_fragment") from None
        now = self._now()
        slots = []
        for identifier in self._slots:
            record = self._get(identifier)
            value = self._assembly(record, now) if record else None
            slots.append((identifier, record, value))
        live = [slot for slot in slots if slot[2] and now < slot[2]["expires_at"]]
        matching = [slot for slot in live if slot[2]["sender"] == sender.to_dict()
                    and slot[2]["digest"] == fragment.digest.hex()]
        if len(matching) > 1:
            raise InboxError()
        if matching:
            identifier, record, value = matching[0]
        else:
            free = [slot for slot in slots if not slot[2] or now >= slot[2]["expires_at"]
                    or slot[2]["state"] == "complete"]
            if not free or sum(slot[2]["sender"] == sender.to_dict()
                               and slot[2]["state"] == "collecting" for slot in live) >= MAX_PER_PEER:
                raise InboxError()
            identifier, record, _ = free[0]
            value = dict(schema=ASSEMBLY_SCHEMA, recipient=self._recipient.to_dict(), sender=sender.to_dict(),
                         digest=fragment.digest.hex(), created_at=now, expires_at=now + ASSEMBLY_SECONDS,
                         operation_id=operation_id, parts={}, state="collecting")
        parts = dict(value["parts"])
        text = message.payload.decode("ascii")
        previous = parts.get(str(fragment.index))
        if previous is not None and previous != text:
            raise _Reject("fragment_conflict")
        if parts:
            first = decode_fragment(next(iter(parts.values())))
            if (first.count, first.size) != (fragment.count, fragment.size):
                raise _Reject("fragment_conflict")
        parts[str(fragment.index)] = text
        value = {**value, "parts": parts}
        self._assembly({"payload": value}, now)
        if previous is None:
            record = self._put(identifier, record["revision"] if record else 0, value)
        if self._resolve(message) != sender or self._access() is not True:
            raise InboxError()
        if len(parts) != fragment.count:
            return IngestResult("partial")
        wire = b"".join(decode_fragment(parts[str(index)]).payload for index in range(fragment.count))
        if len(wire) != fragment.size or hashlib.sha256(wire).digest() != fragment.digest:
            raise _Reject("invalid_envelope")
        if self._access() is not True:
            raise InboxError()
        try:
            text = wire.decode("ascii")
        except UnicodeError:
            raise _Reject("invalid_envelope") from None
        if text.startswith(CUSTODY_PREFIX):
            if self._custody is None:
                raise _Reject("unsupported")
            try:
                deposit = parse_custody(text)
            except CustodyError:
                raise _Reject("invalid_envelope") from None
            source = f"{sender.chain_id}:{sender.public_key.hex()}"
            try:
                status = self._custody.admit(deposit, source=source, operation_id=value["operation_id"])
            except Exception:
                raise InboxError() from None
            if status not in ("stored", "duplicate"):
                raise _Reject("custody_refused")
            if value["state"] != "complete":
                self._put(identifier, record["revision"], {**value, "state": "complete"})
            if self._resolve(message) != sender or self._access() is not True:
                raise InboxError()
            return IngestResult("custodied", identifier)
        result = self._admit_envelope(wire, sender, value['operation_id'],
                                      lambda: self._resolve(message) == sender)
        if value["state"] != "complete":
            self._put(identifier, record["revision"], {**value, "state": "complete"})
        if self._resolve(message) != sender or self._access() is not True:
            raise InboxError()
        return result

    def _admit_envelope(self, wire, sender, operation_id, peer_check):
        """Shared create-only replay/admission path for both carrier encodings."""
        if (self._uncertain or peer_check() is not True
                or self._access() is not True or self._storage() is not True):
            raise InboxError()
        text = wire.decode('ascii')
        try:
            opened = open_envelope(text, self._key, expected_sender=sender,
                                   expected_recipient=self._recipient, now=self._now())
        except MeshEnvelopeError:
            raise _Reject("invalid_envelope") from None
        local_id = inbox_id(sender, opened.message_id)
        existing = self._get(local_id)
        if existing:
            prior = self._historical(local_id, existing)
            if prior.kind != opened.kind or prior.logical_digest != opened.logical_digest:
                raise _Reject("equivocation")
            status = "duplicate"
        else:
            admitted_at = self._now()
            try:
                open_envelope(text, self._key, expected_sender=sender,
                              expected_recipient=self._recipient, now=admitted_at)
            except MeshEnvelopeError:
                raise _Reject("invalid_envelope") from None
            if peer_check() is not True or self._access() is not True or self._storage() is not True:
                raise InboxError()
            body = dict(schema=ADMISSION_SCHEMA, local_id=local_id, remote_id=opened.message_id,
                        sender=sender.to_dict(), recipient=self._recipient.to_dict(),
                        wire_sha256=hashlib.sha256(wire).hexdigest(), envelope_digest=opened.digest,
                        envelope_kind=opened.kind, admitted_at=admitted_at, operation_id=operation_id)
            signature = self._key.sign(ADMISSION_DOMAIN + _canonical(body)).signature
            self._put(local_id, 0, dict(schema=INBOX_SCHEMA, sender=sender.to_dict(),
                recipient=self._recipient.to_dict(), remote_id=opened.message_id, digest=opened.digest,
                wire=wire.decode("ascii"), operation_id=operation_id,
                attestation=dict(body=body, signature=base64.b64encode(signature).decode("ascii"))))
            status = "admitted"
        if peer_check() is not True or self._access() is not True or self._storage() is not True:
            raise InboxError()
        return IngestResult(status, local_id, opened.message_id, opened.kind)
