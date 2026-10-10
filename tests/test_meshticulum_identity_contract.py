# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Namespace and deadline witnesses for inert MT03 types; no runtime or carrier.

Run from the repository root with:
    PYTHONPATH=. python3 -m pytest scripts/tests/test_meshticulum_identity_contract.py -q
"""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meshticulum.contracts.errors import ContractError
from meshticulum.contracts.identity import (
    DeadlineSet, FlowKey, PeerID, RouteBinding, ServiceID, ServiceKind, SessionKey,
)


A = PeerID("chain-a", "ab" * 32)
B = PeerID("chain-b", "cd" * 32)
C = PeerID("chain-c", "ef" * 32)
SESSION = SessionKey(A, B, b"s" * 16, b"g" * 16)
SERVICE = ServiceID(B, ServiceKind.SAGE)
ROUTE = RouteBinding(SESSION, SERVICE, "05a29648-debd-4ee7-80a0-691a86ca06c5",
                     b"b" * 32, b"p" * 32, 1)


def test_full_chain_and_key_are_both_peer_identity():
    same_key_other_chain = PeerID("chain-b", A.agent_id)
    same_chain_other_key = PeerID(A.chain_id, B.agent_id)
    assert len({A, same_key_other_chain, same_chain_other_key}) == 3
    assert PeerID(A.chain_id, A.agent_id) == A
    assert PeerID("A" * 128, "00" * 32).chain_id == "A" * 128


def test_service_namespace_is_separate_from_provider_identity():
    services = {ServiceID(B, kind) for kind in ServiceKind}
    assert len(services) == 4
    assert ServiceID(A, ServiceKind.SAGE) not in services


def test_full_session_roles_and_incarnation_prevent_namespace_aliases():
    sessions = {
        SESSION,
        replace(SESSION, initiator=B, responder=A),
        replace(SESSION, session_id=b"t" * 16),
        replace(SESSION, session_generation=b"h" * 16),
        replace(SESSION, initiator=PeerID("chain-other", A.agent_id)),
    }
    assert len(sessions) == 5
    with pytest.raises(TypeError):
        assert SESSION < replace(SESSION, session_generation=b"h" * 16)


def test_each_endpoint_can_allocate_flow_one_without_cross_delivery_identity():
    keys = {
        FlowKey(SESSION, A, 1),
        FlowKey(SESSION, B, 1),
        FlowKey(SESSION, A, 2),
        FlowKey(replace(SESSION, session_generation=b"h" * 16), A, 1),
    }
    assert len(keys) == 4
    assert FlowKey(SESSION, A, (1 << 64) - 1).flow_id == (1 << 64) - 1


def test_route_service_carrier_profile_and_generation_do_not_alias():
    routes = {
        ROUTE,
        replace(ROUTE, service=ServiceID(B, ServiceKind.INTERNET)),
        replace(ROUTE, carrier_instance_id="69f8a74a-37ef-4b26-990c-77200569b9fd"),
        replace(ROUTE, carrier_binding_digest=b"x" * 32),
        replace(ROUTE, profile_digest=b"q" * 32),
        replace(ROUTE, route_generation=2),
    }
    assert len(routes) == 6
    assert all(route.session == SESSION for route in routes)
    assert replace(ROUTE, route_generation=(1 << 64) - 1).route_generation == (1 << 64) - 1


@pytest.mark.parametrize("chain", [None, True, 1, b"chain", "", "A" * 129,
                                  " chain", "chain ", "chain\n", "cháin", "chain/key"])
def test_invalid_chain_names_are_refused_without_alias_normalization(chain):
    with pytest.raises(ContractError, match="^invalid_chain_id$"):
        PeerID(chain, A.agent_id)


@pytest.mark.parametrize("key", [None, True, 1, bytes(32), "", "ab" * 31,
                                "ab" * 33, "AB" * 32, "gg" * 32,
                                "ab" * 32 + "\n"])
def test_agent_key_requires_exact_lowercase_full_hex(key):
    with pytest.raises(ContractError, match="^invalid_agent_id$"):
        PeerID(A.chain_id, key)


class PeerAlias(PeerID):
    pass


class SessionAlias(SessionKey):
    pass


class ServiceAlias(ServiceID):
    pass


class DeadlineAlias(DeadlineSet):
    pass


@pytest.mark.parametrize("factory", [
    lambda: ServiceID({"chain_id": A.chain_id, "agent_id": A.agent_id}, ServiceKind.SAGE),
    lambda: ServiceID(PeerAlias(A.chain_id, A.agent_id), ServiceKind.SAGE),
    lambda: ServiceID(A, "sage_federation"),
    lambda: ServiceID(A, True),
    lambda: SessionKey({"chain_id": A.chain_id, "agent_id": A.agent_id}, B, b"s" * 16, b"g" * 16),
    lambda: SessionKey(A, PeerAlias(B.chain_id, B.agent_id), b"s" * 16, b"g" * 16),
    lambda: SessionKey(A, A, b"s" * 16, b"g" * 16),
    lambda: FlowKey("session-ref", A, 1),
    lambda: FlowKey(SessionAlias(A, B, b"s" * 16, b"g" * 16), A, 1),
    lambda: FlowKey(SESSION, PeerAlias(A.chain_id, A.agent_id), 1),
    lambda: FlowKey(SESSION, C, 1),
    lambda: RouteBinding("session-ref", SERVICE, ROUTE.carrier_instance_id, b"b" * 32, b"p" * 32, 1),
    lambda: replace(ROUTE, service="sage_federation"),
    lambda: replace(ROUTE, service=ServiceAlias(B, ServiceKind.SAGE)),
    lambda: replace(ROUTE, service=ServiceID(C, ServiceKind.SAGE)),
])
def test_shape_lookalikes_and_nonparticipants_do_not_create_contract_context(factory):
    with pytest.raises(ContractError):
        factory()


@pytest.mark.parametrize("field", ["session_id", "session_generation"])
@pytest.mark.parametrize("nonce", [None, True, 1, "s" * 16, b"s" * 15,
                                  b"s" * 17, bytes(16), bytearray(b"s" * 16),
                                  memoryview(b"s" * 16)])
def test_both_session_nonces_require_exact_nonzero_octets(field, nonce):
    with pytest.raises(ContractError):
        replace(SESSION, **{field: nonce})


@pytest.mark.parametrize("identifier", [None, True, False, 0, -1, 1.0, "1", 1 << 64])
def test_flow_and_route_generations_do_not_wrap_or_coerce(identifier):
    with pytest.raises(ContractError, match="^invalid_flow_id$"):
        FlowKey(SESSION, A, identifier)
    with pytest.raises(ContractError, match="^invalid_route_generation$"):
        replace(ROUTE, route_generation=identifier)


@pytest.mark.parametrize("identifier", [None, True, 1, bytes(16),
    "00000000-0000-0000-0000-000000000000", "05a29648debd4ee780a0691a86ca06c5",
    "05A29648-DEBD-4EE7-80A0-691A86CA06C5", "{05a29648-debd-4ee7-80a0-691a86ca06c5}",
    "urn:uuid:05a29648-debd-4ee7-80a0-691a86ca06c5", ROUTE.carrier_instance_id + "\n"])
def test_carrier_instance_requires_canonical_nonzero_uuid(identifier):
    with pytest.raises(ContractError, match="^invalid_carrier_instance_id$"):
        replace(ROUTE, carrier_instance_id=identifier)


@pytest.mark.parametrize("field", ["carrier_binding_digest", "profile_digest"])
@pytest.mark.parametrize("digest", [None, True, "ab" * 32, b"b" * 31,
                                   b"b" * 33, bytearray(b"b" * 32)])
def test_route_digests_require_exact_full_octets(field, digest):
    with pytest.raises(ContractError):
        replace(ROUTE, **{field: digest})


def test_deadlines_preserve_separate_clock_domains_and_exclusive_expiry():
    deadlines = DeadlineSet(10, 12.5, 1_800_000_000)
    assert deadlines.check(0, 0) is True
    assert deadlines.check(9.999, 1_799_999_999.999) is True
    with pytest.raises(ContractError, match="^application_deadline_expired$"):
        deadlines.check(10, 1_799_999_999)
    with pytest.raises(ContractError, match="^authority_deadline_expired$"):
        DeadlineSet(20, 12.5, 1_800_000_000).check(12.5, 1_799_999_999)
    with pytest.raises(ContractError, match="^authority_expired$"):
        deadlines.check(1, 1_800_000_000)


def test_deadline_tightening_retains_earliest_bound_from_each_input():
    original = DeadlineSet(10, 20, 30)
    candidate = DeadlineSet(100, 15, 25)
    assert original.tightened(candidate) == DeadlineSet(10, 15, 25)
    assert candidate.tightened(original) == DeadlineSet(10, 15, 25)
    assert original == DeadlineSet(10, 20, 30)
    assert candidate == DeadlineSet(100, 15, 25)
    assert original.tightened(DeadlineSet(100, 200, 300)) == original
    tightened = original.tightened(candidate)
    with pytest.raises(ContractError, match="^application_deadline_expired$"):
        tightened.check(10, 24)


@pytest.mark.parametrize("field", ["application_deadline_monotonic",
                                  "authority_deadline_monotonic", "authority_expires_epoch"])
@pytest.mark.parametrize("value", [None, True, False, "10", 0, -1,
                                  float("nan"), float("inf"), float("-inf"),
                                  1e12 + 1, 1 << 4096])
def test_each_deadline_rejects_invalid_time_without_truncation(field, value):
    with pytest.raises(ContractError):
        replace(DeadlineSet(10, 20, 30), **{field: value})


@pytest.mark.parametrize("value", [None, True, False, "0", -1,
                                  float("nan"), float("inf"), float("-inf"),
                                  1e12 + 1, 1 << 4096])
def test_check_rejects_invalid_supplied_clocks_before_success(value):
    deadlines = DeadlineSet(10, 20, 30)
    with pytest.raises(ContractError, match="^invalid_monotonic_clock$"):
        deadlines.check(value, 1)
    with pytest.raises(ContractError, match="^invalid_epoch_clock$"):
        deadlines.check(1, value)


@pytest.mark.parametrize("other", [None, True, (1, 2, 3), {"application_deadline_monotonic": 1},
                                   DeadlineAlias(1, 2, 3)])
def test_deadline_tightening_requires_exact_validated_nested_type(other):
    with pytest.raises(ContractError, match="^invalid_deadline_set$"):
        DeadlineSet(10, 20, 30).tightened(other)


def test_all_contract_values_are_frozen():
    for value, field, replacement in [
        (A, "chain_id", "other"), (SERVICE, "kind", ServiceKind.INTERNET),
        (SESSION, "session_id", b"t" * 16),
        (FlowKey(SESSION, A, 1), "flow_id", 2),
        (ROUTE, "route_generation", 2),
        (DeadlineSet(10, 20, 30), "authority_expires_epoch", 300),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
