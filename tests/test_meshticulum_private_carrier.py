from tests.radio_profiles import SOURCE_ADMISSION
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Recipient-sealed records across two real flow compositions and model workers.

The sessions, scheduler, authority, AEAD and durable stores are real local code.
Worker RF completion is explicitly a source model. There is no device, public
cache, private service admission, autonomous routing or new RF measurement here.
"""
import base64
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
import hashlib
import json
import os
from types import SimpleNamespace

from nacl.exceptions import CryptoError
from nacl.public import SealedBox
from nacl.signing import SigningKey
import pytest

from meshticulum.privacy.mesh_binary_envelope import BinaryEnvelopeError, to_binary, to_text
from meshticulum.privacy.mesh_envelope import (
    MeshEnvelopeError, Peer, SCHEMA, SIGN_DOMAIN, open_envelope, seal_message,
)
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.flow_runtime import BenchFlow
from tests.radio_profiles import profile_for
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import FrameKind, SessionCipher
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.test_meshticulum_bench_runtime import BOOT, Clock, ModelWorker


# Public deterministic fixtures, not deployed identities or session keys.
SENDER_KEY = SigningKey(bytes(range(32)))
RELAY_KEY = SigningKey(bytes(range(32, 64)))
RECIPIENT_KEY = SigningKey(bytes(range(64, 96)))
SENDER = Peer("private-test-a", bytes(SENDER_KEY.verify_key))
RECIPIENT = Peer("private-test-c", bytes(RECIPIENT_KEY.verify_key))
MESSAGE_ID = "00000000-0000-4000-8000-000000000011"
ATTEMPT_ID = "00000000-0000-4000-8000-000000000012"
CONTENT = "synthetic recipient-only message " * 15


def sealed_message(version):
    renewal = {} if version == 1 else dict(attempt_id=ATTEMPT_ID,
        logical_issued_at=900, logical_expires_at=950)
    return seal_message(SENDER_KEY, sender=SENDER, recipient=RECIPIENT,
        message_id=MESSAGE_ID, content=CONTENT, issued_at=1000, expires_at=1100, **renewal)


@contextmanager
def carrier_pair(path, payload, clock, sender_unit, receiver_unit, session_number, *, service=None):
    """Each hop has independent identities, keys, incarnation and durable stores."""
    path.mkdir(mode=0o700)
    profile = profile_for("sf7-500")
    peers = [{"chain_id": "private-carrier-test", "agent_id": unit[-1] * 64}
             for unit in (sender_unit, receiver_unit)]
    digest = hashlib.sha256(payload).hexdigest()
    # Both endpoints authenticate the same original provisioning timestamps.
    # Sampling a ticking clock separately would create two different keys.
    original_epoch = clock()[0]
    flows, workers = [], []
    with ExitStack() as stack:
        for index, unit in enumerate((sender_unit, receiver_unit)):
            directory = path / unit
            directory.mkdir(mode=0o700)
            plan = dict(schema="meshticulum.bench-run.v1", unit=unit, host_boot_id=BOOT,
                local_peer=peers[index], initiator=peers[0], responder=peers[1],
                session_id=bytes([session_number] * 16).hex(),
                generation=bytes([session_number + 10] * 16).hex(),
                issued_epoch_us=original_epoch - 1000000, expires_epoch_us=original_epoch + 180000000,
                profile=asdict(profile), sender_unit=sender_unit, country="MY",
                antenna_confirmed=True, max_frames=512,
                object=dict(sha256=digest, bytes=len(payload), chunk_index=0, object_id=digest))
            if service is not None:
                plan['schema'] = 'meshticulum.service-run.v1'
                plan['service'] = dict(service)
            for name, data in (("grant.json", json.dumps(plan).encode()),
                    ("secret.bin", bytes([session_number] * 32)), ("active", b"active\n")):
                fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
            journal = AttemptJournal(directory / "attempts.db", binding=bytes([session_number] * 32),
                max_attempts=256, max_airtime_us=60000000, create=True, request_clock=lambda: clock()[0])
            stack.callback(journal.close)
            worker = ModelWorker(journal, clock)
            authority = RunAuthority(directory, radio_admission=SOURCE_ADMISSION, expected_unit=unit, host_boot_id=BOOT,
                clock=clock, owner_probe=lambda w=worker: w.owned)
            stack.callback(authority.close)
            store = SessionStore(directory / "wire.db", authority.grant, authority.local_peer,
                boot_id=BOOT, clock=clock, create=True)
            stack.callback(store.close)
            cipher = SessionCipher(authority.grant, authority.local_peer, store)
            ledger = Ledger(BudgetPolicy(180000000, 60000000, 120000000, 1000000,
                quantization_us=1), authority.ledger_epoch, clock()[1])
            flow = BenchFlow(authority=authority, worker=worker, cipher=cipher, journal=journal,
                flow_key=authority.flow_key, contract=authority.contract,
                profile=authority.carrier_profile, ledger=ledger,
                scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
                sends=index == 0, clock=clock)
            stack.callback(flow.close)
            flows.append(flow)
            workers.append(worker)
        workers[0].peer, workers[1].peer = workers[1], workers[0]
        yield SimpleNamespace(sender=flows[0], receiver=flows[1], workers=workers)


def transfer(pair, payload, clock):
    pair.sender.send(payload)
    for _ in range(1600):
        pair.sender.step()
        pair.receiver.step()
        clock.advance(50000)
        if pair.sender.flow.complete and pair.receiver.flow.complete:
            received = bytes(pair.receiver.received)
            assert received == payload
            assert hashlib.sha256(received).hexdigest() == pair.receiver.authority.plan["object"]["sha256"]
            return received
    raise AssertionError("model_transfer_did_not_complete")


def open_private(packet, clock):
    return open_envelope(to_text(packet), RECIPIENT_KEY,
        expected_sender=SENDER, expected_recipient=RECIPIENT, now=clock()[0] // 1000000)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("fault", ["none", "loss", "corrupt", "replay"])
def test_two_hops_preserve_sealed_message_through_link_faults(tmp_path, version, fault):
    clock = Clock()
    sealed = sealed_message(version)
    packet = to_binary(sealed.wire)
    with carrier_pair(tmp_path / "ab", packet, clock, "unit-a", "unit-b", 1) as first:
        if fault == "loss":
            first.workers[0].drop_kind = FrameKind.DATA
        elif fault == "corrupt":
            first.workers[0].corrupt_once = True
        elif fault == "replay":
            first.workers[0].replay_once = True
        relay_record = transfer(first, packet, clock)
        if fault in ("loss", "corrupt"):
            assert first.sender.flow.retransmits >= 1
        if fault in ("corrupt", "replay"):
            assert first.receiver.counts["rx_refused"] >= 1
        # This is precisely what the relay learns after legitimately opening
        # every first-hop frame with its adjacent link key.
        assert CONTENT.encode() not in relay_record
        with pytest.raises(CryptoError):
            SealedBox(RELAY_KEY.to_curve25519_private_key()).decrypt(relay_record[3:])
        with carrier_pair(tmp_path / "bc", relay_record, clock, "unit-b", "unit-c", 2) as second:
            assert first.receiver.authority.local_peer == second.sender.authority.local_peer
            assert first.receiver.cipher.grant.secret != second.sender.cipher.grant.secret
            assert first.receiver.cipher.grant.session != second.sender.cipher.grant.session
            received = transfer(second, relay_record, clock)
            assert to_text(received) == sealed.wire
            result = open_private(received, clock)
            assert result.content == CONTENT and result.digest == sealed.digest
            assert result.attempt_id == (None if version == 1 else ATTEMPT_ID)
            for worker in first.workers + second.workers:
                assert worker.sent and all(CONTENT.encode() not in frame for frame in worker.sent)


def relay_signed_impersonation():
    # A relay can encrypt to the recipient's public key and claim the sender's
    # public identity, but cannot make the pinned sender's signature.
    body = dict(schema=SCHEMA, sender=SENDER.to_dict(), recipient=RECIPIENT.to_dict(),
        message_id=MESSAGE_ID, kind="message", issued_at=1000, expires_at=1100,
        content="relay attempted replacement", original_id=None, original_digest=None)
    canonical = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    signature = RELAY_KEY.sign(SIGN_DOMAIN + canonical(body)).signature
    inner = canonical(dict(body=body, signature=base64.b64encode(signature).decode("ascii")))
    return b"LM\x01" + SealedBox(RECIPIENT_KEY.verify_key.to_curve25519_public_key()).encrypt(inner)


@pytest.mark.parametrize("attack", ["mutate", "impersonate", "version", "plaintext", "expired"])
def test_valid_second_link_does_not_authorize_relay_replacement_or_renewal(tmp_path, attack):
    clock = Clock()
    packet = to_binary(sealed_message(1).wire)
    with carrier_pair(tmp_path / "ab", packet, clock, "unit-a", "unit-b", 1) as first:
        forwarded = transfer(first, packet, clock)
    if attack == "mutate":
        forwarded = forwarded[:-1] + bytes([forwarded[-1] ^ 1])
    elif attack == "impersonate":
        forwarded = relay_signed_impersonation()
    elif attack == "version":
        forwarded = b"LM\x02" + forwarded[3:]
    elif attack == "plaintext":
        forwarded = CONTENT.encode()
    else:
        clock.advance(100000000)
        assert clock()[0] // 1000000 >= 1100
    # Even give the relay a fresh, fully valid link grant for its chosen bytes.
    # Hop authentication succeeds; endpoint identity/expiry must still reject.
    with carrier_pair(tmp_path / "bc", forwarded, clock, "unit-b", "unit-c", 2) as second:
        received = transfer(second, forwarded, clock)
        assert second.receiver.counts["rx_DATA"] > 0
        assert not second.receiver.counts["rx_refused"]
        with pytest.raises((BinaryEnvelopeError, MeshEnvelopeError)):
            open_private(received, clock)
