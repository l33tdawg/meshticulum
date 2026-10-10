"""Protected SAGE auxiliary journal adapter, never canonical receipt authority.

No key/disk/network configuration or retry policy. Uses the exact SDK client
bound by MessageStorageProbe. Every call needs a generation-bound access guard.
PUT uncertainty must be persisted/reconciled by the caller using the original
UUID and revision. Sequential scan is bounded, not an atomic snapshot; fence
producers before treating it as complete workflow inventory.
"""

import json
import math
import uuid

from sage_sdk.exceptions import SageNotFoundError

from .message_storage import MessageStorageProbe


SCHEMA = "sage.workflow-journal.v1"
KINDS = frozenset(("mesh_outbound", "mesh_inbound", "public_proposal",
                   "conversation_control", "conversation_session", "contact_issuance", "correction_attempt"))
_GUARD_UNSET = object()
MAX_REVISION = 2 ** 53 - 1
MAX_PAYLOAD = 16384
MAX_RECORDS = 4096


class WorkflowUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("workflow_unavailable")


class WorkflowNotFound(WorkflowUnavailable):
    def __init__(self):
        RuntimeError.__init__(self, "workflow_not_found")


class WorkflowOutcomeUnknown(WorkflowUnavailable):
    def __init__(self):
        RuntimeError.__init__(self, "workflow_outcome_unknown")


def _identifier(value):
    try:
        if type(value) is not str or len(value) != 36:
            raise ValueError
        parsed = uuid.UUID(value)
        if parsed.int == 0 or str(parsed) != value:
            raise ValueError
        return value
    except Exception:
        raise WorkflowUnavailable() from None


def _payload(value):
    visited = 0

    def visit(item, depth):
        nonlocal visited
        visited += 1
        if visited > MAX_PAYLOAD or depth > 64:
            raise WorkflowUnavailable()
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise WorkflowUnavailable()
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            if len(item.encode("utf-8")) > MAX_PAYLOAD:
                raise WorkflowUnavailable()
        elif item is None or type(item) in (bool, int):
            pass
        elif type(item) is not float or not math.isfinite(item):
            raise WorkflowUnavailable()

    try:
        visit(value, 0)
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded) > MAX_PAYLOAD:
            raise WorkflowUnavailable()
        return json.loads(encoded), encoded
    except Exception:
        raise WorkflowUnavailable() from None


def _mapping(value):
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if type(value) is not dict:
        raise WorkflowUnavailable()
    return value


class WorkflowStore:
    def __init__(self, client, *, storage):
        if type(storage) is not MessageStorageProbe or storage.client is not client:
            raise WorkflowUnavailable()
        self.client = client
        self.storage = storage
        self._actor = storage.actor
        self._endpoint = storage.endpoint

    def _access(self, check):
        if (not callable(check) or check() is not True or self.storage.client is not self.client
                or self.storage.actor != self._actor or self.storage.endpoint != self._endpoint):
            raise WorkflowUnavailable()

    def _snapshot(self, check):
        self._access(check)
        snapshot = self.storage.snapshot()
        self._access(check)
        return snapshot.epoch

    def _invoke(self, method, arguments, check, *, writing=False, missing=False, epoch=None):
        dispatched = False
        try:
            before = self._snapshot(check)
            if epoch is not None and before != epoch:
                raise WorkflowUnavailable()
            self._access(check)
            dispatched = True
            error = None
            try:
                value = getattr(self.client, method)(**arguments)
            except Exception as caught:
                error = caught
                value = None
            after = self._snapshot(check)
            if before != after:
                raise WorkflowUnavailable()
            if error is not None:
                if missing and isinstance(error, SageNotFoundError) and error.status_code == 404:
                    return None, before
                raise WorkflowUnavailable()
            return value, before
        except Exception:
            if writing and dispatched:
                raise WorkflowOutcomeUnknown() from None
            raise WorkflowUnavailable() from None

    def _record(self, value, *, record_id=None, kind=None):
        value = _mapping(value)
        if (set(value) != {"schema", "agent_id", "record_id", "revision", "kind", "payload", "trust"}
                or value["schema"] != SCHEMA or value["agent_id"] != self._actor.agent_id
                or value["trust"] != "untrusted_auxiliary" or type(value["kind"]) is not str
                or value["kind"] not in KINDS or type(value["revision"]) is not int
                or not 1 <= value["revision"] <= MAX_REVISION
                or (record_id is not None and value["record_id"] != record_id)
                or (kind is not None and value["kind"] != kind)):
            raise WorkflowUnavailable()
        _identifier(value["record_id"])
        payload, _ = _payload(value["payload"])
        return {**value, "payload": payload}

    def _page(self, value, after, limit):
        value = _mapping(value)
        if (set(value) != {"schema", "items", "next_after", "has_more"} or value["schema"] != SCHEMA
                or type(value["items"]) is not list or len(value["items"]) > limit
                or type(value["has_more"]) is not bool):
            raise WorkflowUnavailable()
        items = [self._record(item) for item in value["items"]]
        ids = [item["record_id"] for item in items]
        if (ids != sorted(set(ids)) or (after is not None and any(identifier <= after for identifier in ids))
                or (value["has_more"] and (not ids or value["next_after"] != ids[-1]))
                or (not value["has_more"] and value["next_after"] is not None)):
            raise WorkflowUnavailable()
        return {**value, "items": items}

    def get(self, record_id, *, kind, access_check):
        try:
            _identifier(record_id)
            if type(kind) is not str or kind not in KINDS:
                raise WorkflowUnavailable()
            value, epoch = self._invoke("workflow_get", {"record_id": record_id}, access_check, missing=True)
            if value is None:
                capability, _ = self._invoke("workflow_list", {"limit": 1}, access_check, epoch=epoch)
                self._page(capability, None, 1)
                self._access(access_check)
                raise WorkflowNotFound()
            result = self._record(value, record_id=record_id, kind=kind)
            self._access(access_check)
            return result
        except WorkflowNotFound:
            raise
        except Exception:
            raise WorkflowUnavailable() from None

    def put(self, record_id, kind, expected_revision, payload, *, access_check, guard=_GUARD_UNSET):
        returned = False
        try:
            _identifier(record_id)
            if (type(kind) is not str or kind not in KINDS or type(expected_revision) is not int
                    or not 0 <= expected_revision < MAX_REVISION):
                raise WorkflowUnavailable()
            detached, canonical = _payload(payload)
            arguments = dict(record_id=record_id, kind=kind,
                             expected_revision=expected_revision, payload=detached)
            if guard is not _GUARD_UNSET:
                if (kind != "conversation_session" or type(guard) is not dict
                        or set(guard) != {"record_id", "expected_revision"}):
                    raise WorkflowUnavailable()
                control_id = _identifier(guard["record_id"])
                revision = guard["expected_revision"]
                if (control_id == record_id or type(revision) is not int
                        or not 1 <= revision <= MAX_REVISION):
                    raise WorkflowUnavailable()
                arguments["guard"] = dict(record_id=control_id, expected_revision=revision)
            value, _ = self._invoke("workflow_put", arguments, access_check, writing=True)
            returned = True
            result = self._record(value, record_id=record_id, kind=kind)
            if result["revision"] != expected_revision + 1 or _payload(result["payload"])[1] != canonical:
                raise WorkflowOutcomeUnknown()
            self._access(access_check)
            return result
        except WorkflowOutcomeUnknown:
            raise
        except Exception:
            if returned:
                raise WorkflowOutcomeUnknown() from None
            raise WorkflowUnavailable() from None

    def _list(self, after, limit, access_check, epoch=None):
        if after is not None:
            _identifier(after)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise WorkflowUnavailable()
        value, epoch = self._invoke("workflow_list", dict(after=after, limit=limit), access_check, epoch=epoch)
        result = self._page(value, after, limit)
        self._access(access_check)
        return result, epoch

    def list(self, *, after=None, limit=20, access_check):
        try:
            return self._list(after, limit, access_check)[0]
        except Exception:
            raise WorkflowUnavailable() from None

    def scan(self, *, after=None, limit=20, max_records=MAX_RECORDS, access_check):
        """Return bounded sequential inventory; caller must fence concurrent writers."""
        try:
            if type(max_records) is not int or not 1 <= max_records <= MAX_RECORDS:
                raise WorkflowUnavailable()
            records = []
            epoch = None
            for _ in range(MAX_RECORDS):
                page, epoch = self._list(after, limit, access_check, epoch)
                records.extend(page["items"])
                if len(records) > max_records:
                    raise WorkflowUnavailable()
                if not page["has_more"]:
                    self._access(access_check)
                    return records
                if len(records) >= max_records:
                    raise WorkflowUnavailable()
                after = page["next_after"]
            raise WorkflowUnavailable()
        except Exception:
            raise WorkflowUnavailable() from None
