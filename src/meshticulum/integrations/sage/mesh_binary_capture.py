# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""One protected, recipient-signed complete-private capture slot.

The native owner supplies the exclusive lifecycle lease, current access/storage
guards and independently pinned sender/consent verifier. This adapter grants no
permission itself and opens no device, key, endpoint or listener. One producer
owns its fixed actor slot across processes; CAS conflicts fence admission.

Receiving intentions are persisted before flow dispatch. Completed sealed bytes
and the original UUID/deadline survive an uncertain inbox write. Explicit recovery
first probes existing recipient-signed admission, then admits only within the
original deadline. Rejection/processed state is durable before clearing capture.
Nothing here creates delivery/read receipts, resets RF counters, renews grants,
reuses an unresolved receiving intention or closes a borrowed protected store.
"""
import base64
from contextlib import AbstractContextManager
import hashlib
import math
import threading
import time

from nacl.signing import SigningKey

from meshticulum.privacy.mesh_binary_envelope import MAX_BINARY_BYTES, to_binary, to_text
from meshticulum.privacy.mesh_envelope import Peer
from .mesh_inbox import IngestResult, MeshInbox, _canonical, _id, _peer, _uuid
from .workflow_store import WorkflowNotFound, WorkflowStore


SCHEMA = 'sage.lantern.mesh-binary-capture.v1'
DOMAIN = b'sage.lantern.mesh-binary-capture.v1/signature\x00'
MAX_TIME_US = 2 ** 53 - 1
REASONS = frozenset(('invalid_envelope', 'equivocation', 'application_expired', 'original_boot_changed'))


class CaptureError(RuntimeError):
    def __init__(self):
        super().__init__('private_capture_unavailable')


def _require(value):
    if not value:
        raise CaptureError()


def _digest(value):
    _require(type(value) is str and len(value) == 64
             and all(c in '0123456789abcdef' for c in value))
    return value


def _decision(value):
    fields = {'status', 'record_id', 'remote_id', 'envelope_kind', 'reason'}
    _require(type(value) is dict and set(value) == fields)
    if value['status'] in ('admitted', 'duplicate'):
        _uuid(value['record_id']); _uuid(value['remote_id'])
        _require(value['envelope_kind'] in ('message', 'delivery', 'read') and value['reason'] is None)
    else:
        _require(value['status'] == 'rejected' and value['reason'] in REASONS
                 and all(value[k] is None for k in ('record_id', 'remote_id', 'envelope_kind')))
    return value


class _Lease(AbstractContextManager):
    """A trusted native lease cannot suppress a failed write or guard check."""
    def __init__(self, lease):
        _require(isinstance(lease, AbstractContextManager))
        self.lease = lease

    def __enter__(self):
        return self.lease.__enter__()

    def __exit__(self, kind, value, traceback):
        outcome = self.lease.__exit__(kind, value, traceback)
        _require(outcome is None or outcome is False)
        return False


class BinaryCapture:
    def __init__(self, store, *, recipient, recipient_signing_key, owner_lease,
                 access_check, storage_check, peer_check, boot_id, clock=time.time,
                 monotonic_clock=time.monotonic):
        _uuid(boot_id)
        _require(type(store) is WorkflowStore and type(recipient) is Peer
                 and isinstance(recipient_signing_key, SigningKey)
                 and bytes(recipient_signing_key.verify_key) == recipient.public_key
                 and store.storage.actor.chain_id == recipient.chain_id
                 and store.storage.actor.agent_id == recipient.public_key.hex()
                 and all(callable(c) for c in (owner_lease, access_check, storage_check, peer_check, clock,
                                               monotonic_clock)))
        self.store, self.recipient, self.key = store, recipient, recipient_signing_key
        self.slot_id = _id(SCHEMA, [recipient.to_dict(), 'complete-private'])
        self._lease, self._access, self._storage, self._peer, self._clock = (
            owner_lease, access_check, storage_check, peer_check, clock)
        self.boot_id, self._monotonic_clock = boot_id, monotonic_clock
        self._condition = threading.Condition()
        self._busy, self._closing, self._uncertain, self._known = None, False, False, None

    def _time(self):
        value = self._clock()
        _require(type(value) in (int, float) and math.isfinite(value)
                 and 0 <= value <= MAX_TIME_US / 1000000)
        return int(value * 1000000)

    def _monotonic(self):
        value = self._monotonic_clock()
        _require(type(value) in (int, float) and math.isfinite(value)
                 and 0 <= value <= MAX_TIME_US / 1000000)
        return int(value * 1000000)

    def _guard(self, sender=None):
        _require(not self._closing and not self._uncertain
                 and self._access() is True and self._storage() is True)
        if sender is not None:
            _require(self._peer(sender) is True)

    def _run(self, action):
        with self._condition:
            _require(not self._closing and not self._uncertain and self._busy is None)
            self._busy = threading.get_ident()
        entered = False
        try:
            with _Lease(self._lease()):
                self._guard()
                epoch = self.store.storage.snapshot().epoch
                entered = True
                value = action()
                self._guard()
                _require(self.store.storage.snapshot().epoch == epoch)
            return value
        except BaseException as error:
            # After admission into the native lease, an escaping error can
            # follow a dispatched write/handler call or a failed lease exit.
            # Reconstruct and explicitly reconcile; never retry in this owner.
            if entered:
                self._uncertain = True
            if not isinstance(error, Exception):
                raise
            raise CaptureError() from None
        finally:
            with self._condition:
                self._busy = None
                self._condition.notify_all()

    def _validate(self, record):
        value = record['payload']
        _require(type(value) is dict and set(value) == {'schema', 'body', 'signature'}
                 and value['schema'] == SCHEMA and type(value['signature']) is str
                 and len(value['signature']) == 88)
        signature = base64.b64decode(value['signature'], validate=True)
        _require(base64.b64encode(signature).decode('ascii') == value['signature'])
        body = value['body']
        self.key.verify_key.verify(DOMAIN + _canonical(body), signature)
        _require(type(body) is dict and set(body) == {'slot_id', 'revision', 'recipient',
                 'state', 'last_epoch_us', 'last_monotonic_us', 'last_writer_boot_id',
                 'capture', 'decision', 'last_completed'}
                 and record['record_id'] == body['slot_id'] == self.slot_id
                 and body['revision'] == record['revision'] and type(body['revision']) is int
                 and body['recipient'] == self.recipient.to_dict()
                 and type(body['last_epoch_us']) is int
                 and 0 <= body['last_epoch_us'] <= self._time()
                 and type(body['last_monotonic_us']) is int
                 and 0 <= body['last_monotonic_us'] <= MAX_TIME_US)
        _uuid(body['last_writer_boot_id'])
        if body['last_writer_boot_id'] == self.boot_id:
            _require(body['last_monotonic_us'] <= self._monotonic())
        last = body['last_completed']
        if last is not None:
            _require(type(last) is dict and set(last) == {'operation_id', 'packet_sha256', 'decision', 'acknowledged',
                     'sender', 'observed_at_us', 'wire'}
                     and type(last['acknowledged']) is bool)
            _uuid(last['operation_id']); _digest(last['packet_sha256']); _decision(last['decision'])
            _peer(last['sender'])
            _require(type(last['observed_at_us']) is int and 0 <= last['observed_at_us'] <= body['last_epoch_us'])
            if last['acknowledged']:
                _require(last['wire'] is None)
            else:
                _require(hashlib.sha256(to_binary(last['wire'])).hexdigest() == last['packet_sha256'])
        capture = body['capture']
        if body['state'] == 'idle':
            _require(capture is None and body['decision'] is None)
        else:
            _require(body['state'] in ('receiving', 'captured', 'processed')
                     and type(capture) is dict and set(capture) == {'operation_id', 'sender',
                     'packet_sha256', 'packet_bytes', 'application_expires_epoch_us',
                     'application_deadline_monotonic_us', 'host_boot_id',
                     'authority_digest', 'prepared_at_us', 'captured_at_us', 'wire'})
            _uuid(capture['operation_id']); _peer(capture['sender'])
            _digest(capture['packet_sha256']); _digest(capture['authority_digest'])
            _uuid(capture['host_boot_id'])
            _require(type(capture['packet_bytes']) is int and 51 < capture['packet_bytes'] <= MAX_BINARY_BYTES
                     and type(capture['application_expires_epoch_us']) is int
                     and type(capture['prepared_at_us']) is int
                     and type(capture['application_deadline_monotonic_us']) is int
                     and 0 < capture['application_deadline_monotonic_us'] <= MAX_TIME_US
                     and 0 <= capture['prepared_at_us'] < capture['application_expires_epoch_us'] <= MAX_TIME_US
                     and capture['prepared_at_us'] <= body['last_epoch_us'])
            if body['state'] == 'receiving':
                _require(capture['wire'] is None and capture['captured_at_us'] is None
                         and body['decision'] is None)
            else:
                _require(type(capture['captured_at_us']) is int
                         and capture['prepared_at_us'] <= capture['captured_at_us']
                         < capture['application_expires_epoch_us']
                         and capture['captured_at_us'] <= body['last_epoch_us'])
                packet = to_binary(capture['wire'])
                _require(len(packet) == capture['packet_bytes']
                         and hashlib.sha256(packet).hexdigest() == capture['packet_sha256'])
                if body['state'] == 'captured':
                    _require(body['decision'] is None)
                else:
                    _decision(body['decision'])
        return body

    def _read(self):
        self._known = None
        try:
            record = self.store.get(self.slot_id, kind='mesh_inbound', access_check=self._storage)
        except WorkflowNotFound:
            return self._write(0, dict(slot_id=self.slot_id, revision=0, recipient=self.recipient.to_dict(),
                state='idle', last_epoch_us=0, last_monotonic_us=0, last_writer_boot_id=self.boot_id,
                capture=None, decision=None, last_completed=None))
        body = self._validate(record)
        self._known = body['state']
        return record

    def _write(self, revision, body):
        self._known = None
        self._guard()
        now = self._time()
        _require(now >= body['last_epoch_us'])
        mono = self._monotonic()
        if body['last_writer_boot_id'] == self.boot_id:
            _require(mono >= body['last_monotonic_us'])
        body = {**body, 'revision': revision + 1, 'last_epoch_us': now,
                'last_monotonic_us': mono, 'last_writer_boot_id': self.boot_id}
        signature = self.key.sign(DOMAIN + _canonical(body)).signature
        record = self.store.put(self.slot_id, 'mesh_inbound', revision,
            dict(schema=SCHEMA, body=body, signature=base64.b64encode(signature).decode('ascii')),
            access_check=self._storage)
        checked = self._validate(record)
        self._known = checked['state']
        return record

    def state(self):
        """Signed-state metadata only. An unread instance cannot certify drain."""
        return self._run(lambda: self._read()['payload']['body']['state'])

    def prepare(self, operation_id, *, sender, packet_sha256, packet_bytes,
                application_expires_epoch_us, application_deadline_monotonic_us,
                host_boot_id, authority_digest, permission_check):
        """Persist original receive intention before DATA/ACK flow dispatch."""
        def prepare():
            self._guard(sender)
            _require(callable(permission_check) and permission_check() is True)
            _uuid(operation_id); _digest(packet_sha256); _digest(authority_digest)
            _uuid(host_boot_id)
            _require(type(sender) is Peer and type(packet_bytes) is int
                     and 51 < packet_bytes <= MAX_BINARY_BYTES
                     and type(application_expires_epoch_us) is int
                     and self._time() < application_expires_epoch_us <= MAX_TIME_US
                     and type(application_deadline_monotonic_us) is int
                     and self._monotonic() < application_deadline_monotonic_us <= MAX_TIME_US
                     and host_boot_id == self.boot_id)
            record = self._read()
            body = record['payload']['body']
            _require(body['state'] == 'idle')
            if body['last_completed'] is not None:
                _require(body['last_completed']['acknowledged'] is True
                         and body['last_completed']['operation_id'] != operation_id)
            capture = dict(operation_id=operation_id, sender=sender.to_dict(), packet_sha256=packet_sha256,
                packet_bytes=packet_bytes, application_expires_epoch_us=application_expires_epoch_us,
                application_deadline_monotonic_us=application_deadline_monotonic_us, host_boot_id=host_boot_id,
                authority_digest=authority_digest, prepared_at_us=self._time(), captured_at_us=None, wire=None)
            self._write(record['revision'], {**body, 'state': 'receiving', 'capture': capture})
            self._guard(sender)
            _require(permission_check() is True)
            return 'receiving'
        return self._run(prepare)

    def complete(self, operation_id, packet, *, authority_digest, permission_check):
        """Persist bounded sealed bytes; never expose or decrypt message text."""
        def complete():
            _require(callable(permission_check) and permission_check() is True)
            _uuid(operation_id); _digest(authority_digest)
            wire = to_text(packet)
            record = self._read()
            body = record['payload']['body']
            _require(body['state'] in ('receiving', 'captured'))
            capture = body['capture']
            self._guard(_peer(capture['sender']))
            _require(capture['operation_id'] == operation_id and capture['authority_digest'] == authority_digest
                     and len(packet) == capture['packet_bytes']
                     and hashlib.sha256(packet).hexdigest() == capture['packet_sha256']
                     and self._time() < capture['application_expires_epoch_us']
                     and self._monotonic() < capture['application_deadline_monotonic_us']
                     and self.boot_id == capture['host_boot_id'])
            if body['state'] == 'receiving':
                capture = {**capture, 'wire': wire, 'captured_at_us': self._time()}
                self._write(record['revision'], {**body, 'state': 'captured', 'capture': capture})
            self._guard(_peer(capture['sender']))
            _require(permission_check() is True)
            return 'captured'
        return self._run(complete)

    def process_pending(self, inbox, *, operation_id):
        """Explicit reconciliation; durable disposition precedes capture clear.

        Historical lookup is read-only and never grants fresh admission. An
        expired original application can settle an existing signed admission
        or record a rejection, but cannot create a new inbox record.
        """
        def process():
            _uuid(operation_id)
            _require(type(inbox) is MeshInbox and inbox._store is self.store
                     and inbox._recipient == self.recipient)
            record = self._read()
            body = record['payload']['body']
            if body['state'] == 'idle':
                last = body['last_completed']
                if last is not None and last['operation_id'] == operation_id:
                    self._guard(_peer(last['sender']))
                    if not last['acknowledged'] and last['decision']['status'] in ('admitted', 'duplicate'):
                        verified = inbox.reconcile_binary(to_binary(last['wire']), sender=_peer(last['sender']),
                            observed_at=last['observed_at_us'] // 1000000,
                            peer_check=lambda peer: (not self._closing and self._access() is True
                                and self._storage() is True and self._peer(peer) is True))
                        _require(type(verified) is IngestResult and verified.status == 'duplicate'
                                 and verified.record_id == last['decision']['record_id']
                                 and verified.remote_id == last['decision']['remote_id']
                                 and verified.envelope_kind == last['decision']['envelope_kind'])
                    return IngestResult(**_decision(last['decision']))
                return None
            _require(body['state'] in ('captured', 'processed'))
            capture = body['capture']
            _require(capture['operation_id'] == operation_id)
            sender = _peer(capture['sender'])
            self._guard(sender)
            if body['state'] == 'captured':
                packet = to_binary(capture['wire'])
                peer_check = lambda peer: (not self._closing and self._access() is True
                    and self._storage() is True and self._peer(peer) is True)
                decision = inbox.reconcile_binary(packet, sender=sender,
                    observed_at=capture['captured_at_us'] // 1000000, peer_check=peer_check)
                if decision is None:
                    if self.boot_id != capture['host_boot_id']:
                        decision = IngestResult('rejected', reason='original_boot_changed')
                    elif (self._time() >= capture['application_expires_epoch_us']
                            or self._monotonic() >= capture['application_deadline_monotonic_us']):
                        decision = IngestResult('rejected', reason='application_expired')
                    else:
                        decision = inbox.ingest_binary(capture['operation_id'], packet,
                            sender=sender, peer_check=lambda peer: peer_check(peer)
                                and self._time() < capture['application_expires_epoch_us']
                                and self._monotonic() < capture['application_deadline_monotonic_us']
                                and self.boot_id == capture['host_boot_id'])
                _require(type(decision) is IngestResult)
                value = _decision(dict(status=decision.status, record_id=decision.record_id,
                    remote_id=decision.remote_id, envelope_kind=decision.envelope_kind, reason=decision.reason))
                self._guard(sender)
                record = self._write(record['revision'], {**body, 'state': 'processed', 'decision': value})
                body = record['payload']['body']
            decision = _decision(body['decision'])
            if decision['status'] in ('admitted', 'duplicate'):
                verified = inbox.reconcile_binary(to_binary(capture['wire']), sender=sender,
                    observed_at=capture['captured_at_us'] // 1000000,
                    peer_check=lambda peer: (not self._closing and self._access() is True
                        and self._storage() is True and self._peer(peer) is True))
                _require(type(verified) is IngestResult and verified.status == 'duplicate'
                         and verified.record_id == decision['record_id']
                         and verified.remote_id == decision['remote_id']
                         and verified.envelope_kind == decision['envelope_kind'])
            self._guard(sender)
            self._write(record['revision'], {**body, 'state': 'idle', 'capture': None, 'decision': None,
                'last_completed': dict(operation_id=capture['operation_id'],
                    packet_sha256=capture['packet_sha256'], decision=decision, acknowledged=False,
                    sender=capture['sender'], observed_at_us=capture['captured_at_us'], wire=capture['wire'])})
            self._guard(sender)
            return IngestResult(**decision)
        return self._run(process)

    def acknowledge(self, operation_id):
        """Owner acknowledges capture settlement; no peer delivery/read receipt.

        Retaining the last completion until this explicit acknowledgement keeps
        a lost clear response from being displaced by another capture intention.
        """
        def acknowledge():
            _uuid(operation_id)
            record = self._read()
            body = record['payload']['body']
            last = body['last_completed']
            _require(body['state'] == 'idle' and last is not None and last['operation_id'] == operation_id)
            if not last['acknowledged']:
                self._write(record['revision'], {**body,
                    'last_completed': {**last, 'acknowledged': True, 'wire': None}})
            return True
        return self._run(acknowledge)

    def close(self, timeout=5.0):
        """Join this producer; never close the borrowed store or settle RF debt."""
        _require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 <= timeout <= 60)
        with self._condition:
            self._closing = True
            joined = self._condition.wait_for(lambda: self._busy is None, timeout)
            return joined and not self._uncertain and self._known in ('idle', 'captured', 'processed')
