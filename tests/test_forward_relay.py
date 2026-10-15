# SPDX-License-Identifier: Apache-2.0
"""Real MPF2/held file grants/journals, with radio completion modeled only.

No sockets, devices, native consent or cumulative parent admission are opened.
The two radio-model hops exercise the carrier-independent relay. Actual mixed
radio/IP adoption, owner leases and device retirement belong to the consumer.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import asdict, replace
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority, revoke
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.serial_worker import AttemptResult
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, FrameKind
from meshticulum.contracts.identity import DeadlineSet, PeerID
from meshticulum.privacy.forward_relay import (
    ForwardRelayError, RelayDescription, OpaqueForwardRelay, describe_stage, SEQUENCE)
from meshticulum.privacy.forward_session import ForwardSessionError, HEADER
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.radio_profiles import SOURCE_ADMISSION, profile_for
from tests.test_forward_session_flow import session_pair
from tests.test_meshticulum_bench_runtime import BOOT, Clock, ModelWorker
from tests.test_meshticulum_private_carrier import CONTENT, sealed_message, open_private


RELAY = PeerID('relay-source-control', 'a' * 64)


@contextmanager
def carrier(path, packet, clock, source, destination, number, context, *, expiry=None):
    """Distinct actual held grants/stores; only the physical worker is a model."""
    path.mkdir(mode=0o700)
    profile = profile_for('sf7-500')
    pin = hashlib.sha256(packet).hexdigest()
    flows, workers, transfers = [], [], []
    with ExitStack() as stack:
        for index, peer in enumerate((source, destination)):
            unit = 'unit-a' if index == 0 else 'unit-b'
            directory = path / unit
            directory.mkdir(mode=0o700)
            end = context.expires_epoch_us if expiry is None else expiry
            plan = dict(schema='meshticulum.service-run.v1', unit=unit, host_boot_id=BOOT,
                local_peer=asdict(peer), initiator=asdict(source), responder=asdict(destination),
                session_id=number.to_bytes(16, 'big').hex(),
                generation=(1000 + number).to_bytes(16, 'big').hex(),
                issued_epoch_us=context.issued_epoch_us, expires_epoch_us=end,
                profile=asdict(profile), sender_unit='unit-a', country='MY',
                antenna_confirmed=True, max_frames=512,
                object=dict(sha256=pin, bytes=len(packet), chunk_index=0, object_id=pin),
                service=dict(kind='protected_message', format='MPF2.record',
                             application_expires_epoch_us=end))
            for name, raw in (('grant.json', json.dumps(plan).encode()),
                              ('secret.bin', bytes([number]) * 32), ('active', b'active\n')):
                fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as output:
                    output.write(raw)
            journal = AttemptJournal(directory / 'attempts.sqlite', binding=bytes([number]) * 32,
                max_attempts=256, max_airtime_us=60000000, create=True,
                request_clock=lambda: clock()[0])
            stack.callback(journal.close)
            worker = ModelWorker(journal, clock)
            authority = RunAuthority(directory, radio_admission=SOURCE_ADMISSION,
                expected_unit=unit, host_boot_id=BOOT, clock=clock, owner_probe=lambda w=worker: w.owned)
            stack.callback(authority.close)
            store = SessionStore(directory / 'wire.sqlite', authority.grant, peer,
                boot_id=BOOT, clock=clock, create=True)
            stack.callback(store.close)
            flow = BenchFlow(authority=authority, worker=worker,
                cipher=SessionCipher(authority.grant, peer, store), journal=journal,
                flow_key=authority.flow_key, contract=authority.contract,
                profile=authority.carrier_profile,
                ledger=Ledger(BudgetPolicy(180000000, 60000000, 120000000, 1000000,
                    quantization_us=1), authority.ledger_epoch, clock()[1]),
                scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
                sends=index == 0, clock=clock)
            stack.callback(flow.close)
            flows.append(flow)
            workers.append(worker)
            transfers.append(ServiceTransfer(flow))
        workers[0].peer, workers[1].peer = workers[1], workers[0]
        yield SimpleNamespace(flows=flows, workers=workers, transfers=transfers)


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    inner = to_binary(sealed_message(1).wire)
    sessions, context, stores = session_pair(tmp_path, clock, inner)
    deadline = clock()[1] + context.expires_epoch_us - clock()[0]
    description = RelayDescription(context, RELAY,
        DeadlineSet(deadline / 1e6, deadline / 1e6, context.expires_epoch_us / 1e6))
    relay = OpaqueForwardRelay(description, clock=clock)
    result = SimpleNamespace(clock=clock, inner=inner, sessions=sessions, context=context,
                             stores=stores, description=description, relay=relay,
                             path=tmp_path, deliveries=[])
    try:
        yield result
    finally:
        relay.close()
        for session in sessions:
            session.close()
        for store in stores:
            store.db.close()


@contextmanager
def stage(rig, packet, kind, *, outgoing_packet=None, expiry=None):
    slot = SEQUENCE.index(kind)
    peers = tuple(PeerID(p.chain_id, p.public_key.hex())
                  for p in (rig.context.initiator, rig.context.responder))
    source, target = peers if slot % 2 == 0 else peers[::-1]
    with carrier(rig.path / (str(slot) + '-in'), packet, rig.clock, source, RELAY,
                 slot * 2 + 1, rig.context) as incoming, \
         carrier(rig.path / (str(slot) + '-out'), packet if outgoing_packet is None else outgoing_packet,
                 rig.clock, RELAY, target, slot * 2 + 2, rig.context, expiry=expiry) as outgoing:
        yield incoming, outgoing


def complete_stage(rig, packet, kind, *, fault='none'):
    with stage(rig, packet, kind) as (incoming, outgoing):
        ingress, egress = incoming.transfers[1], outgoing.transfers[0]
        value = describe_stage(kind, ingress, egress)
        rig.relay.begin(value, ingress, egress)
        if fault == 'loss':
            incoming.workers[0].drop_kind = outgoing.workers[0].drop_kind = FrameKind.DATA
        elif fault == 'replay':
            incoming.workers[0].replay_once = outgoing.workers[0].replay_once = True
        incoming.transfers[0].send(packet)
        for _ in range(1600):
            incoming.flows[0].step()
            done = rig.relay.step()
            outgoing.flows[1].step()
            if done and incoming.flows[0].flow.complete and outgoing.flows[1].flow.complete:
                break
            rig.clock.advance(50000)
        else:
            raise AssertionError('source relay did not complete within its original window')
        received = outgoing.transfers[1].received()
        assert received == packet and CONTENT.encode() not in received
        receipt = rig.relay.finish_stage()
        assert receipt['packet_sha256'] == hashlib.sha256(packet).hexdigest()
        assert receipt['outgoing_final_ack_observed'] and not receipt['private_admission']
        assert not receipt['native_retirement_proved']
        charged = [f.journal.snapshot() for f in incoming.flows + outgoing.flows]
        assert all(row['attempts'] > 0 and row['reserved_estimated_airtime_us'] > 0 for row in charged)
        rig.deliveries.append(charged)
        assert rig.relay.snapshot()['queued_bytes'] == 0
        return received


def exchange(rig, fault='none'):
    i, r = rig.sessions
    second = r.respond(complete_stage(rig, i.start(), 1, fault=fault))
    i.finish(complete_stage(rig, second, 2, fault=fault))
    fourth = r.accept_confirmation(complete_stage(rig, i.confirm(), 3, fault=fault))
    i.verify_confirmation(complete_stage(rig, fourth, 4, fault=fault))
    assert i.state == r.state == 'ready'


@pytest.mark.parametrize('fault', ['none', 'loss', 'replay'])
def test_five_exact_directions_preserve_real_endpoint_crypto_and_journal_debt(rig, fault):
    exchange(rig, fault)
    packet = rig.sessions[0].seal(rig.inner)
    received = complete_stage(rig, packet, 5, fault=fault)
    opened = rig.sessions[1].open(received)
    assert opened == rig.inner and open_private(opened, rig.clock).content == CONTENT
    assert rig.relay.snapshot()['completed_stages'] == 5
    assert rig.relay.snapshot()['refunds'] == 0
    assert len(rig.deliveries) == 5
    assert rig.stores[0].reserved == 2 and rig.stores[1].reserved == 1
    assert rig.stores[1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0] == 1
    assert not any(name in vars(rig.relay) for name in ('signing_key', 'noise_static_key', 'session', 'plaintext'))


@pytest.mark.parametrize('change', ['direction', 'route', 'grant', 'hash', 'kind', 'carrier'])
def test_changed_stage_cannot_queue_or_open_a_carrier(rig, change):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        if change == 'direction':
            value = replace(value, incoming=replace(value.incoming,
                session=replace(value.incoming.session, initiator=value.incoming.session.responder,
                                responder=value.incoming.session.initiator)))
        elif change == 'route':
            value = replace(value, outgoing=replace(value.outgoing, route_binding_digest=b'x' * 32))
        elif change == 'grant':
            value = replace(value, outgoing=replace(value.outgoing, grant_digest=b'x' * 32))
        elif change == 'carrier':
            value = replace(value, outgoing=replace(value.outgoing, carrier='ip'))
        elif change == 'hash':
            value = replace(value, packet_sha256=b'x' * 32)
        else:
            value = replace(value, kind=2)
        with pytest.raises(ForwardRelayError):
            rig.relay.begin(value, incoming.transfers[1], outgoing.transfers[0])
        assert all(not w.sent for w in incoming.workers + outgoing.workers)
        assert rig.relay.snapshot()['closed'] and rig.relay.snapshot()['refunds'] == 0


@pytest.mark.parametrize('size', [0, 76, 78, 141, 1000000, True])
def test_handshake_size_is_checked_before_work_is_bound(rig, size):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        with pytest.raises(ForwardRelayError):
            replace(value, packet_bytes=size)
        assert all(not w.sent for w in incoming.workers + outgoing.workers)


def test_truthy_description_or_duck_typed_carrier_is_not_live_authority(rig):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        fake = SimpleNamespace(live=lambda: True, authorized=True,
                               flow=incoming.flows[1], descriptor=incoming.transfers[1].descriptor)
        with pytest.raises(ForwardRelayError):
            rig.relay.begin(value, fake, outgoing.transfers[0])
        assert all(not w.sent for w in incoming.workers + outgoing.workers)


@pytest.mark.parametrize('change', ['epoch', 'mono', 'rollback', 'revocation', 'descriptor', 'owner'])
def test_expired_or_revoked_work_is_refused_without_new_attempt_or_debt_reset(rig, change):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        rig.relay.begin(value, incoming.transfers[1], outgoing.transfers[0])
        incoming.transfers[0].send(packet)
        incoming.flows[0].step()
        before = [f.journal.snapshot() for f in incoming.flows + outgoing.flows]
        if change == 'epoch':
            rig.clock.epoch = rig.context.expires_epoch_us
        elif change == 'mono':
            rig.clock.mono = int(rig.description.deadlines.application_deadline_monotonic * 1e6)
        elif change == 'rollback':
            rig.clock.epoch -= 1
        elif change == 'revocation':
            revoke(outgoing.flows[0].authority.path)
        elif change == 'descriptor':
            path = outgoing.flows[0].authority.path / 'grant.json'
            path.write_bytes(path.read_bytes() + b' ')
        else:
            outgoing.workers[0].owned = False
        with pytest.raises(ForwardRelayError):
            rig.relay.step()
        assert [f.journal.snapshot() for f in incoming.flows + outgoing.flows] == before
        assert rig.relay.snapshot()['closed'] and rig.relay.snapshot()['uncertain']
        assert not outgoing.workers[0].sent


def test_newer_adjacent_grant_cannot_extend_original_context(rig):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1, expiry=rig.context.expires_epoch_us + 1000000) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        with pytest.raises(ForwardRelayError):
            rig.relay.begin(value, incoming.transfers[1], outgoing.transfers[0])
        assert not outgoing.workers[0].sent


def test_relay_replacement_requires_exact_both_hop_descriptor_and_endpoint_rejects_tamper(rig):
    exchange(rig)
    original = rig.sessions[0].seal(rig.inner)
    tampered = original[:-1] + bytes([original[-1] ^ 1])
    with stage(rig, original, 5, outgoing_packet=tampered) as (incoming, outgoing):
        with pytest.raises(ForwardRelayError):
            describe_stage(5, incoming.transfers[1], outgoing.transfers[0])
    # Even a compromised relay bypassing the helper cannot authenticate its
    # replacement at the actual endpoint. Already reserved endpoint debt stays.
    before = rig.stores[1].db.execute('SELECT count(*) FROM received').fetchone()[0]
    with pytest.raises(ForwardSessionError):
        rig.sessions[1].open(tampered)
    assert rig.sessions[1].state == 'closed'
    assert rig.stores[1].db.execute('SELECT count(*) FROM received').fetchone()[0] == before
    assert rig.stores[0].reserved == 2


@pytest.mark.parametrize('mode', ['wrong_session', 'plaintext', 'duplicate_begin'])
def test_record_shape_context_and_single_pending_stage_are_enforced(rig, mode):
    packet = rig.sessions[0].start()
    if mode == 'wrong_session':
        packet = packet[:5] + b'z' * 16 + packet[21:]
    elif mode == 'plaintext':
        packet = b'unencrypted source fixture'.ljust(len(packet), b' ')
    with stage(rig, packet, 1) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        rig.relay.begin(value, incoming.transfers[1], outgoing.transfers[0])
        if mode == 'duplicate_begin':
            with pytest.raises(ForwardRelayError):
                rig.relay.begin(value, incoming.transfers[1], outgoing.transfers[0])
        else:
            # Shape-only source send is expected to reject plaintext even
            # before the relay. Wrong MPF2 context has valid outer shape.
            if mode == 'plaintext':
                with pytest.raises(ForwardSessionError):
                    incoming.transfers[0].send(packet)
                return
            incoming.transfers[0].send(packet)
            with pytest.raises(ForwardRelayError):
                for _ in range(200):
                    incoming.flows[0].step()
                    rig.relay.step()
                    rig.clock.advance(50000)
        assert not outgoing.workers[0].sent
        assert rig.relay.snapshot()['queued_records'] == 0


def test_unknown_outgoing_attempt_fences_without_retry_or_refund(rig):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        rig.relay.begin(value, incoming.transfers[1], outgoing.transfers[0])
        worker = outgoing.workers[0]
        attempted = []
        def unknown(payload):
            token = worker.journal.reserve(payload, 200000)
            worker.journal.settle(token, payload, 'UNKNOWN', 'source_unknown_model')
            worker.fenced = True
            attempted.append(payload)
            return AttemptResult(token, 'UNKNOWN', 200000, None)
        worker.transmit = unknown
        incoming.transfers[0].send(packet)
        with pytest.raises(ForwardRelayError):
            for _ in range(300):
                incoming.flows[0].step()
                rig.relay.step()
                outgoing.flows[1].step()
                rig.clock.advance(50000)
        assert len(attempted) == 1
        spent = worker.journal.snapshot()
        assert spent['attempts'] == 1 and spent['reserved_estimated_airtime_us'] == 200000
        assert spent['fenced']
        with pytest.raises(ForwardRelayError):
            rig.relay.step()
        assert worker.journal.snapshot() == spent and len(attempted) == 1
        assert rig.relay.snapshot()['uncertain'] and rig.relay.snapshot()['refunds'] == 0


def test_base_exception_cancels_borrowed_queues_and_is_not_hidden(rig, monkeypatch):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1) as (incoming, outgoing):
        value = describe_stage(1, incoming.transfers[1], outgoing.transfers[0])
        rig.relay.begin(value, incoming.transfers[1], outgoing.transfers[0])
        def interrupted(_):
            raise KeyboardInterrupt('original source interruption')
        monkeypatch.setattr(Clock, '__call__', interrupted)
        with pytest.raises(KeyboardInterrupt, match='original source interruption'):
            rig.relay.step()
        assert incoming.transfers[1].cancelled and outgoing.transfers[0].cancelled
        assert rig.relay.snapshot()['closed'] and not outgoing.workers[0].sent
