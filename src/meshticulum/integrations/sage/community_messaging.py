"""Guarded, disabled-by-default SAGE SDK messaging; no HTTP/UI/radio service.

SDK client.py: pipe_inbox_history, pipe_outbox, lookup_agents, pipe_resolve and
message_send. All clients/verifiers/security providers are operator dependencies,
never browser input. They must bind the configured SDK signer to actor and the
authenticated home node, enforce bounded IO, and establish actual encrypted
storage. These assertions are not inferred from a JSON field or a vault cookie.
No claim of recipient-only E2E encryption, vault unloading or permanent history.

Recipient verifier returns authenticated ContactClaim evidence under the current
configured consent policy; directory/resolve JSON alone does not establish that
policy. No verifier means no discovery/send. No cache or durable content store.
All returned payloads are untrusted data, never instructions. History deliberately
omits provider-addressed and foreign rows in this same-node-only foundation.

Explicit TTL is required: current SDK MessageSendResponse requires expires_at,
whereas the server omits it for durable TTL 0. The staged SDK now forwards
pipe_send idempotency keys, but this adapter remains deliberately same-node-only;
that SDK capability alone does not establish federated delivery or consent.
Once message_send is dispatched, any error/revocation means unknown outcome.
Retry only the identical approved request with the same caller-owned key.
Preflight/guard checks cannot retract an already dispatched server mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import math
import re
import time
from typing import Callable, Protocol

from .message_contacts import ContactClaim, RecipientID


class SageMessagingClient(Protocol):
    def pipe_inbox_history(self, limit: int = 20): ...
    def pipe_outbox(self, limit: int = 20): ...
    def lookup_agents(self, name: str, limit: int = 20): ...
    def pipe_resolve(self, to: str): ...
    def message_send(self, to_agent: str, payload: str, idempotency_key: str,
                     intent: str | None = None, ttl_minutes: int | None = None): ...


class MessagingUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("messaging_unavailable")


@dataclass(frozen=True)
class Prerequisites:
    actor: RecipientID
    authenticated_node_and_signer: bool = False
    encrypted_storage_verified: bool = False
    passive_history_supported: bool = False
    local_send_supported: bool = False
    recipient_policy_verified: bool = False
    issuer: str = ""
    policy_binding: str = ""
    storage_epoch: str = ""


@dataclass(frozen=True, repr=False)
class AccessRequest:
    operation: str
    actor: RecipientID
    session_id: str
    recipient: RecipientID | None = None
    payload_digest: str | None = None
    idempotency_key: str | None = None
    ttl_minutes: int | None = None


@dataclass(frozen=True, repr=False)
class HistoryItem:
    message_id: str
    sender: RecipientID
    recipient: RecipientID
    payload: str
    workflow_status: str
    created_at: str
    trust: str = field(default="untrusted_data", init=False)


@dataclass(frozen=True, repr=False)
class SendOutcome:
    outcome: str
    message_id: str | None = None
    idempotent_replay: bool | None = None


def _text(value, maximum, *, empty=False):
    return (type(value) is str and (empty or bool(value)) and "\x00" not in value
            and len(value.encode("utf-8")) <= maximum)


def _record(value):
    if type(value) is not dict:
        value = value.model_dump(mode="json")
    if type(value) is not dict:
        raise MessagingUnavailable()
    return value


def _created_at(value):
    if (type(value) is not str or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
            r"(?:\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value) is None):
        raise MessagingUnavailable()
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class CommunityMessaging:
    def __init__(self, client: SageMessagingClient, *, actor: RecipientID,
                 prerequisites: Callable[[], Prerequisites] | None = None,
                 guard: Callable[[AccessRequest], bool] | None = None,
                 verify_recipient: Callable[[RecipientID, RecipientID, dict], ContactClaim] | None = None,
                 clock: Callable[[], float] = time.time):
        if type(actor) is not RecipientID:
            raise MessagingUnavailable()
        self._client = client
        self._actor = actor
        self._prerequisites = prerequisites
        self._guard = guard
        self._verify = verify_recipient
        self._clock = clock

    def _check(self, request):
        if not _text(request.session_id, 256):
            raise MessagingUnavailable()
        state = self._prerequisites() if self._prerequisites is not None else None
        if (type(state) is not Prerequisites or state.actor != self._actor
                or state.authenticated_node_and_signer is not True or state.encrypted_storage_verified is not True
                or self._guard is None or self._guard(request) is not True):
            raise MessagingUnavailable()
        if request.operation in ("inbox", "outbox"):
            if state.passive_history_supported is not True:
                raise MessagingUnavailable()
        else:
            if (state.recipient_policy_verified is not True or self._verify is None
                    or not _text(state.issuer, 128) or not _text(state.policy_binding, 128)
                    or (request.operation == "send" and state.local_send_supported is not True)):
                raise MessagingUnavailable()
        return state

    def capabilities(self, session_id):
        result = dict(inbox=False, outbox=False, directory=False, send=False,
                      federated_send=False, radio=False, durable_send=False, end_to_end_encrypted=False)
        for operation in ("inbox", "outbox", "directory", "send"):
            try:
                self._check(AccessRequest(operation, self._actor, session_id))
                result[operation] = True
            except Exception:
                pass
        return result

    @staticmethod
    def _limit(limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MessagingUnavailable()

    def _history(self, operation, session_id, limit):
        try:
            self._limit(limit)
            request = AccessRequest(operation, self._actor, session_id)
            state = self._check(request)
            method = self._client.pipe_inbox_history if operation == "inbox" else self._client.pipe_outbox
            response = _record(method(limit=limit))
            rows = response["items"]
            if type(rows) is not list or len(rows) > limit or type(response["count"]) is not int or response["count"] != len(rows):
                raise MessagingUnavailable()
            items = []
            seen = set()
            for row in rows:
                row = _record(row)
                if (row.get("source_chain_id") or row.get("destination_chain_id") or row.get("to_provider")
                        or row.get("from_agent" if operation == "outbox" else "to_agent") != self._actor.agent_id):
                    continue
                sender = RecipientID(self._actor.chain_id, row["from_agent"])
                recipient = RecipientID(self._actor.chain_id, row["to_agent"])
                if (not _text(row["pipe_id"], 256) or row["pipe_id"] in seen
                        or not _text(row["payload"], 256 << 10, empty=True)):
                    raise MessagingUnavailable()
                seen.add(row["pipe_id"])
                status = row.get("status")
                if status not in ("pending", "claimed", "completed", "expired"):
                    status = "unknown"
                items.append(HistoryItem(row["pipe_id"], sender, recipient, row["payload"], status,
                                         _created_at(row.get("created_at"))))
            if self._check(request) != state:
                raise MessagingUnavailable()
            return tuple(items)
        except Exception:
            raise MessagingUnavailable() from None

    def inbox_history(self, session_id: str, *, limit: int = 20):
        return self._history("inbox", session_id, limit)

    def outbox(self, session_id: str, *, limit: int = 20):
        return self._history("outbox", session_id, limit)

    def _recipient(self, request, recipient, record, state):
        claim = self._verify(self._actor, recipient, record)
        now = self._clock()
        if (type(claim) is not ContactClaim or claim.caller != self._actor or claim.recipient != recipient
                or claim.issuer != state.issuer or claim.policy_binding != state.policy_binding
                or claim.findable is not True or claim.messaging_allowed is not True
                or type(now) not in (int, float) or not math.isfinite(now)
                or not claim.issued_at <= now < min(claim.expires_at, claim.issued_at + 60)
                or self._check(request) != state):
            raise MessagingUnavailable()
        return claim

    def directory(self, session_id: str, *, name: str, limit: int = 20):
        try:
            self._limit(limit)
            if not _text(name, 128) or not name.strip():
                raise MessagingUnavailable()
            request = AccessRequest("directory", self._actor, session_id)
            state = self._check(request)
            response = _record(self._client.lookup_agents(name=name, limit=limit))
            rows = response["agents"]
            if type(rows) is not list or len(rows) > limit:
                raise MessagingUnavailable()
            claims = []
            seen = set()
            for row in rows:
                row = _record(row)
                if row.get("status") != "active":
                    raise MessagingUnavailable()
                recipient = RecipientID(self._actor.chain_id, row["agent_id"])
                if recipient in seen:
                    raise MessagingUnavailable()
                seen.add(recipient)
                claims.append(self._recipient(request, recipient, row, state))
            if self._check(request) != state:
                raise MessagingUnavailable()
            return tuple(claims)
        except Exception:
            raise MessagingUnavailable() from None

    def send(self, session_id: str, *, recipient: RecipientID, payload: str,
             idempotency_key: str, ttl_minutes: int, approved: bool):
        dispatched = False
        try:
            if (approved is not True or type(recipient) is not RecipientID
                    or recipient.chain_id != self._actor.chain_id or not _text(payload, 256 << 10)
                    or not _text(idempotency_key, 256) or not idempotency_key.strip()
                    or type(ttl_minutes) is not int or not 1 <= ttl_minutes <= 1440):
                raise MessagingUnavailable()
            request = AccessRequest("send", self._actor, session_id, recipient,
                                    hashlib.sha256(payload.encode()).hexdigest(), idempotency_key, ttl_minutes)
            state = self._check(request)
            resolved = _record(self._client.pipe_resolve(to=recipient.agent_id))
            if (resolved.get("to_agent") != recipient.agent_id or resolved.get("to_provider")
                    or resolved.get("source_chain_id") or resolved.get("destination_chain_id")):
                raise MessagingUnavailable()
            self._recipient(request, recipient, resolved, state)
            if self._check(request) != state:
                raise MessagingUnavailable()
            dispatched = True
            arguments = dict(to_agent=recipient.agent_id, payload=payload,
                             idempotency_key=idempotency_key, ttl_minutes=ttl_minutes)
            if state.storage_epoch:
                arguments["require_encrypted_storage"] = True
            response = _record(self._client.message_send(**arguments))
            if (self._check(request) != state or not _text(response["message_id"], 256)
                    or type(response["idempotent_replay"]) is not bool
                    or response["status"] not in ("pending", "claimed", "completed", "expired")):
                raise MessagingUnavailable()
            return SendOutcome("accepted_by_node", response["message_id"], response["idempotent_replay"])
        except Exception:
            if dispatched:
                return SendOutcome("unknown")
            raise MessagingUnavailable() from None
