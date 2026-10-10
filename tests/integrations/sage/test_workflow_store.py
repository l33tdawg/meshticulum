# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import pytest
pytest.importorskip("sage_sdk", reason="optional SAGE adapter requires compatible SDK")

"""Real SDK signing/parsing with synthetic HTTP only; no network or key files."""

import json
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest

from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient
from meshticulum.integrations.sage.message_contacts import RecipientID
from meshticulum.integrations.sage.message_storage import MessageStorageProbe
from meshticulum.integrations.sage.workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore, WorkflowUnavailable


BASE = "http://127.0.0.1:19100"
FIRST = "00000000-0000-4000-8000-000000000001"
SECOND = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def setup():
    identity = AgentIdentity(SigningKey(bytes(range(32))))
    actor = RecipientID("home", identity.agent_id)
    fixture = SimpleNamespace(calls=[], records={}, route_status=200, get_status=None, put_status=200,
                              page_override=None, change=None, put_override=None)
    fixture.storage = dict(schema="sage.message-storage.v1", instance_id="b" * 64, agent_id=actor.agent_id,
        encryption_expected=True, vault_active=True, vault_generation="1", stable=True,
        canonical_send_idempotency=True, encrypted_send_admission=True)

    def record(identifier=FIRST, revision=1, payload=None):
        return dict(schema="sage.workflow-journal.v1", agent_id=actor.agent_id, record_id=identifier,
                    revision=revision, kind="mesh_outbound", payload={} if payload is None else payload,
                    trust="untrusted_auxiliary")

    fixture.record = record

    def handler(request):
        fixture.calls.append((request.method, request.url.path, str(request.url.query)))
        assert request.headers["X-Agent-ID"] == actor.agent_id
        assert request.headers["X-Signature"]
        path = request.url.path
        if path == "/v1/messages/storage":
            return httpx.Response(200, json=fixture.storage)
        if path == "/v1/workflows":
            if fixture.route_status != 200:
                return httpx.Response(fixture.route_status, json={"detail": "synthetic missing route"})
            after = request.url.params.get("after", "")
            limit = int(request.url.params["limit"])
            items = [fixture.records[key] for key in sorted(fixture.records) if key > after]
            more = len(items) > limit
            items = items[:limit]
            page = dict(schema="sage.workflow-journal.v1", items=items,
                        next_after=items[-1]["record_id"] if more else None, has_more=more)
            if fixture.change:
                fixture.change()
            return httpx.Response(200, json=fixture.page_override if fixture.page_override is not None else page)
        identifier = path.rsplit("/", 1)[1]
        if request.method == "PUT":
            body = json.loads(request.content)
            fixture.last_put = body
            fixture.records[identifier] = record(identifier, body["expected_revision"] + 1, body["payload"])
            fixture.records[identifier]["kind"] = body["kind"]
            if fixture.change:
                fixture.change()
            if fixture.put_status == "timeout":
                raise httpx.ReadTimeout("SECRET persisted then timeout")
            return httpx.Response(fixture.put_status, json=fixture.put_override or fixture.records[identifier])
        if fixture.change:
            fixture.change()
        status = fixture.get_status or (200 if identifier in fixture.records else 404)
        return httpx.Response(status, json=fixture.records.get(identifier, {"detail": "synthetic missing"}))

    client = SageClient(BASE, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=BASE, timeout=2, trust_env=False, follow_redirects=False,
                                  transport=httpx.MockTransport(handler))
    fixture.client = client
    fixture.probe = MessageStorageProbe(client, actor=actor, endpoint=BASE)
    fixture.store = WorkflowStore(client, storage=fixture.probe)
    yield fixture
    client._client.close()


def put(setup, **changes):
    args = dict(record_id=FIRST, kind="mesh_outbound", expected_revision=0,
                payload={"ciphertext": "synthetic"}, access_check=lambda: True)
    args.update(changes)
    return setup.store.put(**args)


def test_real_sdk_put_get_list_scan(setup):
    result = put(setup)
    assert result["revision"] == 1 and result["trust"] == "untrusted_auxiliary"
    assert setup.store.get(FIRST, kind="mesh_outbound", access_check=lambda: True) == result
    put(setup, record_id=SECOND)
    page = setup.store.list(limit=1, access_check=lambda: True)
    assert page["has_more"] and page["next_after"] == FIRST
    assert [item["record_id"] for item in setup.store.scan(limit=1, access_check=lambda: True)] == [FIRST, SECOND]


def test_absent_requires_current_epoch_list_capability(setup):
    with pytest.raises(WorkflowNotFound):
        setup.store.get(FIRST, kind="mesh_outbound", access_check=lambda: True)
    assert any(path == "/v1/workflows" for _, path, _ in setup.calls)
    setup.route_status = 404
    with pytest.raises(WorkflowUnavailable) as error:
        setup.store.get(FIRST, kind="mesh_outbound", access_check=lambda: True)
    assert type(error.value) is WorkflowUnavailable


@pytest.mark.parametrize("status", [403, 404, 503])
def test_list_error_never_empty(setup, status):
    setup.route_status = status
    with pytest.raises(WorkflowUnavailable):
        setup.store.list(access_check=lambda: True)


@pytest.mark.parametrize("status", [403, 503])
def test_get_failure_never_notfound(setup, status):
    setup.get_status = status
    with pytest.raises(WorkflowUnavailable) as error:
        setup.store.get(FIRST, kind="mesh_outbound", access_check=lambda: True)
    assert type(error.value) is WorkflowUnavailable


@pytest.mark.parametrize("change", [{"vault_active": False}, {"stable": False}, {"agent_id": "f" * 64}])
def test_preflight_lock_or_actor_denies_put(setup, change):
    setup.storage.update(change)
    with pytest.raises(WorkflowUnavailable) as error:
        put(setup)
    assert type(error.value) is WorkflowUnavailable
    assert not any(method == "PUT" for method, _, _ in setup.calls)


@pytest.mark.parametrize("status", ["timeout", 409, 503, 404])
def test_uncertain_put_persisted_caller_reconciles_no_retry(setup, status):
    setup.put_status = status
    with pytest.raises(WorkflowOutcomeUnknown, match="^workflow_outcome_unknown$"):
        put(setup)
    assert sum(method == "PUT" for method, _, _ in setup.calls) == 1
    result = setup.store.get(FIRST, kind="mesh_outbound", access_check=lambda: True)
    assert result["revision"] == 1


@pytest.mark.parametrize("operation", ["get", "put", "list", "missing"])
def test_epoch_change_denies_result(setup, operation):
    if operation != "missing":
        setup.records[FIRST] = setup.record()
    setup.change = lambda: setup.storage.update(vault_generation="2")
    error = WorkflowOutcomeUnknown if operation == "put" else WorkflowUnavailable
    with pytest.raises(error):
        if operation == "put":
            put(setup)
        elif operation == "list":
            setup.store.list(access_check=lambda: True)
        else:
            setup.store.get(FIRST, kind="mesh_outbound", access_check=lambda: True)


@pytest.mark.parametrize("field,value", [("agent_id", "e" * 64), ("record_id", SECOND),
    ("revision", True), ("revision", 2), ("kind", "mesh_inbound"), ("payload", {"changed": True}),
    ("trust", "trusted"), ("extra", 1)])
def test_put_response_mismatch_unknown(setup, field, value):
    setup.put_override = setup.record(payload={"ciphertext": "synthetic"})
    setup.put_override[field] = value
    with pytest.raises(WorkflowOutcomeUnknown):
        put(setup)


@pytest.mark.parametrize("bad", ["reverse", "duplicate", "badcursor", "terminalcursor", "empty_more", "crossactor"])
def test_bad_sorted_pagination(setup, bad):
    items = [setup.record(), setup.record(SECOND)]
    page = dict(schema="sage.workflow-journal.v1", items=items, next_after=None, has_more=False)
    if bad == "reverse": page["items"].reverse()
    if bad == "duplicate": page["items"] = [items[0], items[0]]
    if bad == "badcursor": page.update(has_more=True, next_after=FIRST)
    if bad == "terminalcursor": page["next_after"] = SECOND
    if bad == "empty_more": page.update(items=[], has_more=True, next_after=FIRST)
    if bad == "crossactor": items[0]["agent_id"] = "f" * 64
    setup.page_override = page
    with pytest.raises(WorkflowUnavailable):
        setup.store.list(access_check=lambda: True)


def test_cursor_lower_bound_and_scan_record_cap(setup):
    setup.records = {FIRST: setup.record(), SECOND: setup.record(SECOND)}
    with pytest.raises(WorkflowUnavailable):
        setup.store.scan(limit=1, max_records=1, access_check=lambda: True)
    setup.page_override = dict(schema="sage.workflow-journal.v1", items=[setup.record()], next_after=None, has_more=False)
    with pytest.raises(WorkflowUnavailable):
        setup.store.list(after=FIRST, access_check=lambda: True)


def test_guard_revoked_after_put_unknown_and_read_sanitized(setup):
    allowed = True
    def revoke():
        nonlocal allowed
        allowed = False
    setup.change = revoke
    with pytest.raises(WorkflowOutcomeUnknown):
        put(setup, access_check=lambda: allowed)
    with pytest.raises(WorkflowUnavailable):
        setup.store.list(access_check=lambda: allowed)


def test_local_payload_bounds_and_same_client_binding(setup):
    for payload in ({"bad": float("nan")}, {1: "bad"}, "x" * 16385):
        with pytest.raises(WorkflowUnavailable):
            put(setup, payload=payload)
    assert not setup.calls
    with pytest.raises(WorkflowUnavailable):
        WorkflowStore(object(), storage=setup.probe)


def test_scan_epoch_changes_between_pages_not_snapshot(setup):
    setup.records = {FIRST: setup.record(), SECOND: setup.record(SECOND)}
    original_list = setup.store._list
    calls = 0

    def advancing(*args, **kwargs):
        nonlocal calls
        result = original_list(*args, **kwargs)
        calls += 1
        if calls == 1:
            setup.storage["instance_id"] = "c" * 64
        return result

    setup.store._list = advancing
    with pytest.raises(WorkflowUnavailable):
        setup.store.scan(limit=1, access_check=lambda: True)
    assert calls == 1


@pytest.mark.parametrize("field,value", [("agent_id", "f" * 64), ("kind", "public_proposal"),
                                      ("revision", False), ("record_id", SECOND)])
def test_exact_get_actor_id_kind_revision(setup, field, value):
    setup.records[FIRST] = setup.record()
    setup.records[FIRST][field] = value
    with pytest.raises(WorkflowUnavailable):
        setup.store.get(FIRST, kind="mesh_outbound", access_check=lambda: True)


@pytest.mark.parametrize("options", [dict(limit=0), dict(limit=51), dict(limit=True),
                                   dict(after="bad"), dict(max_records=4097), dict(max_records=True)])
def test_invalid_scan_bounds_before_io(setup, options):
    with pytest.raises(WorkflowUnavailable):
        setup.store.scan(access_check=lambda: True, **options)
    assert not setup.calls


def test_conversation_guard_forwarded_without_mutation(setup):
    guard = dict(record_id=SECOND, expected_revision=7)
    result = setup.store.put(FIRST, "conversation_session", 0, {"text": "synthetic"},
                             guard=guard, access_check=lambda: True)
    assert setup.last_put["guard"] == guard
    assert result["kind"] == "conversation_session"
    guard["expected_revision"] = 8
    assert setup.last_put["guard"]["expected_revision"] == 7


def test_conversation_control_omits_guard(setup):
    setup.store.put(FIRST, "conversation_control", 0, {"consent": False}, access_check=lambda: True)
    assert "guard" not in setup.last_put


@pytest.mark.parametrize("guard", [None, {}, [], {"record_id": SECOND, "expected_revision": True},
    {"record_id": SECOND, "expected_revision": 0}, {"record_id": SECOND, "expected_revision": 2 ** 53},
    {"record_id": FIRST, "expected_revision": 1}, {"record_id": "bad", "expected_revision": 1},
    {"record_id": SECOND, "expected_revision": 1, "extra": True}])
def test_invalid_conversation_guard_denied_before_io(setup, guard):
    with pytest.raises(WorkflowUnavailable):
        setup.store.put(FIRST, "conversation_session", 0, {}, guard=guard, access_check=lambda: True)
    assert not setup.calls


@pytest.mark.parametrize("kind", ["mesh_outbound", "mesh_inbound", "public_proposal", "conversation_control"])
def test_guard_restricted_to_conversation_session(setup, kind):
    with pytest.raises(WorkflowUnavailable):
        setup.store.put(FIRST, kind, 0, {}, guard=dict(record_id=SECOND, expected_revision=1),
                        access_check=lambda: True)
    assert not setup.calls
