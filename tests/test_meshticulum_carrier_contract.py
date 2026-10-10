# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Source-only carrier contract arithmetic and codec boundary controls."""

import base64
from dataclasses import FrozenInstanceError, replace
import math

import pytest

from meshticulum.contracts.carrier import (
    CarrierKind, CarrierProfile, ProfileBasis, UntrustedCarrierPayload,
    classify_text, decode_text_frame, encode_text_frame,
)
from meshticulum.contracts.errors import ContractError


def profile(**changes):
    values = dict(kind=CarrierKind.TEXT, mtu_bytes=128, header_bytes=40,
                  authentication_bytes=16, profile_digest=b"p" * 32,
                  initial_rto_seconds=1.0, min_rto_seconds=0.2, max_rto_seconds=8.0,
                  forward_seconds=0.8, firmware_ack_seconds=0.1,
                  reverse_control_seconds=0.3, measured_at_monotonic=100,
                  max_age_seconds=60, basis=ProfileBasis.SOURCE_FIXTURE)
    return CarrierProfile(**(values | changes))


@pytest.mark.parametrize("mtu,header,authentication,capacity", [
    (128, 40, 16, 37), (127, 40, 16, 34), (124, 40, 0, 50),
    (128, 0, 0, 93), (8, 0, 0, 3), (128, 76, 16, 1),
])
def test_text_capacity_subtracts_every_explicit_byte(mtu, header, authentication, capacity):
    assert profile(mtu_bytes=mtu, header_bytes=header,
                   authentication_bytes=authentication).payload_capacity == capacity


def test_binary_capacity_does_not_subtract_text_prefix_or_base64_overhead():
    assert profile(kind=CarrierKind.BINARY, mtu_bytes=4096,
                   header_bytes=64, authentication_bytes=16).payload_capacity == 4016


@pytest.mark.parametrize("changes", [
    {"kind": "text"}, {"kind": ProfileBasis.SOURCE_FIXTURE},
    {"basis": "source_fixture"}, {"basis": CarrierKind.TEXT},
    {"mtu_bytes": True}, {"mtu_bytes": 128.0}, {"mtu_bytes": 129}, {"mtu_bytes": 0},
    {"header_bytes": True}, {"header_bytes": 1.5}, {"header_bytes": -1},
    {"authentication_bytes": False}, {"authentication_bytes": 2.0},
    {"authentication_bytes": -1}, {"header_bytes": 4097},
    {"kind": CarrierKind.BINARY, "mtu_bytes": 4097},
    {"kind": CarrierKind.BINARY, "mtu_bytes": 16, "authentication_bytes": 16, "header_bytes": 0},
    {"header_bytes": 77, "authentication_bytes": 16},
    {"mtu_bytes": 7, "header_bytes": 0, "authentication_bytes": 0},
    {"profile_digest": b"p" * 31}, {"profile_digest": b"p" * 33},
    {"profile_digest": bytearray(b"p" * 32)}, {"profile_digest": "p" * 32},
])
def test_invalid_profile_fields_refused_before_use(changes):
    with pytest.raises(ContractError):
        profile(**changes)


@pytest.mark.parametrize("field", [
    "initial_rto_seconds", "min_rto_seconds", "max_rto_seconds",
    "forward_seconds", "firmware_ack_seconds", "reverse_control_seconds",
    "measured_at_monotonic", "max_age_seconds",
])
@pytest.mark.parametrize("invalid", [True, -1, math.inf, -math.inf, math.nan, "1"])
def test_duration_fields_refuse_invalid_types_and_nonfinite_values(field, invalid):
    with pytest.raises(ContractError):
        profile(**{field: invalid})


@pytest.mark.parametrize("changes", [
    {"initial_rto_seconds": 0}, {"min_rto_seconds": 0}, {"max_rto_seconds": 0},
    {"max_age_seconds": 0}, {"min_rto_seconds": 2}, {"max_rto_seconds": 0.5},
    {"max_rto_seconds": 3601}, {"forward_seconds": 3601},
    {"firmware_ack_seconds": 3601}, {"reverse_control_seconds": 3601},
    {"max_age_seconds": 86401}, {"measured_at_monotonic": 1e12 + 1},
])
def test_duration_bounds_and_order_refused(changes):
    with pytest.raises(ContractError):
        profile(**changes)


def test_basis_is_explicit_and_costs_remain_separate():
    measured = replace(profile(), basis=ProfileBasis.MEASURED_PATH)
    assert measured.basis is ProfileBasis.MEASURED_PATH
    assert (measured.forward_seconds, measured.firmware_ack_seconds,
            measured.reverse_control_seconds) == (0.8, 0.1, 0.3)
    assert profile(forward_seconds=0, firmware_ack_seconds=0,
                   reverse_control_seconds=0).forward_seconds == 0
    with pytest.raises(FrozenInstanceError):
        measured.mtu_bytes = 1


def test_freshness_has_fixed_measurement_and_exact_expiry_boundary():
    candidate = profile()
    assert candidate.check_fresh(100) is None
    assert candidate.check_fresh(159.999) is None
    for now, code in [(99.999, "profile_clock_before_measurement"),
                      (160, "carrier_profile_expired"), (161, "carrier_profile_expired")]:
        with pytest.raises(ContractError) as error:
            candidate.check_fresh(now)
        assert error.value.code == code
    assert candidate.measured_at_monotonic == 100


@pytest.mark.parametrize("now", [True, -1, math.nan, math.inf, "100", 1e12 + 1])
def test_invalid_freshness_clock_refused(now):
    with pytest.raises(ContractError):
        profile().check_fresh(now)


@pytest.mark.parametrize("size,text_size", [(1, 8), (88, 124), (93, 128)])
def test_text_codec_exact_boundary_and_untrusted_roundtrip(size, text_size):
    payload = bytes(range(size))
    text = encode_text_frame(payload)
    assert len(text.encode("ascii")) == text_size
    decoded = decode_text_frame(text)
    assert type(decoded) is UntrustedCarrierPayload
    assert decoded.payload == payload
    assert not hasattr(decoded, "authenticated")
    assert not hasattr(decoded, "admitted")
    assert "payload=" not in repr(decoded)
    with pytest.raises(FrozenInstanceError):
        decoded.payload = b"changed"


def test_encode_refuses_one_byte_beyond_raw_capacity_and_decoded_overlength():
    with pytest.raises(ContractError):
        encode_text_frame(bytes(94))
    text = "MT1:" + base64.b64encode(bytes(94)).decode("ascii")
    assert len(text) == 132
    with pytest.raises(ContractError):
        decode_text_frame(text)


def test_smaller_mtu_bound_is_shared_by_encode_and_decode():
    assert encode_text_frame(b"abc", mtu_bytes=8) == "MT1:YWJj"
    assert decode_text_frame("MT1:YWJj", mtu_bytes=8).payload == b"abc"
    with pytest.raises(ContractError):
        encode_text_frame(b"abcd", mtu_bytes=8)
    with pytest.raises(ContractError):
        decode_text_frame("MT1:YWJjZA==", mtu_bytes=8)


@pytest.mark.parametrize("mtu", [True, 128.0, 129, 0, 7, math.inf, "128"])
def test_text_codec_refuses_invalid_mtu_for_both_directions(mtu):
    with pytest.raises(ContractError):
        encode_text_frame(b"a", mtu_bytes=mtu)
    with pytest.raises(ContractError):
        decode_text_frame("MT1:YQ==", mtu_bytes=mtu)


@pytest.mark.parametrize("payload", [b"", bytearray(b"a"), memoryview(b"a"), "a", None])
def test_codec_requires_exact_nonempty_bytes(payload):
    with pytest.raises(ContractError):
        encode_text_frame(payload)


@pytest.mark.parametrize("text", [
    "MT1:", "MT1:YQ", "MT1:YQ===", "MT1:YR==", "MT1:YQ==\n",
    "MT1:Y Q==", "MT1:YQ==\x00", "MT1:é", "MT1:!!!!",
    "SL1:YQ==", "LP1:YQ==", "LC1:YQ==", "MT2:YQ==",
    "mt1:YQ==", "MT1YQ==", b"MT1:YQ==", None,
])
def test_decode_refuses_noncanonical_malformed_or_other_service_input(text):
    with pytest.raises(ContractError):
        decode_text_frame(text)


@pytest.mark.parametrize("text,classification", [
    ("MT1:YQ==", "candidate"), ("MT1:!!!!", "candidate"),
    ("MT1:" + "é" * 1000, "candidate"),
    ("SL1:opaque", "legacy"), ("LP1:opaque", "legacy"), ("LC1:opaque", "legacy"),
    ("SL10:opaque", "unknown"), ("mt1:opaque", "unknown"),
    ("MT2:opaque", "unknown"), ("", "unknown"), (None, "unknown"),
    (b"MT1:YQ==", "unknown"),
])
def test_classification_only_selects_prefix_and_never_admits(text, classification):
    assert classify_text(text) == classification
