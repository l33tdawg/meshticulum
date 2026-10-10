# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import pytest
pytest.importorskip("sage_sdk", reason="optional SAGE adapter requires compatible SDK")

"""Complete private ingress: real crypto/SDK/store adapter, mocked SAGE endpoint.

These controls establish source semantics, not a production encrypted backend
or native consent authority. The fixture contains only deterministic test keys.
"""
import json
import uuid

import pytest

from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.privacy.mesh_envelope import Peer, seal_delivery_receipt, seal_message
from meshticulum.integrations.sage.mesh_inbox import INBOX_SCHEMA, InboxError, inbox_id
from tests.integrations.sage.test_mesh_inbox import (
    rig, sealed, feed, records, SENDER, SENDER_KEY, RECIPIENT, KEY, REMOTE, OPERATION,
)


def ingest(rig, wire=None, *, inbox=None, check=None):
    return (inbox or rig.inbox).ingest_binary(OPERATION,
        to_binary((wire or sealed()).decode()), sender=SENDER,
        peer_check=check or (lambda peer: peer == rig.peer))


@pytest.mark.parametrize('first', ['binary', 'fragments'])
def test_carrier_switch_and_restart_share_create_only_replay(rig, first):
    original = sealed()
    if first == 'binary':
        result = ingest(rig, original)
    else:
        result = feed(rig.inbox, original)[-1]
    assert result.status == 'admitted'
    before = json.dumps(records(rig), sort_keys=True)
    # Randomized re-encryption and a changed carrier cannot create a second row.
    assert ingest(rig, sealed(), inbox=rig.new()).status == 'duplicate'
    assert feed(rig.new(), sealed())[-1].status == 'duplicate'
    assert json.dumps(records(rig), sort_keys=True) == before
    assert len(records(rig)) == 1 and records(rig)[0]['revision'] == 1
    assert rig.new().historical_read(result.record_id).content == 'private synthetic'
    assert 'private synthetic' not in json.dumps(rig.rows)


def test_renewed_binary_attempt_retains_original_logical_identity(rig):
    original = sealed(expires_at=200)
    assert ingest(rig, original).status == 'admitted'
    before = json.dumps(records(rig), sort_keys=True)
    rig.now = 201
    renewed = sealed(issued_at=201, expires_at=301, attempt_id=str(uuid.UUID(int=91)),
                     logical_issued_at=100, logical_expires_at=200)
    assert ingest(rig, renewed, inbox=rig.new()).status == 'duplicate'
    assert json.dumps(records(rig), sort_keys=True) == before


def test_authenticated_equivocation_cannot_replace_first_admission(rig):
    ingest(rig)
    before = json.dumps(records(rig), sort_keys=True)
    assert ingest(rig, sealed(content='different signed message')).reason == 'equivocation'
    assert json.dumps(records(rig), sort_keys=True) == before


@pytest.mark.parametrize('attack', ['plaintext', 'tamper', 'version', 'wrong_sender', 'wrong_recipient', 'expired'])
def test_complete_binary_endpoint_rejects_without_plaintext_fallback(rig, attack):
    packet = to_binary(sealed().decode())
    sender = SENDER
    if attack == 'plaintext':
        packet = b'private synthetic'
    elif attack == 'tamper':
        packet = packet[:-1] + bytes([packet[-1] ^ 1])
    elif attack == 'version':
        packet = b'LM\x02' + packet[3:]
    elif attack == 'wrong_sender':
        sender = Peer('different-chain', SENDER.public_key)
    elif attack == 'wrong_recipient':
        packet = to_binary(sealed(recipient=Peer('different-chain', RECIPIENT.public_key)).decode())
    else:
        rig.now = 1000
    result = rig.inbox.ingest_binary(OPERATION, packet, sender=sender, peer_check=lambda peer: True)
    assert result.status == 'rejected' and result.reason == 'invalid_envelope'
    assert not rig.rows and not records(rig)


@pytest.mark.parametrize('mode', ['before', 'after', 'conflict'])
def test_unknown_write_fences_same_handler_and_explicit_restart_reconciles(rig, mode):
    wire = sealed()
    rig.failure = (INBOX_SCHEMA, mode)
    with pytest.raises(InboxError):
        ingest(rig, wire)
    assert rig.inbox._uncertain and rig.puts == 1
    rig.failure = None
    with pytest.raises(InboxError):
        ingest(rig, wire)
    assert rig.puts == 1
    result = ingest(rig, wire, inbox=rig.new())
    assert result.status == ('duplicate' if mode == 'after' else 'admitted')
    assert len(records(rig)) == 1
    assert records(rig)[0]['payload']['operation_id'] == OPERATION


@pytest.mark.parametrize('gate', ['live', 'storage', 'pin'])
def test_protected_guards_are_separate_from_codec_success(rig, gate):
    if gate == 'pin':
        rig.peer = None
    else:
        setattr(rig, gate, False)
    with pytest.raises(InboxError):
        ingest(rig)
    assert not rig.rows


def test_temporary_pin_failure_is_unavailable_not_definitive_reject(rig):
    def unavailable(peer):
        raise OSError('synthetic registry unavailable')
    with pytest.raises(InboxError):
        ingest(rig, check=unavailable)
    assert not rig.rows


@pytest.mark.parametrize('change', ['peer', 'live', 'storage', 'instance'])
def test_revocation_or_storage_epoch_change_during_write_returns_no_success(rig, change):
    value = None if change == 'peer' else 'c' * 64 if change == 'instance' else False
    rig.after_put = lambda: setattr(rig, change, value)
    with pytest.raises(InboxError):
        ingest(rig)
    assert len(records(rig)) == 1  # Possible commit is retained, never rewritten.


def test_receipt_ingress_remains_an_assertion(rig):
    wire = seal_delivery_receipt(SENDER_KEY, sender=SENDER, recipient=RECIPIENT,
        message_id=REMOTE, original_id=OPERATION, original_digest='a' * 64,
        issued_at=100, expires_at=1000).wire.encode()
    result = ingest(rig, wire)
    assert result.status == 'admitted' and result.envelope_kind == 'delivery'
    assert not hasattr(result, 'delivered') and not hasattr(result, 'read')


def test_uncertain_fragment_write_also_fences_complete_binary_ingress(rig):
    from meshticulum.integrations.sage.mesh_inbox import ASSEMBLY_SCHEMA
    from tests.integrations.sage.test_mesh_inbox import frame
    from meshticulum.integrations.sage.mesh_fragments import split_envelope
    wire = sealed()
    rig.failure = (ASSEMBLY_SCHEMA, 'before')
    with pytest.raises(InboxError):
        rig.inbox.ingest(OPERATION, frame(split_envelope(wire)[0]))
    rig.failure = None
    with pytest.raises(InboxError):
        ingest(rig, wire)
    assert rig.puts == 1
