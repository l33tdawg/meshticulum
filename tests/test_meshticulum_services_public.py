from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from meshticulum.bench.authority import RunAuthority, revoke
from meshticulum.bench.services import PreparedBlockExchange, ServiceTransfer, resource
from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.wire import FrameKind, WireError
from meshticulum.contracts.identity import ServiceKind
from meshticulum.contracts.negotiation import Feature, TrafficClass
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision
from tests.test_meshticulum_resources import pack
from tests.service_helpers import scope


def test_public_adapter_loads_without_private_runtime_or_sdk():
    root = Path(__file__).resolve().parents[1]
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
