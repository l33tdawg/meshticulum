"""Single-owner durable firmware dequeue slot; no decoding of application content.

The root dispatcher owns storage_check: it remains valid through an admitted
capture, and is revoked only after this producer joins, before vault closure.
It must not be a manually toggled user flag. access_check is the separate live
user generation gate. Callbacks and the borrowed driver/store must be bounded.

One worker exclusively owns the fixed actor-scoped slot, including across
restarts. CAS detects conflicting writers but cannot undo a firmware dequeue.
Never replace an unresolved slot with a fresh UUID. consume(operation_id,
IncomingMessage) must durably deduplicate by operation_id before returning True.
An uncertain handler can be called again on explicit process_pending, not here
automatically. There are no delivery/read acknowledgements at this layer.

close stops admission and waits only for the current method; it does no I/O.
True certifies a joined producer and last verified idle/captured durable state
under the exclusive-owner contract, not consumer completion or vault closure.
False requires containment; it does not authorize closing the vault while a
callback remains in flight. A new, unread instance cannot certify clean drain.
"""

import base64
import math
import threading
import uuid

from .meshcore_transport import IncomingMessage, decode_incoming
from .workflow_store import WorkflowNotFound, WorkflowOutcomeUnknown, WorkflowStore


SCHEMA = "sage.lantern.mesh-receiver.v1"


class ReceiverError(RuntimeError):
    def __init__(self, code="receive_unavailable"):
        self.code = code
        super().__init__(code)


def _uuid(value):
    if type(value) is not str or len(value) != 36:
        raise ReceiverError()
    parsed = uuid.UUID(value)
    if not parsed.int or str(parsed) != value:
        raise ReceiverError()
    return value


def _payload(state, operation_id=None, raw_frame=None):
    return dict(schema=SCHEMA, state=state, operation_id=operation_id, raw_frame=raw_frame)


def _validate(payload):
    if type(payload) is not dict or set(payload) != {"schema", "state", "operation_id", "raw_frame"}:
        raise ReceiverError()
    if payload["schema"] != SCHEMA:
        raise ReceiverError()
    state = payload["state"]
    if state == "idle":
        if payload != _payload("idle"):
            raise ReceiverError()
    elif state in ("receiving", "captured", "interrupted"):
        _uuid(payload["operation_id"])
        if state in ("receiving", "interrupted"):
            if payload["raw_frame"] is not None:
                raise ReceiverError()
        else:
            encoded = payload["raw_frame"]
            if type(encoded) is not str or not 1 <= len(encoded) <= 236:
                raise ReceiverError()
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > 176 or base64.b64encode(raw).decode("ascii") != encoded:
                raise ReceiverError()
            return decode_incoming(raw)
    else:
        raise ReceiverError()


class MeshReceiver:
    def __init__(self, store, driver, *, slot_id, access_check, storage_check):
        try:
            _uuid(slot_id)
            if type(store) is not WorkflowStore or not callable(driver.receive_next):
                raise ReceiverError()
            if not callable(access_check) or not callable(storage_check):
                raise ReceiverError()
        except Exception:
            raise ReceiverError("invalid_receive_configuration") from None
        self._store, self._driver, self._slot = store, driver, slot_id
        self._access, self._storage = access_check, storage_check
        self._condition = threading.Condition()
        self._busy = None
        self._closing = False
        self._known = None
        self._uncertain = False

    def _live(self):
        with self._condition:
            if self._closing:
                return False
        allowed = self._access() is True
        with self._condition:
            return allowed and not self._closing and not self._uncertain

    def _run(self, action):
        with self._condition:
            if self._closing or self._busy is not None:
                raise ReceiverError("receive_closed_or_busy")
            self._busy = threading.get_ident()
        try:
            return action()
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise ReceiverError() from None
        except Exception:
            raise ReceiverError() from None
        finally:
            with self._condition:
                self._busy = None
                self._condition.notify_all()

    def _read(self):
        self._known = None
        try:
            record = self._store.get(self._slot, kind="mesh_inbound", access_check=self._storage)
        except WorkflowNotFound:
            return self._write(0, _payload("idle"))
        _validate(record["payload"])
        self._known = record["payload"]["state"]
        return record

    def _write(self, revision, payload):
        self._known = None
        try:
            record = self._store.put(self._slot, "mesh_inbound", revision, payload,
                                     access_check=self._storage)
        except WorkflowOutcomeUnknown:
            self._uncertain = True
            raise
        _validate(record["payload"])
        self._known = record["payload"]["state"]
        return record

    def state(self):
        """Read/initialize the fixed slot; return only its state, never raw data."""
        return self._run(lambda: self._read()["payload"]["state"])

    def receive_next(self):
        """Return captured or idle; never expose frame bytes to this caller."""
        def receive():
            if not self._live():
                raise ReceiverError()
            record = self._read()
            if record["payload"]["state"] != "idle":
                raise ReceiverError()
            operation_id = str(uuid.uuid4())
            receiving = self._write(record["revision"], _payload("receiving", operation_id))
            captured = []

            def persist(message):
                if captured or type(message) is not IncomingMessage:
                    raise ReceiverError()
                if type(message.raw_frame) is not bytes or decode_incoming(message.raw_frame) != message:
                    raise ReceiverError()
                encoded = base64.b64encode(message.raw_frame).decode("ascii")
                self._write(receiving["revision"], _payload("captured", operation_id, encoded))
                captured.append(message)
                return True

            message = self._driver.receive_next(authorize=self._live, persist=persist)
            if message is None and not captured:
                self._write(receiving["revision"], _payload("idle"))
            elif not captured or message != captured[0]:
                raise ReceiverError()
            if not self._live():
                raise ReceiverError()
            return "captured" if captured else "idle"
        return self._run(receive)

    def process_pending(self, consume):
        """True only after durable handler acknowledgement and successful clear CAS."""
        def process():
            record = self._read()
            payload = record["payload"]
            if payload["state"] == "idle":
                return False
            if payload["state"] != "captured" or not callable(consume) or not self._live():
                raise ReceiverError()
            message = _validate(payload)
            if consume(payload["operation_id"], message) is not True:
                return False
            self._write(record["revision"], _payload("idle"))
            return True
        return self._run(process)

    def close(self, timeout=5.0):
        """Permanently stop admission; bounded join, no borrowed-resource closure."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 60:
            raise ReceiverError("invalid_close_timeout")
        with self._condition:
            self._closing = True
            if self._busy == threading.get_ident():
                return False
            joined = self._condition.wait_for(lambda: self._busy is None, timeout)
            return joined and not self._uncertain and self._known in ("idle", "captured")
