"""Inert, bounded MT03 negotiation records.

This module implements no authentication protocol, radio access or permission
store. The host supplies a current authenticated transcript verifier and a local
service-policy read lease. A returned record is a snapshot, never dispatch
authority; dispatch must hold its own current broker and service leases.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import json
import secrets
import time

from .carrier import CarrierProfile
from .errors import ContractError, exact_fields, integer, octets, seconds
from .identity import DeadlineSet, PeerID, RouteBinding, ServiceKind, SessionKey


SCHEMA = "sage.meshticulum.capabilities.v1"
MAX_OFFER_BYTES = 4096
IMPLEMENTED_VERSIONS = frozenset({1})


class Feature(str, Enum):
    BYTE_CREDIT = "byte_credit"
    SELECTIVE_ACK_32 = "selective_ack_32"
    INCREMENTAL_STREAM = "incremental_stream"
    HALF_CLOSE = "half_close"
    OBJECT_RESUME = "verified_object_resume"
    PROTECTED_MESSAGE_V2 = "protected_message_v2"


class TrafficClass(str, Enum):
    INTERACTIVE = "interactive"
    BULK = "bulk"


class CommitOutcome(str, Enum):
    REFUSED_SCHEDULER = "refused_by_scheduler_before_send"
    REFUSED_CARRIER = "refused_by_carrier_queue_before_send"
    ACCEPTED = "confirmed_carrier_acceptance"
    UNKNOWN = "attempted_unknown"


BASE_FEATURES = frozenset({Feature.BYTE_CREDIT, Feature.SELECTIVE_ACK_32})
SERVICE_FEATURES = {
    ServiceKind.INTERNET: frozenset({Feature.INCREMENTAL_STREAM, Feature.HALF_CLOSE}),
    ServiceKind.SAGE: frozenset({Feature.INCREMENTAL_STREAM, Feature.HALF_CLOSE}),
    ServiceKind.KNOWLEDGE: frozenset({Feature.OBJECT_RESUME}),
    ServiceKind.MESSAGE: frozenset({Feature.PROTECTED_MESSAGE_V2}),
}


@dataclass(frozen=True)
class CapabilityLimits:
    """Local record budgets; max_control_bytes bounds a logical control record.

    It is not a claim that a control record fits one carrier frame. Bootstrap
    fragmentation and the actual DATA/ACK layout require separate acceptance.
    Both frame-count and byte limits must hold in the eventual flow engine.
    """
    max_payload_bytes: int
    window_bytes: int
    max_unconsumed_bytes: int
    initial_credit_bytes: int
    max_reassembly_frames: int
    max_queued_bytes: int
    max_retry_attempts: int
    max_flows: int
    max_flows_per_peer: int
    max_total_frames: int
    max_control_bytes: int
    sack_bits: int = 32

    def __post_init__(self):
        for name, lo, hi in (
            ("max_payload_bytes", 1, 4096), ("window_bytes", 1, 65536),
            ("max_unconsumed_bytes", 1, 65536), ("initial_credit_bytes", 0, 65536),
            ("max_reassembly_frames", 1, 32), ("max_queued_bytes", 1, 262144),
            ("max_retry_attempts", 1, 64), ("max_flows", 1, 8),
            ("max_flows_per_peer", 1, 2), ("max_total_frames", 1, 65535),
            ("max_control_bytes", 1, 4096), ("sack_bits", 32, 32),
        ):
            integer(getattr(self, name), lo, hi, "invalid_capability_limits")
        if (self.max_payload_bytes > min(self.window_bytes, self.max_unconsumed_bytes,
                                         self.max_queued_bytes)
                or self.initial_credit_bytes > min(self.window_bytes, self.max_unconsumed_bytes)
                or self.window_bytes > min(self.max_unconsumed_bytes,
                                           self.max_reassembly_frames * self.max_payload_bytes)
                or self.max_flows_per_peer > self.max_flows):
            raise ContractError("inconsistent_capability_limits")

    @classmethod
    def from_dict(cls, value):
        exact_fields(value, cls.__dataclass_fields__, "invalid_capability_limits")
        return cls(**value)

    def intersect(self, other, *, payload_capacity):
        if type(other) is not CapabilityLimits:
            raise ContractError("invalid_capability_limits")
        integer(payload_capacity, 1, 4096, "unsupported_payload")
        values = {key: min(getattr(self, key), getattr(other, key))
                  for key in self.__dataclass_fields__}
        values["max_payload_bytes"] = min(values["max_payload_bytes"], payload_capacity)
        values["window_bytes"] = min(values["window_bytes"], values["max_unconsumed_bytes"],
                                      values["max_reassembly_frames"] * values["max_payload_bytes"])
        values["initial_credit_bytes"] = min(values["initial_credit_bytes"], values["window_bytes"])
        return CapabilityLimits(**values)


def _enum_tuple(value, kind, maximum, code):
    if (type(value) is not tuple or not 1 <= len(value) <= maximum
            or any(type(item) is not kind for item in value)
            or len(set(value)) != len(value)):
        raise ContractError(code)


@dataclass(frozen=True)
class Capabilities:
    versions: tuple[int, ...]
    services: tuple[ServiceKind, ...]
    features: tuple[Feature, ...]
    limits: CapabilityLimits

    def __post_init__(self):
        if type(self.versions) is not tuple or not 1 <= len(self.versions) <= 4:
            raise ContractError("invalid_versions")
        for version in self.versions:
            integer(version, 1, 255, "invalid_versions")
        if tuple(sorted(set(self.versions))) != self.versions:
            raise ContractError("invalid_versions")
        _enum_tuple(self.services, ServiceKind, 4, "invalid_services")
        _enum_tuple(self.features, Feature, 6, "invalid_features")
        if type(self.limits) is not CapabilityLimits:
            raise ContractError("invalid_capability_limits")

    def to_bytes(self):
        value = dict(schema=SCHEMA, versions=list(self.versions),
                     services=sorted(item.value for item in self.services),
                     features=sorted(item.value for item in self.features),
                     limits=asdict(self.limits))
        wire = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
        if len(wire) > MAX_OFFER_BYTES:
            raise ContractError("offer_too_large")
        return wire


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ContractError("duplicate_field")
        value[key] = item
    return value


@dataclass(frozen=True)
class UntrustedOffer:
    capabilities: Capabilities
    raw_bytes: bytes
    digest: bytes

    def __post_init__(self):
        if (type(self.capabilities) is not Capabilities or type(self.raw_bytes) is not bytes
                or self.capabilities.to_bytes() != self.raw_bytes
                or type(self.digest) is not bytes
                or self.digest != hashlib.sha256(self.raw_bytes).digest()):
            raise ContractError("invalid_untrusted_offer")


def parse_offer(raw):
    """Decode strict capability metadata; this supplies zero authentication."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_OFFER_BYTES:
        raise ContractError("invalid_offer_size")
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ContractError("invalid_number")))
        exact_fields(value, {"schema", "versions", "services", "features", "limits"}, "invalid_offer_fields")
        if value["schema"] != SCHEMA:
            raise ContractError("unsupported_schema")
        if any(type(value[key]) is not list for key in ("versions", "services", "features")):
            raise ContractError("invalid_offer_fields")
        caps = Capabilities(tuple(value["versions"]), tuple(ServiceKind(x) for x in value["services"]),
                            tuple(Feature(x) for x in value["features"]),
                            CapabilityLimits.from_dict(value["limits"]))
        # One encoding per offer keeps verifier/transcript inputs unambiguous.
        if caps.to_bytes() != raw:
            raise ContractError("noncanonical_offer")
        return UntrustedOffer(caps, raw, hashlib.sha256(raw).digest())
    except ContractError:
        raise
    except Exception:
        raise ContractError("invalid_offer") from None


@dataclass(frozen=True)
class VerifiedOffer:
    """Trusted verifier result, not a proof format this module verifies."""

    peer: PeerID
    session: SessionKey
    route: RouteBinding
    record_digest: bytes
    channel_binding_digest: bytes
    expires_at_monotonic: float

    def __post_init__(self):
        if type(self.peer) is not PeerID or type(self.session) is not SessionKey or type(self.route) is not RouteBinding:
            raise ContractError("invalid_verifier_context")
        if (self.route.session != self.session or self.peer != self.route.service.provider
                or self.peer not in (self.session.initiator, self.session.responder)):
            raise ContractError("invalid_verifier_context")
        octets(self.record_digest, 32, "invalid_verifier_context")
        octets(self.channel_binding_digest, 32, "invalid_verifier_context")
        seconds(self.expires_at_monotonic, minimum=1e-12, code="invalid_verifier_context")


@dataclass(frozen=True)
class AdmissionRequest:
    peer: PeerID
    route: RouteBinding
    offer_digest: bytes
    local_policy_digest: bytes
    deadlines: DeadlineSet
    nonce: bytes

    def __post_init__(self):
        if (type(self.peer) is not PeerID or type(self.route) is not RouteBinding
                or self.peer != self.route.service.provider or type(self.deadlines) is not DeadlineSet):
            raise ContractError("invalid_admission_request")
        octets(self.offer_digest, 32, "invalid_admission_request")
        octets(self.local_policy_digest, 32, "invalid_admission_request")
        octets(self.nonce, 16, "invalid_admission_request")
        if not any(self.nonce):
            raise ContractError("invalid_admission_request")


@dataclass(frozen=True)
class AuthorityGrant:
    """Fresh local service permission held under the host's policy read lease."""

    request: AdmissionRequest
    deadlines: DeadlineSet
    policy_revision: int

    def __post_init__(self):
        if type(self.request) is not AdmissionRequest or type(self.deadlines) is not DeadlineSet:
            raise ContractError("invalid_authority_grant")
        integer(self.policy_revision, 1, (1 << 64) - 1, "invalid_authority_grant")


@dataclass(frozen=True)
class NegotiatedContract:
    version: int
    features: frozenset[Feature]
    limits: CapabilityLimits
    route: RouteBinding
    deadlines: DeadlineSet
    traffic_class: TrafficClass
    authority_revision: int
    admission_nonce: bytes

    def __post_init__(self):
        integer(self.version, 1, 1, "invalid_negotiated_contract")
        if (type(self.features) is not frozenset or not 1 <= len(self.features) <= 6
                or any(type(item) is not Feature for item in self.features)
                or type(self.limits) is not CapabilityLimits or type(self.route) is not RouteBinding
                or type(self.deadlines) is not DeadlineSet or type(self.traffic_class) is not TrafficClass):
            raise ContractError("invalid_negotiated_contract")
        service = self.route.service.kind
        if not (BASE_FEATURES | SERVICE_FEATURES[service]) <= self.features:
            raise ContractError("invalid_negotiated_contract")
        expected = TrafficClass.BULK if service is ServiceKind.KNOWLEDGE else TrafficClass.INTERACTIVE
        if self.traffic_class is not expected:
            raise ContractError("invalid_negotiated_contract")
        integer(self.authority_revision, 1, (1 << 64) - 1, "invalid_negotiated_contract")
        octets(self.admission_nonce, 16, "invalid_negotiated_contract")
        if not any(self.admission_nonce):
            raise ContractError("invalid_negotiated_contract")

    @property
    def raw_sage_stream_authenticated(self):
        # Federation must still wrap a raw stream in its pinned TLS context.
        return False


def _now(monotonic_clock, epoch_clock):
    try:
        mono, epoch = monotonic_clock(), epoch_clock()
        seconds(mono, code="invalid_monotonic_clock")
        seconds(epoch, minimum=1, code="invalid_epoch_clock")
        return mono, epoch
    except ContractError:
        raise
    except Exception:
        raise ContractError("clock_unavailable") from None


def negotiate(raw_offer, *, local_capabilities, expected_peer, route, profile, deadlines,
              authenticate=None, authorize=None, monotonic_clock=time.monotonic, epoch_clock=time.time):
    """Return an inert negotiated snapshot while a fresh service lease is held.

    authenticate(raw, route) must verify the entire external transcript against
    independently pinned peers and return VerifiedOffer. authorize(request)
    must return a context manager holding current per-service local permission;
    it yields AuthorityGrant for this exact fresh request. Neither callback is
    implemented here. Missing, truthy or cached results fail closed.
    """
    if (type(local_capabilities) is not Capabilities or type(expected_peer) is not PeerID
            or type(route) is not RouteBinding or type(profile) is not CarrierProfile
            or type(deadlines) is not DeadlineSet or expected_peer != route.service.provider
            or profile.profile_digest != route.profile_digest):
        raise ContractError("invalid_negotiation_context")
    if not callable(monotonic_clock) or not callable(epoch_clock):
        raise ContractError("clock_unavailable")
    start_mono, start_epoch = _now(monotonic_clock, epoch_clock)
    deadlines.check(start_mono, start_epoch)
    profile.check_fresh(start_mono)
    offer = parse_offer(raw_offer)
    if not callable(authenticate):
        raise ContractError("authentication_required")
    try:
        proof = authenticate(offer.raw_bytes, route)
    except Exception:
        raise ContractError("authentication_unavailable") from None
    if (type(proof) is not VerifiedOffer or proof.peer != expected_peer or proof.session != route.session
            or proof.route != route or proof.record_digest != offer.digest
            or proof.expires_at_monotonic <= start_mono):
        raise ContractError("authentication_refused")
    common = set(local_capabilities.versions) & set(offer.capabilities.versions) & IMPLEMENTED_VERSIONS
    if not common:
        raise ContractError("unsupported_version")
    service = route.service.kind
    if service not in local_capabilities.services or service not in offer.capabilities.services:
        raise ContractError("unsupported_service")
    features = frozenset(local_capabilities.features) & frozenset(offer.capabilities.features)
    if not (BASE_FEATURES | SERVICE_FEATURES[service]) <= features:
        raise ContractError("unsupported_capability")
    limits = local_capabilities.limits.intersect(offer.capabilities.limits,
                                                payload_capacity=profile.payload_capacity)
    if not callable(authorize):
        raise ContractError("service_authorization_required")
    # Bind the local policy snapshot to actual sizing/timing inputs, in addition
    # to the supplied profile reference. A caller-assigned digest is not proof
    # that two local profiles have identical fields.
    policy_bytes = json.dumps(dict(capabilities=local_capabilities.to_bytes().decode("ascii"),
                                   profile={**asdict(profile), "profile_digest": profile.profile_digest.hex()}),
                              sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    request = AdmissionRequest(expected_peer, route, offer.digest,
                               hashlib.sha256(policy_bytes).digest(), deadlines,
                               secrets.token_bytes(16))
    try:
        guard = authorize(request)
    except Exception:
        raise ContractError("service_authorization_unavailable") from None
    if not isinstance(guard, AbstractContextManager):
        raise ContractError("service_authorization_requires_lease")
    contract = None
    try:
        with guard as grant:
            if type(grant) is not AuthorityGrant or grant.request != request:
                raise ContractError("service_authorization_refused")
            current_mono, current_epoch = _now(monotonic_clock, epoch_clock)
            if current_mono < start_mono or current_epoch < start_epoch:
                raise ContractError("clock_regressed")
            bounded_deadlines = deadlines.tightened(grant.deadlines)
            bounded_deadlines.check(current_mono, current_epoch)
            profile.check_fresh(current_mono)
            if proof.expires_at_monotonic <= current_mono:
                raise ContractError("authentication_expired")
            traffic = TrafficClass.BULK if service is ServiceKind.KNOWLEDGE else TrafficClass.INTERACTIVE
            contract = NegotiatedContract(max(common), features, limits, route, bounded_deadlines,
                                          traffic, grant.policy_revision, request.nonce)
    except ContractError:
        raise
    except Exception:
        raise ContractError("service_authorization_unavailable") from None
    if contract is None:
        raise ContractError("service_authorization_refused")
    # A guard's exit may block. Do not return an already-expired snapshot; this
    # remains separate from the current lease required for eventual dispatch.
    end_mono, end_epoch = _now(monotonic_clock, epoch_clock)
    if end_mono < current_mono or end_epoch < current_epoch:
        raise ContractError("clock_regressed")
    contract.deadlines.check(end_mono, end_epoch)
    profile.check_fresh(end_mono)
    if proof.expires_at_monotonic <= end_mono:
        raise ContractError("authentication_expired")
    return contract
