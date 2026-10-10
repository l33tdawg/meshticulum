# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import pytest
pytest.importorskip("sage_sdk", reason="optional SAGE adapter requires compatible SDK")

"""Finite service integration with real local flow/leases and modeled RF.

Private storage uses the real SDK WorkflowStore adapter with MockTransport;
neither the modeled worker nor that backend proves physical/private admission.
"""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.integrations.sage.mesh_inbox import InboxError
from meshticulum.bench.authority import RunAuthority, revoke
from meshticulum.bench.services import PreparedBlockExchange, ServiceTransfer, resource
from meshticulum.bench.wire import FrameKind, WireError
from meshticulum.contracts.identity import ServiceKind
from meshticulum.contracts.negotiation import Feature, TrafficClass
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision
from tests.test_meshticulum_resources import pack
from tests.integrations.sage.test_mesh_inbox import rig, sealed, records, SENDER, OPERATION


def scope(format, clock, expires=None):
    return dict(format=format, kind='protected_message' if format == 'LME.binary' else 'knowledge',
                application_expires_epoch_us=expires or clock()[0] + 60000000)


def test_public_adapter_loads_without_private_runtime_or_sdk():
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run([sys.executable, '-c',
        'import sys; from meshticulum.bench.services import ServiceTransfer; '
        'assert not any(n == "sage_lantern" or n.startswith("sage_lantern.") '
        'or n == "sage_sdk" or n.startswith("sage_sdk.") for n in sys.modules)'],
        cwd=root, env={**os.environ, 'PYTHONPATH': str(root)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def finish(pair, clock):
    for _ in range(1600):
        pair.sender.step()
        pair.receiver.step()
        clock.advance(50000)
        if pair.sender.flow.complete and pair.receiver.flow.complete:
            return
    raise AssertionError('source_model_flow_did_not_complete')


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('fault', ['none', 'loss', 'corrupt', 'replay'])
def test_private_service_two_hops_reach_protected_replay_admission(tmp_path, rig, version, fault):
    clock = Clock()
    renewal = {} if version == 1 else dict(attempt_id='00000000-0000-4000-8000-000000000099',
        logical_issued_at=100, logical_expires_at=200)
    wire = sealed(issued_at=1000, expires_at=1100, **renewal)
    payload = to_binary(wire.decode())
    service = scope('LME.binary', clock)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 11, service=service) as first:
        start, relay = ServiceTransfer(first.sender), ServiceTransfer(first.receiver)
        assert start.kind is ServiceKind.MESSAGE
        assert first.sender.authority.contract.traffic_class is TrafficClass.INTERACTIVE
        assert Feature.PROTECTED_MESSAGE_V2 in first.sender.authority.contract.features
        if fault == 'loss':
            first.workers[0].drop_kind = FrameKind.DATA
        elif fault == 'corrupt':
            first.workers[0].corrupt_once = True
        elif fault == 'replay':
            first.workers[0].replay_once = True
        start.send(payload)
        finish(first, clock)
        forwarded = relay.received()
        assert forwarded == payload and b'private synthetic' not in forwarded
        with carrier_pair(tmp_path / 'bc', forwarded, clock, 'unit-b', 'unit-c', 12,
                          service=service) as second:
            onward, destination = ServiceTransfer(second.sender), ServiceTransfer(second.receiver)
            onward.send(forwarded)
            finish(second, clock)
            rig.now = clock()[0] / 1000000
            admitted = destination.deliver_private(rig.inbox, operation_id=OPERATION,
                sender=SENDER, peer_check=lambda peer: peer == rig.peer)
            assert admitted.status == 'admitted'
            before = json.dumps(records(rig), sort_keys=True)
            # Replaying a completed transfer into a fresh handler is harmless.
            duplicate = destination.deliver_private(rig.new(), operation_id=OPERATION,
                sender=SENDER, peer_check=lambda peer: peer == rig.peer)
            assert duplicate.status == 'duplicate'
            assert json.dumps(records(rig), sort_keys=True) == before
            assert rig.new().historical_read(admitted.record_id).content == 'private synthetic'
            assert 'private synthetic' not in json.dumps(rig.rows)
            for endpoint in (first.sender, first.receiver, second.sender, second.receiver):
                assert endpoint.journal.snapshot()['attempts'] > 0


@pytest.mark.parametrize('gate', ['expired', 'revoked', 'worker_lost', 'consent_lost', 'cancelled'])
def test_completed_flow_does_not_bypass_current_application_guards(tmp_path, rig, gate):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    service = scope('LME.binary', clock)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 21, service=service) as pair:
        start, sink = ServiceTransfer(pair.sender), ServiceTransfer(pair.receiver)
        start.send(payload)
        finish(pair, clock)
        if gate == 'expired':
            clock.advance(service['application_expires_epoch_us'] - clock()[0])
        elif gate == 'revoked':
            revoke(pair.receiver.authority.path)
        elif gate == 'worker_lost':
            pair.workers[1].owned = False
        elif gate == 'consent_lost':
            rig.peer = None
        else:
            sink.cancel()
        with pytest.raises((WireError, InboxError)):
            sink.deliver_private(rig.inbox, operation_id=OPERATION, sender=SENDER,
                                 peer_check=lambda peer: peer == rig.peer)
        assert not rig.rows


def test_private_records_cannot_enter_public_handler_or_implicit_bench_service(tmp_path):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    calls = []
    server = resource.BlockServer(lambda path: calls.append(path))
    with carrier_pair(tmp_path / 'private', payload, clock, 'unit-a', 'unit-b', 31,
                      service=scope('LME.binary', clock)) as pair:
        start, sink = ServiceTransfer(pair.sender), ServiceTransfer(pair.receiver)
        with pytest.raises(WireError):
            sink.serve_public(server)
        start.send(payload)
        finish(pair, clock)
        with pytest.raises(WireError):
            sink.serve_public(server)
        assert not calls
    with carrier_pair(tmp_path / 'legacy', payload, clock, 'unit-a', 'unit-b', 32) as pair:
        with pytest.raises(WireError):
            ServiceTransfer(pair.sender)


def test_knowledge_grant_cannot_send_binary_private_record(tmp_path):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 33,
                      service=scope('MR1.request', clock)) as pair:
        start = ServiceTransfer(pair.sender)
        with pytest.raises(ValueError):
            start.send(payload)
        assert not pair.workers[0].sent and not pair.sender.flow.queued_bytes()


def test_incomplete_or_wrong_object_is_never_application_content(tmp_path):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 34,
                      service=scope('LME.binary', clock)) as pair:
        start, sink = ServiceTransfer(pair.sender), ServiceTransfer(pair.receiver)
        with pytest.raises(WireError):
            sink.received()
        with pytest.raises(WireError):
            start.send(payload[:-1] + bytes([payload[-1] ^ 1]))
        start.send(payload)
        finish(pair, clock)
        pair.receiver.received[-1] ^= 1
        with pytest.raises(WireError):
            sink.received()


def test_cancelled_host_queue_keeps_worker_attempt_debt(tmp_path):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 35,
                      service=scope('LME.binary', clock)) as pair:
        start = ServiceTransfer(pair.sender)
        start.send(payload)
        for _ in range(5):
            pair.sender.step(); pair.receiver.step(); clock.advance(50000)
        before = pair.sender.journal.snapshot()
        start.cancel()
        assert pair.sender.journal.snapshot() == before
        with pytest.raises(WireError):
            start.send(payload)
        assert pair.sender.scheduler.snapshot()['flows'] == 0


@pytest.mark.parametrize('change', ['format', 'kind', 'deadline', 'extra'])
def test_invalid_root_service_scope_is_refused_before_claim(tmp_path, change):
    clock = Clock()
    directory = tmp_path / 'grant'
    plan = provision(directory, 'unit-a', b'x', clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('LME.binary', clock)
    if change == 'format':
        plan['service']['format'] = 'text-fallback'
    elif change == 'kind':
        plan['service']['kind'] = 'knowledge'
    elif change == 'deadline':
        plan['service']['application_expires_epoch_us'] = plan['expires_epoch_us'] + 1
    else:
        plan['service']['extra'] = True
    (directory / 'grant.json').write_text(json.dumps(plan))
    with pytest.raises(WireError):
        RunAuthority(directory, expected_unit='unit-a', host_boot_id=BOOT,
                     clock=clock, owner_probe=lambda: True)
    assert not (directory / 'started').exists()


def test_service_format_and_original_expiry_are_in_crypto_context(tmp_path):
    clock = Clock()
    contexts = []
    for index, format in enumerate(['MR1.request', 'MR1.response', 'LME.binary', 'MR1.request']):
        directory = tmp_path / str(index)
        plan = provision(directory, 'unit-a', b'x', clock)
        plan['schema'] = 'meshticulum.service-run.v1'
        plan['service'] = scope(format, clock)
        if index == 3:
            plan['service']['application_expires_epoch_us'] -= 1000000
        (directory / 'grant.json').write_text(json.dumps(plan))
        with ExitStack() as stack:
            authority = RunAuthority(directory, expected_unit='unit-a', host_boot_id=BOOT,
                                     clock=clock, owner_probe=lambda: True)
            stack.callback(authority.close)
            contexts.append(authority.grant.digest)
    assert len(set(contexts)) == 4  # Same secret/session/object cannot relabel or renew a service.


@contextmanager
def prepared_pair(tmp_path, fault):
    clock = Clock()
    clock.mono = int(time.monotonic() * 1000000)
    request = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    reply = resource.BlockServer(lambda path: b'public fixture').handle(request)
    magic, binding, status, total, offset = resource.RESPONSE.unpack(reply[:resource.RESPONSE.size])
    body = reply[resource.RESPONSE.size:]
    if fault == 'binding':
        binding = bytes(16)
    elif fault == 'offset':
        offset, body = 1, body[1:]
    elif fault == 'short':
        body = body[:-1]
    reply = resource.RESPONSE.pack(magic, binding, status, total, offset) + body
    expires = clock()[0] + 60000000
    with carrier_pair(tmp_path / 'request', request, clock, 'unit-a', 'unit-b', 61,
                      service=scope('MR1.request', clock, expires)) as forward:
        start = ServiceTransfer(forward.sender)
        with carrier_pair(tmp_path / 'reply', reply, clock, 'unit-b', 'unit-a', 62,
                          service=scope('MR1.response', clock, expires)) as reverse:
            source, sink = ServiceTransfer(reverse.sender), ServiceTransfer(reverse.receiver)
            sent = False

            def pump():
                nonlocal sent
                forward.sender.step(); forward.receiver.step()
                if forward.receiver.flow.complete and not sent:
                    source.send(reply)
                    sent = True
                reverse.sender.step(); reverse.receiver.step()
                clock.advance(expires - clock()[0] if fault == 'expires' else 50000)

            yield PreparedBlockExchange(start, sink, pump=pump), request, clock, forward, reverse


@pytest.mark.parametrize('fault', ['binding', 'offset', 'short', 'expires', 'revoked', 'deadline', 'request'])
def test_authenticated_block_flow_still_enforces_application_binding_and_deadline(tmp_path, fault):
    with prepared_pair(tmp_path, fault) as (exchange, request, clock, forward, reverse):
        deadline = clock()[1] / 1000000 + (0.01 if fault == 'deadline' else 120)
        if fault == 'revoked':
            revoke(forward.sender.authority.path)
        elif fault == 'request':
            request = request[:-1] + bytes([request[-1] ^ 1])
        with pytest.raises(WireError):
            exchange(request, resource.RESPONSE.size + 32, deadline)
        if fault in ('deadline', 'revoked', 'request'):
            assert not forward.workers[0].sent and not reverse.workers[0].sent
        if fault != 'deadline':
            assert exchange.used and exchange.request.cancelled and exchange.response.cancelled


def test_nested_application_lease_cannot_unlock_outer_revocation_barrier(tmp_path):
    import fcntl
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 71,
                      service=scope('LME.binary', clock)) as pair:
        authority = pair.sender.authority
        with authority.application_lease():
            with pytest.raises(WireError):
                with authority.application_lease():
                    raise AssertionError('nested lease admitted')
            with (authority.path / 'active').open('r+b') as handle:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not authority.held


def test_reply_bound_is_enforced_before_either_direction_transmits(tmp_path):
    with prepared_pair(tmp_path, 'none') as (exchange, request, clock, forward, reverse):
        with pytest.raises(WireError):
            exchange(request, resource.RESPONSE.size + 1, clock()[1] / 1000000 + 120)
        assert not forward.workers[0].sent and not reverse.workers[0].sent
        assert not exchange.used


def test_expiry_during_private_store_write_retains_commit_without_success(tmp_path, rig):
    clock = Clock()
    payload = to_binary(sealed(issued_at=1000, expires_at=1100).decode())
    service = scope('LME.binary', clock)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 72,
                      service=service) as pair:
        start, sink = ServiceTransfer(pair.sender), ServiceTransfer(pair.receiver)
        start.send(payload)
        finish(pair, clock)
        rig.now = clock()[0] / 1000000
        rig.after_put = lambda: clock.advance(service['application_expires_epoch_us'] - clock()[0])
        with pytest.raises(InboxError):
            sink.deliver_private(rig.inbox, operation_id=OPERATION, sender=SENDER,
                                 peer_check=lambda peer: peer == rig.peer)
        assert len(records(rig)) == 1
        assert records(rig)[0]['payload']['operation_id'] == OPERATION


def test_verified_public_block_reader_uses_real_request_and_reply_flows(tmp_path, pack):
    from meshticulum.resources import lamplighter as ll
    directory, trust, digest, manifest = pack
    cache = ll.VerifiedCache(directory, trust, digest)
    server = resource.BlockServer(cache.read)
    clock = Clock()
    clock.mono = int(time.monotonic() * 1000000)
    deadline = time.monotonic() + 10
    # One original expiry, shared by every request/reply and never refreshed.
    expires = clock()[0] + 5000000
    observations = []

    def exchange(request, limit, original_deadline):
        index = len(observations)
        expected = server.handle(request)
        with carrier_pair(tmp_path / ('request-' + str(index)), request, clock,
                          'unit-a', 'unit-b', 41 + index * 2,
                          service=scope('MR1.request', clock, expires)) as forward:
            request_source, request_sink = ServiceTransfer(forward.sender), ServiceTransfer(forward.receiver)
            with carrier_pair(tmp_path / ('response-' + str(index)), expected, clock,
                              'unit-b', 'unit-a', 42 + index * 2,
                              service=scope('MR1.response', clock, expires)) as reverse:
                reply_source, reply_sink = ServiceTransfer(reverse.sender), ServiceTransfer(reverse.receiver)
                started = False

                def pump():
                    nonlocal started
                    forward.sender.step(); forward.receiver.step()
                    if forward.receiver.flow.complete and not started:
                        reply_source.send(request_sink.serve_public(server))
                        started = True
                    reverse.sender.step(); reverse.receiver.step()
                    clock.advance(50000)

                prepared = PreparedBlockExchange(request_source, reply_sink, pump=pump)
                result = prepared(request, limit, original_deadline)
                observations.append((original_deadline, forward.sender.journal.snapshot(),
                                     forward.receiver.journal.snapshot(), reverse.sender.journal.snapshot(),
                                     reverse.receiver.journal.snapshot()))
                return result

    entry = manifest['chunks'][0]
    reader = resource.BlockReader(exchange, max_block=96)
    raw = reader('/packs/' + digest + '/chunks/' + entry['sha256'] + '.json', 65536, deadline)
    ll.chunk(raw, entry)
    assert raw == cache.piece(entry['sha256']) and len(observations) > 1
    assert all(bound == deadline and all(journal['attempts'] > 0 for journal in journals)
               for bound, *journals in observations)


def test_legacy_public_runner_refuses_service_before_radio(tmp_path, monkeypatch):
    from meshticulum.bench import node
    clock = Clock()
    directory = tmp_path / 'grant'
    plan = provision(directory, 'unit-a', b'x', clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('LME.binary', clock)
    (directory / 'grant.json').write_text(json.dumps(plan))
    original = node.Path.read_text
    monkeypatch.setattr(node.Path, 'read_text', lambda path, *a, **k:
        BOOT if str(path) == '/proc/sys/kernel/random/boot_id' else original(path, *a, **k))
    monkeypatch.setattr(node, 'clock', clock)
    monkeypatch.setattr(node, 'roles', lambda unit: {'model': 'test-only'})
    opened = []
    monkeypatch.setattr(node, '_serial', lambda unit: opened.append(unit))
    result = node.run('unit-a', directory, tmp_path / 'public-objects')
    assert result['error'].endswith('service_requires_dedicated_runner')
    assert not opened and not (tmp_path / 'public-objects').exists()
