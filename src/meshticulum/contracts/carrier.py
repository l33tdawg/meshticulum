"""Local carrier arithmetic and an untrusted MT1 text codec candidate.

Header and authentication sizes are explicit arithmetic inputs. This module
does not settle their wire layout, implement a MAC, authenticate a frame, or
admit a service. A profile's basis records a caller assertion, not provenance
of a physical measurement. No runtime, device or network adapter is imported.
"""

import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum

from .errors import ContractError, integer, octets, seconds


TEXT_PREFIX = "MT1:"
LEGACY_TEXT_PREFIXES = ("SL1:", "LP1:", "LC1:")
MAX_TEXT_BYTES = 128
MAX_BINARY_BYTES = 4096
# Conservative local validation bounds, not measured path characteristics.
MAX_DURATION_SECONDS = 3600
MAX_PROFILE_AGE_SECONDS = 86400


class CarrierKind(StrEnum):
    TEXT = "text"
    BINARY = "binary"


class ProfileBasis(StrEnum):
    SOURCE_FIXTURE = "source_fixture"
    MEASURED_PATH = "measured_path"


def _text_raw_capacity(mtu_bytes):
    integer(mtu_bytes, 1, MAX_TEXT_BYTES, "invalid_text_mtu")
    capacity = ((mtu_bytes - len(TEXT_PREFIX)) // 4) * 3
    if capacity < 1:
        raise ContractError("insufficient_carrier_capacity")
    return capacity


def _positive_seconds(value, maximum, code):
    seconds(value, maximum=maximum, code=code)
    if value <= 0:
        raise ContractError(code)


@dataclass(frozen=True)
class CarrierProfile:
    kind: CarrierKind
    mtu_bytes: int
    header_bytes: int
    authentication_bytes: int
    profile_digest: bytes
    initial_rto_seconds: float
    min_rto_seconds: float
    max_rto_seconds: float
    forward_seconds: float
    firmware_ack_seconds: float
    reverse_control_seconds: float
    measured_at_monotonic: float
    max_age_seconds: float
    basis: ProfileBasis

    def __post_init__(self):
        if type(self.kind) is not CarrierKind:
            raise ContractError("invalid_carrier_kind")
        if type(self.basis) is not ProfileBasis:
            raise ContractError("invalid_profile_basis")
        maximum = MAX_TEXT_BYTES if self.kind is CarrierKind.TEXT else MAX_BINARY_BYTES
        integer(self.mtu_bytes, 1, maximum, "invalid_carrier_mtu")
        integer(self.header_bytes, 0, MAX_BINARY_BYTES, "invalid_header_size")
        integer(self.authentication_bytes, 0, MAX_BINARY_BYTES, "invalid_authentication_size")
        octets(self.profile_digest, 32, "invalid_profile_digest")
        for value in (self.initial_rto_seconds, self.min_rto_seconds, self.max_rto_seconds):
            _positive_seconds(value, MAX_DURATION_SECONDS, "invalid_rto")
        if not self.min_rto_seconds <= self.initial_rto_seconds <= self.max_rto_seconds:
            raise ContractError("invalid_rto_order")
        for value in (self.forward_seconds, self.firmware_ack_seconds, self.reverse_control_seconds):
            seconds(value, maximum=MAX_DURATION_SECONDS, code="invalid_carrier_cost")
        seconds(self.measured_at_monotonic, code="invalid_profile_timestamp")
        _positive_seconds(self.max_age_seconds, MAX_PROFILE_AGE_SECONDS, "invalid_profile_max_age")
        if self.payload_capacity < 1:
            raise ContractError("insufficient_carrier_capacity")

    @property
    def payload_capacity(self):
        """Application payload after explicit header and authentication bytes."""
        raw_capacity = (_text_raw_capacity(self.mtu_bytes)
                        if self.kind is CarrierKind.TEXT else self.mtu_bytes)
        return raw_capacity - self.header_bytes - self.authentication_bytes

    def check_fresh(self, now_monotonic):
        """Reject a time before this observation or at/after its expiry.

        The calling clock adapter owns monotonicity between checks. This frozen
        profile compares each supplied time with its original observation.
        """
        seconds(now_monotonic, code="invalid_profile_check_time")
        if now_monotonic < self.measured_at_monotonic:
            raise ContractError("profile_clock_before_measurement")
        if now_monotonic - self.measured_at_monotonic >= self.max_age_seconds:
            raise ContractError("carrier_profile_expired")


@dataclass(frozen=True, repr=False)
class UntrustedCarrierPayload:
    """Decoded opaque bytes; no authentication or application admission proof."""

    payload: bytes

    def __post_init__(self):
        if type(self.payload) is not bytes or not 1 <= len(self.payload) <= MAX_BINARY_BYTES:
            raise ContractError("invalid_carrier_payload")


def encode_text_frame(payload: bytes, mtu_bytes=MAX_TEXT_BYTES):
    """Encode one nonempty raw frame; headers/tags must already be included."""
    capacity = _text_raw_capacity(mtu_bytes)
    if type(payload) is not bytes or not 1 <= len(payload) <= capacity:
        raise ContractError("invalid_carrier_payload")
    return TEXT_PREFIX + base64.b64encode(payload).decode("ascii")


def decode_text_frame(text, mtu_bytes=MAX_TEXT_BYTES):
    """Decode canonical MT1 text without legacy fallback or authentication."""
    capacity = _text_raw_capacity(mtu_bytes)
    if type(text) is not str or len(text) > mtu_bytes:
        raise ContractError("invalid_carrier_text")
    try:
        encoded = text.encode("ascii")
    except UnicodeError:
        raise ContractError("invalid_carrier_text") from None
    if not encoded.startswith(TEXT_PREFIX.encode("ascii")):
        raise ContractError("invalid_carrier_prefix")
    body = encoded[len(TEXT_PREFIX):]
    try:
        payload = base64.b64decode(body, validate=True)
    except (ValueError, binascii.Error):
        raise ContractError("invalid_carrier_encoding") from None
    if base64.b64encode(payload) != body:
        raise ContractError("noncanonical_carrier_encoding")
    if not 1 <= len(payload) <= capacity:
        raise ContractError("invalid_carrier_payload")
    return UntrustedCarrierPayload(payload)


def classify_text(text):
    """Classify exact prefixes only; a candidate classification proves no validity."""
    if type(text) is not str:
        return "unknown"
    if text.startswith(TEXT_PREFIX):
        return "candidate"
    if text.startswith(LEGACY_TEXT_PREFIXES):
        return "legacy"
    return "unknown"
