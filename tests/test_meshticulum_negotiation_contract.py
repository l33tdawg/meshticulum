# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Independent MT03 negotiation witnesses using synthetic trusted callbacks.

These fixtures exercise source contracts only. They establish neither transcript
authentication nor permission-store, stream, scheduler, firmware or radio behavior.
Remote JSON and expected arithmetic are authored independently of the parser.
"""

from contextlib import AbstractContextManager, contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import hashlib
import json
from pathlib import Path
import sys
import traceback

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meshticulum.contracts.carrier import (
    CarrierKind, CarrierProfile, ProfileBasis,
)
from meshticulum.contracts.errors import ContractError
from meshticulum.contracts.identity import (
    DeadlineSet, PeerID, RouteBinding, ServiceID, ServiceKind, SessionKey,
)
from meshticulum.contracts.negotiation import (
    AdmissionRequest, AuthorityGrant, Capabilities, CapabilityLimits,
    CommitOutcome, Feature, NegotiatedContract, TrafficClass, UntrustedOffer,
    VerifiedOffer, negotiate, parse_offer,
)


REMOTE_LIMITS = {
    "max_payload_bytes": 128, "window_bytes": 512,
    "max_unconsumed_bytes": 512, "initial_credit_bytes": 256,
    "max_reassembly_frames": 8, "max_queued_bytes": 4096,
    "max_retry_attempts": 6, "max_flows": 4, "max_flows_per_peer": 2,
    "max_total_frames": 4096, "max_control_bytes": 32, "sack_bits": 32,
}
LOCAL_LIMITS = {
    "max_payload_bytes": 256, "window_bytes": 1024,
    "max_unconsumed_bytes": 1024, "initial_credit_bytes": 512,
    "max_reassembly_frames": 16, "max_queued_bytes": 8192,
    "max_retry_attempts": 8, "max_flows": 8, "max_flows_per_peer": 2,
    "max_total_frames": 8192, "max_control_bytes": 48, "sack_bits": 32,
}
SERVICE_NAMES = ["internet", "knowledge", "protected_message", "sage_federation"]
FEATURE_NAMES = ["byte_credit", "half_close", "incremental_stream",
                 "protected_message_v2", "selective_ack_32", "verified_object_resume"]


def wire_object(**changes):
    value = {
        "schema": "sage.meshticulum.capabilities.v1", "versions": [1],
        "services": list(SERVICE_NAMES), "features": list(FEATURE_NAMES),
        "limits": dict(REMOTE_LIMITS),
    }
    value.update(changes)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


class Rig:
    def __init__(self, kind=ServiceKind.SAGE):
        self.local = PeerID("fixture-home", "a" * 64)
        self.peer = PeerID("fixture-uplink", "b" * 64)
        self.session = SessionKey(self.local, self.peer, b"s" * 16, b"g" * 16)
        self.route = RouteBinding(
            self.session, ServiceID(self.peer, kind),
            "12345678-1234-4234-8234-123456789abc", b"c" * 32, b"p" * 32, 1,
        )
        self.profile = CarrierProfile(
            CarrierKind.TEXT, 128, 13, 16, b"p" * 32,
            2, 1, 8, .25, .5, .75, 90, 60, ProfileBasis.SOURCE_FIXTURE,
        )
        self.local_caps = Capabilities(
            (1, 2), tuple(ServiceKind), tuple(Feature), CapabilityLimits(**LOCAL_LIMITS),
        )
        self.raw = canonical(wire_object())
        self.deadlines = DeadlineSet(190, 180, 2150)
        self.grant_deadlines = DeadlineSet(170, 160, 2100)
        self.mono = 100
        self.epoch = 2000
        self.proof_expiry = 180
        self.requests = []
        self.events = []
        self.on_authenticate = lambda: None
        self.on_authorize = lambda: None
        self.on_enter = lambda: None
        self.on_exit = lambda: None

    def authenticate(self, raw, route):
        self.events.append(("authenticate", raw, route))
        self.on_authenticate()
        return VerifiedOffer(self.peer, self.session, self.route,
                             hashlib.sha256(raw).digest(), b"t" * 32,
                             self.proof_expiry)

    def authorize(self, request):
        self.events.append(("authorize", request))
        self.requests.append(request)
        self.on_authorize()

        @contextmanager
        def lease():
            self.events.append(("enter",))
            self.on_enter()
            try:
                yield AuthorityGrant(request, self.grant_deadlines, 3)
            finally:
                self.on_exit()
                self.events.append(("exit",))
        return lease()

    def run(self, **changes):
        args = dict(local_capabilities=self.local_caps, expected_peer=self.peer,
                    route=self.route, profile=self.profile, deadlines=self.deadlines,
                    authenticate=self.authenticate, authorize=self.authorize,
                    monotonic_clock=lambda: self.mono, epoch_clock=lambda: self.epoch)
        args.update(changes)
        return negotiate(self.raw, **args)


@pytest.fixture
def rig():
    return Rig()


def refused(rig, code, **changes):
    with pytest.raises(ContractError, match=f"^{code}$"):
        rig.run(**changes)


@pytest.mark.parametrize("kind,traffic", [
    (ServiceKind.INTERNET, TrafficClass.INTERACTIVE),
    (ServiceKind.SAGE, TrafficClass.INTERACTIVE),
    (ServiceKind.KNOWLEDGE, TrafficClass.BULK),
    (ServiceKind.MESSAGE, TrafficClass.INTERACTIVE),
])
def test_admitted_service_intersects_bounds_with_independent_text_arithmetic(kind, traffic):
    rig = Rig(kind)
    contract = rig.run()
    # Four prefix bytes plus padded base64 of header + authentication + content:
    # 4 + 4*ceil((13+16+64)/3) = 128; a 65th byte would need 132 bytes.
    assert 4 + 4 * ((13 + 16 + 64 + 2) // 3) == 128
    assert 4 + 4 * ((13 + 16 + 65 + 2) // 3) == 132
    assert asdict(contract.limits) == {**REMOTE_LIMITS, "max_payload_bytes": 64}
    assert contract.version == 1
    assert contract.route == rig.route
    assert contract.deadlines == DeadlineSet(170, 160, 2100)
    assert contract.traffic_class is traffic
    assert contract.authority_revision == 3
    assert contract.admission_nonce == rig.requests[0].nonce
    assert contract.raw_sage_stream_authenticated is False
    assert [event[0] for event in rig.events] == ["authenticate", "authorize", "enter", "exit"]


def test_parse_is_untrusted_and_supplies_no_authority(rig):
    offer = parse_offer(rig.raw)
    assert type(offer) is UntrustedOffer
    assert offer.raw_bytes == rig.raw
    assert offer.digest == hashlib.sha256(rig.raw).digest()
    assert not hasattr(offer, "authenticated")
    assert rig.events == []
    refused(rig, "authentication_required", authenticate=None)
    assert rig.events == []


@pytest.mark.parametrize("result", [None, True, False, "verified", {"authenticated": True}])
def test_truthy_or_shape_only_verifier_results_never_reach_authority(rig, result):
    refused(rig, "authentication_refused", authenticate=lambda *_: result)
    assert rig.requests == []


def test_authentication_exception_fails_closed_before_authorizer(rig):
    def failed(*_):
        raise RuntimeError("synthetic verifier failure")
    refused(rig, "authentication_unavailable", authenticate=failed)
    assert rig.requests == []


def test_message_consent_does_not_supply_a_sage_service_lease(rig):
    refused(rig, "service_authorization_required", authorize=None)
    assert rig.requests == []
    refused(rig, "service_authorization_requires_lease", authorize=lambda _: True)


@pytest.mark.parametrize("result", [None, False, True, "approved", {"messaging_allowed": True}])
def test_authority_lease_must_yield_exact_service_grant(rig, result):
    @contextmanager
    def lease():
        yield result
    refused(rig, "service_authorization_refused", authorize=lambda _: lease())


def test_every_admission_rechecks_authority_with_fresh_nonce(rig):
    first = rig.run()
    second = rig.run()
    assert len(rig.requests) == 2
    assert first.admission_nonce != second.admission_nonce
    assert len(first.admission_nonce) == len(second.admission_nonce) == 16
    assert any(first.admission_nonce) and any(second.admission_nonce)
    assert rig.requests[0].offer_digest == hashlib.sha256(rig.raw).digest()
    assert rig.requests[0].deadlines == rig.deadlines
    cached = AuthorityGrant(rig.requests[0], rig.grant_deadlines, 3)
    @contextmanager
    def replay():
        yield cached
    refused(rig, "service_authorization_refused", authorize=lambda _: replay())


def test_permission_revocation_between_admissions_is_rechecked(rig):
    permission = {"sage_federation": True}
    observed = []
    def authorize(request):
        @contextmanager
        def lease():
            observed.append(request)
            yield (AuthorityGrant(request, rig.grant_deadlines, 3)
                   if permission[request.route.service.kind.value] else None)
        return lease()
    admitted = rig.run(authorize=authorize)
    permission["sage_federation"] = False
    refused(rig, "service_authorization_refused", authorize=authorize)
    assert len(observed) == 2
    assert observed[0].nonce == admitted.admission_nonce
    assert observed[1].nonce != observed[0].nonce


@pytest.mark.parametrize("field", ["offer_digest", "local_policy_digest", "nonce", "deadlines", "route"])
def test_authority_cannot_grant_a_different_admission_request(rig, field):
    def authorize(request):
        changes = {
            "offer_digest": b"x" * 32, "local_policy_digest": b"x" * 32,
            "nonce": b"n" * 16, "deadlines": DeadlineSet(200, 200, 2200),
            "route": replace(rig.route, route_generation=2),
        }
        @contextmanager
        def lease():
            yield AuthorityGrant(replace(request, **{field: changes[field]}), rig.grant_deadlines, 3)
        return lease()
    refused(rig, "service_authorization_refused", authorize=authorize)


def test_local_policy_digest_covers_actual_profile_and_capabilities(rig):
    rig.run()
    first = rig.requests[-1].local_policy_digest
    # Same opaque digest reference, different physical sizing input.
    rig.profile = replace(rig.profile, mtu_bytes=124)
    rig.run()
    second = rig.requests[-1].local_policy_digest
    rig.profile = replace(rig.profile, initial_rto_seconds=3)
    rig.run()
    third = rig.requests[-1].local_policy_digest
    rig.local_caps = replace(rig.local_caps, limits=replace(rig.local_caps.limits, max_retry_attempts=9))
    rig.run()
    fourth = rig.requests[-1].local_policy_digest
    assert len({first, second, third, fourth}) == 4
    rig.run()
    assert rig.requests[-1].local_policy_digest == fourth


@pytest.mark.parametrize("part", ["chain", "agent", "roles", "session_id", "session_generation",
                                "service", "carrier_instance", "carrier_binding", "profile", "generation",
                                "record_digest"])
def test_verified_record_must_match_full_pinned_context(rig, part):
    peer, session, route = rig.peer, rig.session, rig.route
    if part in ("chain", "agent"):
        peer = PeerID("another-chain" if part == "chain" else peer.chain_id,
                      "d" * 64 if part == "agent" else peer.agent_id)
        session = replace(session, responder=peer)
        route = replace(route, session=session, service=ServiceID(peer, route.service.kind))
    elif part == "roles":
        session = replace(session, initiator=rig.peer, responder=rig.local)
        route = replace(route, session=session)
    elif part in ("session_id", "session_generation"):
        session = replace(session, **{part: b"x" * 16})
        route = replace(route, session=session)
    else:
        changes = {
            "service": {"service": ServiceID(peer, ServiceKind.INTERNET)},
            "carrier_instance": {"carrier_instance_id": "22345678-1234-4234-8234-123456789abc"},
            "carrier_binding": {"carrier_binding_digest": b"x" * 32},
            "profile": {"profile_digest": b"x" * 32}, "generation": {"route_generation": 2},
            "record_digest": {},
        }
        route = replace(route, **changes[part])
    proof = VerifiedOffer(peer, session, route,
                          b"x" * 32 if part == "record_digest" else hashlib.sha256(rig.raw).digest(),
                          b"t" * 32, 180)
    refused(rig, "authentication_refused", authenticate=lambda *_: proof)
    assert rig.requests == []


@pytest.mark.parametrize("change", [
    lambda r: {"expected_peer": r.local},
    lambda r: {"profile": replace(r.profile, profile_digest=b"x" * 32)},
    lambda r: {"route": "route-ref"},
    lambda r: {"local_capabilities": {"versions": [1]}},
    lambda r: {"deadlines": (190, 180, 2150)},
])
def test_invalid_local_context_is_refused_before_callbacks(rig, change):
    refused(rig, "invalid_negotiation_context", **change(rig))
    assert rig.events == []


def test_route_change_cannot_extend_application_or_authority_deadlines(rig):
    rig.grant_deadlines = DeadlineSet(900, 800, 3000)
    original = rig.run()
    rig.route = replace(rig.route, route_generation=2)
    rig.mono = 101
    renewed = rig.run()
    assert original.deadlines == renewed.deadlines == rig.deadlines
    assert renewed.route.route_generation == 2
    assert renewed.admission_nonce != original.admission_nonce
    assert rig.requests[-1].route == rig.route


@pytest.mark.parametrize("versions", [[2], [3, 4]])
def test_advertised_unimplemented_versions_are_refused(rig, versions):
    rig.local_caps = replace(rig.local_caps, versions=tuple(versions))
    rig.raw = canonical(wire_object(versions=versions))
    refused(rig, "unsupported_version")
    assert rig.requests == []


@pytest.mark.parametrize("side", ["local", "remote"])
@pytest.mark.parametrize("kind,missing", [
    (ServiceKind.INTERNET, "incremental_stream"), (ServiceKind.INTERNET, "half_close"),
    (ServiceKind.SAGE, "incremental_stream"), (ServiceKind.SAGE, "half_close"),
    (ServiceKind.KNOWLEDGE, "verified_object_resume"),
    (ServiceKind.MESSAGE, "protected_message_v2"),
    (ServiceKind.SAGE, "byte_credit"), (ServiceKind.SAGE, "selective_ack_32"),
])
def test_required_feature_refusal_prevents_finite_only_stream_downgrade(side, kind, missing):
    rig = Rig(kind)
    if side == "local":
        rig.local_caps = replace(rig.local_caps, features=tuple(f for f in Feature if f.value != missing))
    else:
        rig.raw = canonical(wire_object(features=[f for f in FEATURE_NAMES if f != missing]))
    refused(rig, "unsupported_capability")
    assert rig.requests == []


@pytest.mark.parametrize("side", ["local", "remote"])
def test_requested_service_is_fixed_by_route_and_must_be_supported(rig, side):
    if side == "local":
        rig.local_caps = replace(rig.local_caps, services=(ServiceKind.MESSAGE,))
    else:
        rig.raw = canonical(wire_object(services=["protected_message"]))
    refused(rig, "unsupported_service")
    assert rig.requests == []


def test_only_required_service_features_can_suffice_for_knowledge():
    rig = Rig(ServiceKind.KNOWLEDGE)
    names = ["byte_credit", "selective_ack_32", "verified_object_resume"]
    rig.raw = canonical(wire_object(features=names, services=["knowledge"]))
    assert rig.run().features == frozenset({Feature.BYTE_CREDIT, Feature.SELECTIVE_ACK_32, Feature.OBJECT_RESUME})


def test_reduced_carrier_capacity_tightens_window_and_credit_in_bytes(rig):
    # MT1: + base64(13 header + 16 auth + 16 content) needs 64 bytes.
    # Eight retained frames hold at most 128 content bytes; remote credit 256
    # and the former 512-byte window must each fall to that independent bound.
    rig.profile = replace(rig.profile, mtu_bytes=64)
    assert 4 + 4 * ((13 + 16 + 16 + 2) // 3) == 64
    contract = rig.run()
    assert asdict(contract.limits) == {
        **REMOTE_LIMITS, "max_payload_bytes": 16, "window_bytes": 128,
        "initial_credit_bytes": 128,
    }
    # max_control_bytes bounds a logical record, not physical frame fit.
    assert contract.limits.max_control_bytes == 32


@pytest.mark.parametrize("field", ["priority", "traffic_class", "authenticated", "messaging_allowed",
                                  "provider", "route_generation"])
def test_remote_authority_or_priority_labels_are_not_negotiation_inputs(rig, field):
    rig.raw = canonical(wire_object(**{field: "interactive"}))
    refused(rig, "invalid_offer_fields")
    assert rig.events == []


@pytest.mark.parametrize("field,invalid", [
    ("max_payload_bytes", 0), ("max_payload_bytes", 4097),
    ("window_bytes", 0), ("window_bytes", 65537),
    ("max_unconsumed_bytes", 0), ("max_unconsumed_bytes", 65537),
    ("initial_credit_bytes", -1), ("initial_credit_bytes", 65537),
    ("max_reassembly_frames", 0), ("max_reassembly_frames", 33),
    ("max_queued_bytes", 0), ("max_queued_bytes", 262145),
    ("max_retry_attempts", 0), ("max_retry_attempts", 65),
    ("max_flows", 0), ("max_flows", 9),
    ("max_flows_per_peer", 0), ("max_flows_per_peer", 3),
    ("max_total_frames", 0), ("max_total_frames", 65536),
    ("max_control_bytes", 0), ("max_control_bytes", 4097),
    ("sack_bits", 31), ("sack_bits", 64),
])
def test_remote_bounds_refuse_unrepresentable_values_before_authentication(rig, field, invalid):
    value = wire_object()
    value["limits"][field] = invalid
    rig.raw = canonical(value)
    refused(rig, "invalid_capability_limits")
    assert rig.events == []


@pytest.mark.parametrize("field", list(REMOTE_LIMITS))
@pytest.mark.parametrize("invalid", [True, 1.0, "1", None])
def test_limits_do_not_coerce_bools_floats_strings_or_null(rig, field, invalid):
    value = wire_object()
    value["limits"][field] = invalid
    rig.raw = canonical(value)
    refused(rig, "invalid_capability_limits")
    assert rig.events == []


@pytest.mark.parametrize("changes", [
    {"window_bytes": 127}, {"max_unconsumed_bytes": 127}, {"max_queued_bytes": 127},
    {"initial_credit_bytes": 513}, {"max_flows": 1},
    {"window_bytes": 513}, {"max_unconsumed_bytes": 511},
    {"max_reassembly_frames": 3},
])
def test_limits_refuse_advertised_credit_or_payload_exceeding_local_storage(rig, changes):
    value = wire_object()
    value["limits"].update(changes)
    rig.raw = canonical(value)
    refused(rig, "inconsistent_capability_limits")
    assert rig.events == []


@pytest.mark.parametrize("limits", [
    {"max_payload_bytes": 1, "window_bytes": 1, "max_unconsumed_bytes": 1,
     "initial_credit_bytes": 0, "max_reassembly_frames": 1, "max_queued_bytes": 1,
     "max_retry_attempts": 1, "max_flows": 1, "max_flows_per_peer": 1,
     "max_total_frames": 1, "max_control_bytes": 1, "sack_bits": 32},
    {"max_payload_bytes": 4096, "window_bytes": 65536, "max_unconsumed_bytes": 65536,
     "initial_credit_bytes": 65536, "max_reassembly_frames": 32, "max_queued_bytes": 262144,
     "max_retry_attempts": 64, "max_flows": 8, "max_flows_per_peer": 2,
     "max_total_frames": 65535, "max_control_bytes": 4096, "sack_bits": 32},
])
def test_limits_accept_exact_positive_edges_without_claiming_flow_implementation(limits):
    offer = parse_offer(canonical(wire_object(limits=limits)))
    assert asdict(offer.capabilities.limits) == limits


@pytest.mark.parametrize("versions", [[], [0], [256], [True], [1.0], ["1"], [1, 1],
                                    [2, 1], [1, 2, 3, 4, 5]])
def test_versions_are_exact_bounded_unique_sorted_integers(rig, versions):
    rig.raw = canonical(wire_object(versions=versions))
    refused(rig, "invalid_versions")
    assert rig.events == []


@pytest.mark.parametrize("raw,code", [
    (b"", "invalid_offer_size"), (b" " * 4097, "invalid_offer_size"),
    (bytearray(b"{}"), "invalid_offer_size"), ("{}", "invalid_offer_size"),
    (b"SL1:AAAA", "invalid_offer"), (b"MT1:AAAA", "invalid_offer"),
    (b"\xff", "invalid_offer"), (b"[]", "invalid_offer_fields"),
    (b"{\"schema\":1,\"schema\":2}", "duplicate_field"),
])
def test_offer_encoding_refuses_ambiguous_or_wrong_domain_input(rig, raw, code):
    rig.raw = raw
    refused(rig, code)
    assert rig.events == []


def test_denial_diagnostics_do_not_log_untrusted_content_or_callback_secrets(rig, caplog, capsys):
    canary = "private-fixture-canary-7f05"
    secret = "secret-fixture-canary-681e"
    raw = canonical(wire_object(**{"payload": canary}))
    with pytest.raises(ContractError) as denied:
        parse_offer(raw)
    assert str(denied.value) == "invalid_offer_fields"
    assert canary not in repr(denied.value)
    def failed(*_):
        raise RuntimeError(secret)
    with pytest.raises(ContractError) as denied:
        rig.run(authenticate=failed)
    formatted = "".join(traceback.format_exception(denied.value))
    assert str(denied.value) == "authentication_unavailable"
    assert secret not in formatted
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("transform,code", [
    (lambda raw: b" " + raw, "noncanonical_offer"),
    (lambda raw: raw + b"\n", "noncanonical_offer"),
    (lambda raw: raw.replace(b'"versions":[1]', b'"versions":[1e0]'), "invalid_versions"),
    (lambda raw: raw.replace(b'"max_flows":4', b'"max_flows":NaN'), "invalid_number"),
    (lambda raw: raw.replace(b'"max_flows":4', b'"max_flows":Infinity'), "invalid_number"),
    (lambda raw: raw.replace(b'"max_flows":4', b'"max_flows":4,"max_flows":4'), "duplicate_field"),
    (lambda raw: raw.replace(b'"internet"', b'"\\u0069nternet"'), "noncanonical_offer"),
    (lambda raw: raw.replace(b'"schema":"sage.meshticulum.capabilities.v1"',
                            b'"schema":"sage.meshticulum.capabilities.v2"'), "unsupported_schema"),
])
def test_one_canonical_transcript_encoding_and_explicit_schema(rig, transform, code):
    rig.raw = transform(rig.raw)
    refused(rig, code)
    assert rig.events == []


@pytest.mark.parametrize("mutate,code", [
    (lambda v: v["limits"].update(extra=1), "invalid_capability_limits"),
    (lambda v: v["limits"].pop("sack_bits"), "invalid_capability_limits"),
    (lambda v: v.update(services=["internet", "internet"]), "invalid_services"),
    (lambda v: v.update(features=["byte_credit", "byte_credit"]), "invalid_features"),
    (lambda v: v.update(features=["selective_ack_64"]), "invalid_offer"),
    (lambda v: v.update(services=["udp_tunnel"]), "invalid_offer"),
    (lambda v: v.update(features=list(reversed(FEATURE_NAMES))), "noncanonical_offer"),
    (lambda v: v.update(services=list(reversed(SERVICE_NAMES))), "noncanonical_offer"),
    (lambda v: v.update(versions=1), "invalid_offer_fields"),
])
def test_strict_shape_and_known_features(rig, mutate, code):
    value = wire_object()
    mutate(value)
    rig.raw = canonical(value)
    refused(rig, code)
    assert rig.events == []


@pytest.mark.parametrize("phase", ["on_authenticate", "on_authorize", "on_enter", "on_exit"])
@pytest.mark.parametrize("boundary,code", [
    ("application", "application_deadline_expired"),
    ("authority_monotonic", "authority_deadline_expired"),
    ("authority_epoch", "authority_expired"),
    ("profile", "carrier_profile_expired"), ("proof", "authentication_expired"),
    ("monotonic_regression", "clock_regressed"), ("epoch_regression", "clock_regressed"),
])
def test_callback_and_lease_exit_time_races_fail_at_exact_boundary(rig, phase, boundary, code):
    # Keep unrelated limits away from the chosen exclusive boundary.
    rig.profile = replace(rig.profile, max_age_seconds=1000)
    rig.deadlines = DeadlineSet(400, 400, 2400)
    rig.grant_deadlines = DeadlineSet(400, 400, 2400)
    rig.proof_expiry = 400
    if boundary == "application":
        rig.deadlines = DeadlineSet(120, 400, 2400)
        mutate = lambda: setattr(rig, "mono", 120)
    elif boundary == "authority_monotonic":
        rig.grant_deadlines = DeadlineSet(400, 120, 2400)
        mutate = lambda: setattr(rig, "mono", 120)
    elif boundary == "authority_epoch":
        rig.grant_deadlines = DeadlineSet(400, 400, 2020)
        mutate = lambda: setattr(rig, "epoch", 2020)
    elif boundary == "profile":
        rig.profile = replace(rig.profile, max_age_seconds=30)
        mutate = lambda: setattr(rig, "mono", 120)
    elif boundary == "proof":
        rig.proof_expiry = 120
        mutate = lambda: setattr(rig, "mono", 120)
    elif boundary == "monotonic_regression":
        mutate = lambda: setattr(rig, "mono", 99)
    else:
        mutate = lambda: setattr(rig, "epoch", 1999)
    setattr(rig, phase, mutate)
    refused(rig, code)


def test_verified_offer_already_expired_never_reaches_authorizer(rig):
    rig.proof_expiry = rig.mono
    refused(rig, "authentication_refused")
    assert rig.requests == []


@pytest.mark.parametrize("field,value,code", [
    ("mono", True, "invalid_monotonic_clock"), ("mono", float("nan"), "invalid_monotonic_clock"),
    ("mono", float("inf"), "invalid_monotonic_clock"), ("mono", -1, "invalid_monotonic_clock"),
    ("epoch", 0, "invalid_epoch_clock"), ("epoch", True, "invalid_epoch_clock"),
    ("epoch", float("nan"), "invalid_epoch_clock"), ("epoch", float("inf"), "invalid_epoch_clock"),
])
def test_invalid_clocks_deny_before_callbacks(rig, field, value, code):
    setattr(rig, field, value)
    refused(rig, code)
    assert rig.events == []


def test_unavailable_clock_or_authority_denies(rig):
    def failed(*_):
        raise RuntimeError("synthetic unavailable dependency")
    refused(rig, "clock_unavailable", monotonic_clock=None)
    refused(rig, "clock_unavailable", epoch_clock=failed)
    assert rig.events == []
    refused(rig, "service_authorization_unavailable", authorize=failed)


@pytest.mark.parametrize("failure", ["enter", "exit"])
def test_lease_failure_does_not_return_a_contract(rig, failure):
    class Lease(AbstractContextManager):
        def __init__(self, request):
            self.request = request
        def __enter__(self):
            if failure == "enter":
                raise RuntimeError("synthetic lease failure")
            return AuthorityGrant(self.request, rig.grant_deadlines, 3)
        def __exit__(self, *args):
            raise RuntimeError("synthetic lease failure")
    refused(rig, "service_authorization_unavailable", authorize=Lease)


@pytest.mark.parametrize("fault", ["denial", "expiry"])
def test_suppressing_context_manager_cannot_turn_a_refusal_into_success(rig, fault):
    class SuppressingLease(AbstractContextManager):
        def __init__(self, request):
            self.request = request
        def __enter__(self):
            if fault == "denial":
                return True
            rig.epoch = 2100
            return AuthorityGrant(self.request, rig.grant_deadlines, 3)
        def __exit__(self, *args):
            assert args[0] is ContractError
            return True
    refused(rig, "service_authorization_refused", authorize=SuppressingLease)


def test_new_record_constructors_do_not_accept_shape_only_or_changed_transcripts(rig):
    parsed = parse_offer(rig.raw)
    with pytest.raises(ContractError, match="^invalid_untrusted_offer$"):
        replace(parsed, digest=b"x" * 32)
    with pytest.raises(ContractError, match="^invalid_untrusted_offer$"):
        replace(parsed, raw_bytes=rig.raw + b" ")
    with pytest.raises(ContractError, match="^invalid_untrusted_offer$"):
        replace(parsed, capabilities={"authenticated": True})
    admitted = rig.run()
    for changes in [
        {"version": True}, {"version": 2}, {"features": set(admitted.features)},
        {"features": frozenset({Feature.BYTE_CREDIT, Feature.SELECTIVE_ACK_32})},
        {"traffic_class": TrafficClass.BULK}, {"authority_revision": True},
        {"authority_revision": 0}, {"authority_revision": 1 << 64},
        {"admission_nonce": bytes(16)}, {"admission_nonce": "n" * 16},
    ]:
        with pytest.raises(ContractError, match="^invalid_negotiated_contract$"):
            replace(admitted, **changes)
    for record, field, value in [(parsed, "digest", b"x" * 32),
                                 (admitted, "traffic_class", TrafficClass.BULK),
                                 (rig.requests[0], "nonce", b"n" * 16)]:
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, value)


def test_commit_outcomes_remain_distinct_and_are_not_exercised_as_dispatch():
    assert {item.value for item in CommitOutcome} == {
        "refused_by_scheduler_before_send", "refused_by_carrier_queue_before_send",
        "confirmed_carrier_acceptance", "attempted_unknown",
    }
    assert not hasattr(NegotiatedContract, "send")
