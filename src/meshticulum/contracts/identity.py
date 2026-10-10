"""Inert identity and deadline values for the isolated MT03 contract candidate.

These constructors validate exact types and namespace relationships. They do
not authenticate peers, authorize services, allocate fresh identifiers, prove
non-reuse, read clocks, or restore live sessions. Trusted admission adapters
must establish those facts independently. Session generation is an opaque
incarnation nonce, compared only for equality; route generation is separate.
"""

from dataclasses import dataclass
from enum import Enum
import re
import uuid

from .errors import ContractError, integer, octets, seconds


MAX_UINT64 = (1 << 64) - 1


def _exact(value, expected, code):
    if type(value) is not expected:
        raise ContractError(code)
    return value


def _nonce(value, code):
    octets(value, 16, code)
    if value == bytes(16):
        raise ContractError(code)


def _canonical_uuid(value):
    if type(value) is not str:
        raise ContractError("invalid_carrier_instance_id")
    try:
        identifier = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ContractError("invalid_carrier_instance_id") from None
    if identifier.int == 0 or str(identifier) != value:
        raise ContractError("invalid_carrier_instance_id")


def _positive_seconds(value, code):
    seconds(value, code=code)
    if value <= 0:
        raise ContractError(code)


@dataclass(frozen=True, slots=True)
class PeerID:
    chain_id: str
    agent_id: str

    def __post_init__(self):
        if (type(self.chain_id) is not str
                or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", self.chain_id) is None):
            raise ContractError("invalid_chain_id")
        if (type(self.agent_id) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.agent_id) is None):
            raise ContractError("invalid_agent_id")


class ServiceKind(str, Enum):
    INTERNET = "internet"
    SAGE = "sage_federation"
    KNOWLEDGE = "knowledge"
    MESSAGE = "protected_message"


@dataclass(frozen=True, slots=True)
class ServiceID:
    provider: PeerID
    kind: ServiceKind

    def __post_init__(self):
        _exact(self.provider, PeerID, "invalid_service_provider")
        _exact(self.kind, ServiceKind, "invalid_service_kind")


@dataclass(frozen=True, slots=True)
class SessionKey:
    initiator: PeerID
    responder: PeerID
    session_id: bytes
    session_generation: bytes

    def __post_init__(self):
        _exact(self.initiator, PeerID, "invalid_session_initiator")
        _exact(self.responder, PeerID, "invalid_session_responder")
        if self.initiator == self.responder:
            raise ContractError("identical_session_peers")
        _nonce(self.session_id, "invalid_session_id")
        _nonce(self.session_generation, "invalid_session_generation")


@dataclass(frozen=True, slots=True)
class FlowKey:
    session: SessionKey
    initiator: PeerID
    flow_id: int

    def __post_init__(self):
        _exact(self.session, SessionKey, "invalid_flow_session")
        _exact(self.initiator, PeerID, "invalid_flow_initiator")
        if self.initiator not in (self.session.initiator, self.session.responder):
            raise ContractError("flow_initiator_not_participant")
        integer(self.flow_id, 1, MAX_UINT64, "invalid_flow_id")


@dataclass(frozen=True, slots=True)
class RouteBinding:
    session: SessionKey
    service: ServiceID
    carrier_instance_id: str
    carrier_binding_digest: bytes
    profile_digest: bytes
    route_generation: int

    def __post_init__(self):
        _exact(self.session, SessionKey, "invalid_route_session")
        _exact(self.service, ServiceID, "invalid_route_service")
        if self.service.provider not in (self.session.initiator, self.session.responder):
            raise ContractError("service_provider_not_participant")
        _canonical_uuid(self.carrier_instance_id)
        octets(self.carrier_binding_digest, 32, "invalid_carrier_binding_digest")
        octets(self.profile_digest, 32, "invalid_profile_digest")
        integer(self.route_generation, 1, MAX_UINT64, "invalid_route_generation")


@dataclass(frozen=True, slots=True)
class DeadlineSet:
    """Immutable exclusive deadlines with independent monotonic and epoch units.

    check() validates supplied clock values and raises on expiry; success is
    True. Clock validity, monotonic continuity and epoch synchronization are
    external admission requirements. tightened() creates a new value whose
    three bounds are no later than either input's, without changing either.
    """

    application_deadline_monotonic: int | float
    authority_deadline_monotonic: int | float
    authority_expires_epoch: int | float

    def __post_init__(self):
        _positive_seconds(self.application_deadline_monotonic, "invalid_application_deadline")
        _positive_seconds(self.authority_deadline_monotonic, "invalid_authority_deadline")
        _positive_seconds(self.authority_expires_epoch, "invalid_authority_expiry")

    def check(self, now_monotonic, now_epoch):
        seconds(now_monotonic, code="invalid_monotonic_clock")
        seconds(now_epoch, code="invalid_epoch_clock")
        if now_monotonic >= self.application_deadline_monotonic:
            raise ContractError("application_deadline_expired")
        if now_monotonic >= self.authority_deadline_monotonic:
            raise ContractError("authority_deadline_expired")
        if now_epoch >= self.authority_expires_epoch:
            raise ContractError("authority_expired")
        return True

    def tightened(self, other):
        _exact(other, DeadlineSet, "invalid_deadline_set")
        return DeadlineSet(
            min(self.application_deadline_monotonic, other.application_deadline_monotonic),
            min(self.authority_deadline_monotonic, other.authority_deadline_monotonic),
            min(self.authority_expires_epoch, other.authority_expires_epoch),
        )
