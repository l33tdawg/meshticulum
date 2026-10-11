"""Immutable capability values retain one meaning across wire persistence."""
import hashlib
import itertools
import json

import pytest
from meshticulum.contracts.errors import ContractError
from meshticulum.contracts.identity import ServiceKind
from meshticulum.contracts.negotiation import Capabilities, CapabilityLimits, Feature, UntrustedOffer, parse_offer


def canonical():
    limits = CapabilityLimits(max_payload_bytes=32, window_bytes=64, max_unconsumed_bytes=128,
        initial_credit_bytes=32, max_reassembly_frames=4, max_queued_bytes=128,
        max_retry_attempts=2, max_flows=2, max_flows_per_peer=1, max_total_frames=16, max_control_bytes=32)
    return Capabilities((1,), tuple(sorted(ServiceKind, key=lambda x: x.value)),
                        tuple(sorted(Feature, key=lambda x: x.value)), limits)


@pytest.mark.parametrize('field', ['services', 'features'])
def test_every_enum_permutation_has_one_equal_immutable_value_and_wire_roundtrip(field):
    expected = canonical()
    for ordering in itertools.permutations(getattr(expected, field)):
        args = dict(versions=expected.versions, services=expected.services,
                    features=expected.features, limits=expected.limits)
        args[field] = ordering
        value = Capabilities(**args)
        raw = value.to_bytes()
        assert value == expected and hash(value) == hash(expected)
        assert raw == expected.to_bytes()
        decoded = parse_offer(raw)
        assert decoded.capabilities == value
        direct = UntrustedOffer(value, raw, hashlib.sha256(raw).digest())
        assert direct == decoded


@pytest.mark.parametrize('field', ['services', 'features'])
def test_wire_array_permutations_remain_noncanonical_and_refused(field):
    value = json.loads(canonical().to_bytes())
    value[field].reverse()
    raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')
    with pytest.raises(ContractError, match='noncanonical_offer'):
        parse_offer(raw)


@pytest.mark.parametrize('field', ['services', 'features'])
def test_normalization_does_not_accept_duplicate_values(field):
    expected = canonical()
    args = dict(versions=expected.versions, services=expected.services,
                features=expected.features, limits=expected.limits)
    args[field] = getattr(expected, field) + (getattr(expected, field)[0],)
    with pytest.raises(ContractError):
        Capabilities(**args)
