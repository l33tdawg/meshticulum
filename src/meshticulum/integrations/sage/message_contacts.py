"""Memory-only private recipient cache, not a directory or delivery authority.

An injected trusted verifier authenticates bounded evidence and returns the
normalized ContactClaim below. This is NOT a SAGE wire schema or a signature
verifier: the integration must verify the exact caller, recipient, issuer,
current policy binding, consent, revision and validity under configured trust.
No verifier means no contacts. Never construct claims from unchecked JSON.

One cache belongs to one caller and one operator-selected policy generation.
Replace the cache after policy changes; do not reuse old bindings. Revocations
are terminal for this instance. Call get again before presenting a recipient;
actual sending still requires SAGE live resolution and authorization. No cached
object grants send permission. Names are labels, not lookup keys. Agent IDs are
exact credential public keys, not a promise of identity across key rotation.

No routes, radio addresses, keys, message content or raw evidence are retained.
Radio binding verification and encrypted persistence are intentionally absent.
The verifier and clock are trusted synchronous dependencies; do not perform
unbounded network IO in them. Cache limits are local policy, not SAGE API limits.
"""

from __future__ import annotations

import math
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass


MAX_EVIDENCE_BYTES = 65536
MAX_CONTACTS = 256
MAX_REVISION = (1 << 63) - 1


class ContactError(ValueError):
    def __init__(self):
        super().__init__("recipient_unavailable")


def _label(value: object, limit: int) -> bool:
    return (type(value) is str and 0 < len(value) <= limit
            and not any(unicodedata.category(character).startswith("C")
                        or unicodedata.category(character) in {"Zl", "Zp"}
                        for character in value)
            and len(value.encode("utf-8")) <= limit * 4)


def _identifier(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) is not None


@dataclass(frozen=True, slots=True)
class RecipientID:
    chain_id: str
    agent_id: str

    def __post_init__(self):
        if (not _identifier(self.chain_id) or type(self.agent_id) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.agent_id) is None):
            raise ContactError()


@dataclass(frozen=True, slots=True)
class ContactClaim:
    caller: RecipientID
    recipient: RecipientID
    display_name: str
    issuer: str
    policy_binding: str
    revision: int
    issued_at: int
    expires_at: int
    findable: bool
    messaging_allowed: bool

    def __post_init__(self):
        if (type(self.caller) is not RecipientID or type(self.recipient) is not RecipientID
                or not _label(self.display_name, 128) or not _identifier(self.issuer)
                or not _identifier(self.policy_binding)
                or type(self.revision) is not int or not 0 <= self.revision <= MAX_REVISION
                or type(self.issued_at) is not int or type(self.expires_at) is not int
                or not 0 <= self.issued_at < self.expires_at <= MAX_REVISION
                or type(self.findable) is not bool or type(self.messaging_allowed) is not bool):
            raise ContactError()


Verifier = Callable[[bytes], ContactClaim]


class RecipientCache:
    """Bounded snapshots plus anti-rollback/revocation fences; no disk IO.

    Revision is a verifier-normalized monotonic revision covering all claim
    fields, not an assumed SAGE field. Equal-revision different claims conflict.
    Expired entries retain revision fences and consume capacity. clear() hides
    cached claims without forgetting fences; a new cache requires fresh
    authoritative verification, not replay of previously trusted evidence.
    """

    def __init__(self, *, caller: RecipientID, issuer: str, policy_binding: str,
                 verifier: Verifier | None = None, clock: Callable[[], float] = time.time,
                 max_age_seconds: int = 60, capacity: int = MAX_CONTACTS):
        if (type(caller) is not RecipientID or not _identifier(issuer)
                or not _identifier(policy_binding) or (verifier is not None and not callable(verifier))
                or not callable(clock) or type(max_age_seconds) is not int
                or not 1 <= max_age_seconds <= 3600 or type(capacity) is not int
                or not 1 <= capacity <= MAX_CONTACTS):
            raise ContactError()
        self._caller = caller
        self._issuer = issuer
        self._binding = policy_binding
        self._verifier = verifier
        self._clock = clock
        self._max_age = max_age_seconds
        self._capacity = capacity
        self._claims: dict[RecipientID, ContactClaim] = {}
        self._revoked: set[RecipientID] = set()
        self._hidden: set[RecipientID] = set()
        self._last_time = 0.0
        self._failed = False
        self._generation = 0
        self._mutex = threading.RLock()

    def _now(self) -> float:
        try:
            current = self._clock()
            if (self._failed or type(current) not in (int, float) or not math.isfinite(current)
                    or current < self._last_time or current > MAX_REVISION):
                raise ContactError()
            self._last_time = current
            return current
        except Exception:
            self._failed = True
            self._hidden.update(self._claims)
            raise ContactError() from None

    def _fresh(self, claim: ContactClaim, now: float) -> bool:
        return claim.issued_at <= now < min(claim.expires_at, claim.issued_at + self._max_age)

    def put(self, evidence: bytes) -> ContactClaim:
        if type(evidence) is not bytes or not 1 <= len(evidence) <= MAX_EVIDENCE_BYTES or self._verifier is None:
            raise ContactError()
        with self._mutex:
            self._now()
            generation = self._generation
        try:
            claim = self._verifier(evidence)
            if type(claim) is not ContactClaim:
                raise ContactError()
        except Exception:
            raise ContactError() from None
        with self._mutex:
            now = self._now()
            if (generation != self._generation or claim.caller != self._caller
                    or claim.issuer != self._issuer or claim.policy_binding != self._binding
                    or claim.recipient in self._revoked or not self._fresh(claim, now)):
                raise ContactError()
            previous = self._claims.get(claim.recipient)
            if previous is not None and (claim.revision < previous.revision
                    or (claim.revision == previous.revision and claim != previous)):
                raise ContactError()
            if previous is None and len(set(self._claims) | self._revoked) >= self._capacity:
                raise ContactError()
            self._claims[claim.recipient] = claim
            if not claim.findable or not claim.messaging_allowed:
                self._revoked.add(claim.recipient)
                raise ContactError()
            self._hidden.discard(claim.recipient)
            return claim

    def get(self, recipient: RecipientID) -> ContactClaim:
        with self._mutex:
            now = self._now()
            if type(recipient) is not RecipientID:
                raise ContactError()
            claim = self._claims.get(recipient)
            if (claim is None or recipient in self._revoked or recipient in self._hidden
                    or not self._fresh(claim, now)):
                raise ContactError()
            return claim

    def revoke(self, recipient: RecipientID) -> None:
        """Trusted local invalidation; never accepts a remote unverified proof."""
        with self._mutex:
            if type(recipient) is not RecipientID:
                raise ContactError()
            if recipient not in self._claims and recipient not in self._revoked:
                if len(set(self._claims) | self._revoked) >= self._capacity:
                    raise ContactError()
            self._revoked.add(recipient)
            self._generation += 1

    def clear(self) -> None:
        with self._mutex:
            self._hidden.update(self._claims)
            self._generation += 1
