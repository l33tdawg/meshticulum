"""Verified-piece resume across actual TCP and paced modeled RF, never RF proof."""
from contextlib import ExitStack
import hashlib
import itertools
import time

import pytest

from meshticulum.bench.services import PreparedBlockExchange, ServiceTransfer, resource
from meshticulum.contracts.identity import PeerID
from meshticulum.ip.journal import IpJournal
from meshticulum.resources import lamplighter as ll
from meshticulum.resources import swarm
from tests.service_helpers import scope
from tests.test_ip_transport import Clock, pair as tcp_pair
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.test_meshticulum_resources import pack


class FreshCarrier:
    """Synthetic local provisioner: every block gets two independent admissions.

    The RF worker is a model. Deliberate 20 ms pump pacing makes the scheduler
    decision repeatable; neither these timings nor TCP loopback imply RF rates.
    """
    def __init__(self, path, server, clock, expires, deadline, serials, *, radio=False, generation=1):
        path.mkdir(mode=0o700)
        self.path, self.server, self.clock = path, server, clock
        self.expires, self.deadline, self.serials = expires, deadline, serials
        self.radio, self.generation = radio, generation
        self.records = []
        self.failed_journal = None
        self.disconnect_next_chunk = False

    def exchange(self, request, limit, deadline):
        if time.monotonic() >= self.deadline or self.clock()[0] >= self.expires:
            raise TimeoutError('original resource authorization expired')
        assert deadline >= self.deadline
        response = self.server.handle(request)
        first, second = next(self.serials), next(self.serials)
        resource_path, _, _ = resource.decode_request(request)
        fail = self.disconnect_next_chunk and '/chunks/' in resource_path
        if fail:
            self.disconnect_next_chunk = False
        record = dict(path=resource_path, failed=fail, generation=self.generation)
        with ExitStack() as stack:
            if self.radio:
                request_pair = stack.enter_context(carrier_pair(self.path / str(first), request,
                    self.clock, 'unit-c', 'unit-b', first,
                    service=scope('MR1.request', self.clock, self.expires)))
                response_pair = stack.enter_context(carrier_pair(self.path / str(second), response,
                    self.clock, 'unit-b', 'unit-c', second,
                    service=scope('MR1.response', self.clock, self.expires)))
                flows = [request_pair.sender, request_pair.receiver,
                         response_pair.sender, response_pair.receiver]
                workers = request_pair.workers + response_pair.workers
                assert all(f.cipher.grant.digest == p.sender.cipher.grant.digest
                           for p in (request_pair, response_pair) for f in (p.sender, p.receiver))
            else:
                c = PeerID('private-carrier-test', 'c' * 64)
                b = PeerID(c.chain_id, 'b' * 64)
                route = hashlib.sha256(('source-generation-' + str(self.generation)).encode()).digest()
                args = dict(expires=self.expires, deadline_us=int(self.deadline * 1e6),
                            route_binding_digest=route)
                forward, fw, _, _ = stack.enter_context(tcp_pair(self.path / str(first), request,
                    initiator=c, responder=b, session_id=bytes([first]) * 16,
                    generation=bytes([first + 10]) * 16, **args))
                reverse, rw, _, _ = stack.enter_context(tcp_pair(self.path / str(second), response,
                    'MR1.response', initiator=b, responder=c, session_id=bytes([second]) * 16,
                    generation=bytes([second + 10]) * 16, **args))
                flows, workers = forward + reverse, fw + rw
            tx, rx, reply_tx, reply_rx = [ServiceTransfer(f) for f in flows]
            sent_response = False
            disconnected = False

            def pump():
                nonlocal sent_response, disconnected
                flows[0].step()
                if fail and not disconnected and workers[0].journal.snapshot()['lanes'].get('DATA', 0):
                    workers[0].close()
                    disconnected = True
                flows[1].step()
                if flows[1].flow.complete and not sent_response:
                    reply_tx.send(rx.serve_public(self.server))
                    sent_response = True
                flows[2].step()
                flows[3].step()
                time.sleep(.02 if self.radio else .0002)

            exchange = PreparedBlockExchange(tx, reply_rx, pump=pump)
            try:
                return exchange(request, limit, deadline)
            finally:
                # Capture before native teardown; costs are never subtracted.
                record['sessions'] = [f.authority.grant.session.session_id.hex() for f in (flows[0], flows[2])]
                record['expiries'] = [f.authority.application_expires_epoch_us for f in flows]
                record['deadlines'] = [f.authority.deadlines.application_deadline_monotonic for f in flows]
                record['journals'] = [w.journal.snapshot() for w in workers]
                if fail:
                    self.failed_journal = (workers[0].journal.path, flows[0].authority.grant)
                self.records.append(record)

    def source(self):
        name = 'paced-model-radio' if self.radio else 'loopback-tcp'
        # Bootstrap preference intentionally favors radio. Verified timing must
        # reverse this preference after fresh IP admission and verification.
        rate = 10000 if self.radio else 1000
        hop = swarm.Hop('C', 'B', name, name, rate)
        return swarm.Source(swarm.Route(name, self.generation, (hop,)),
            resource.BlockReader(self.exchange, max_block=16384), request_timeout=90, max_batch=1)


def test_tcp_loss_fresh_radio_resume_and_return_to_verified_faster_tcp(pack, tmp_path):
    directory, trust, digest, manifest = pack
    cache = ll.VerifiedCache(directory, trust, digest)
    server = resource.BlockServer(cache.read)
    clock = Clock()
    epoch, mono = clock()
    expires = epoch + 30000000
    deadline = mono / 1e6 + 30.1
    serials = itertools.count(1)
    old = FreshCarrier(tmp_path / 'ip-old', server, clock, expires, deadline, serials)
    radio = FreshCarrier(tmp_path / 'radio-new', server, clock, expires, deadline, serials, radio=True)
    returned = FreshCarrier(tmp_path / 'ip-returned', server, clock, expires, deadline, serials, generation=2)
    ip_source, radio_source, returned_source = old.source(), radio.source(), returned.source()
    destination = tmp_path / 'recipient'

    def fetch(sources, max_chunks=None):
        return ll.fetch_sources(sources, digest, destination, trust, max_chunks=max_chunks,
                                timeout=deadline - time.monotonic(), max_workers=1)

    initial = fetch([ip_source], 2)
    assert (initial['downloaded'], initial['reused']) == (2, 0)
    verified_before = {p.name: p.read_bytes() for p in (destination / 'chunks').iterdir()}
    trained = ip_source._samples
    old.disconnect_next_chunk = True
    with pytest.raises(ValueError, match='no peer supplied valid chunk batch'):
        fetch([ip_source], 1)
    assert ip_source._samples == trained
    failed = next(r for r in old.records if r['failed'])
    assert failed['journals'][0]['attempts'] > 0
    assert failed['journals'][0]['reserved_stream_bytes'] > 0
    assert {p.name: p.read_bytes() for p in (destination / 'chunks').iterdir()} == verified_before

    resumed = fetch([radio_source], 2)
    assert (resumed['downloaded'], resumed['reused']) == (2, 2)
    assert returned_source._samples == 0  # A new route generation has no inherited measurements.
    reprobed = fetch([returned_source], 1)
    assert (reprobed['downloaded'], reprobed['reused']) == (1, 4)
    assert returned_source._samples == 1 and radio_source._samples == 2
    now = time.monotonic()
    assert returned_source.seconds(4096, now) < radio_source.seconds(4096, now)
    final = fetch([radio_source, returned_source])
    assert final['complete'] and (final['downloaded'], final['reused']) == (3, 5)
    assert final['chunks_by_peer'] == {radio_source.name: 0, returned_source.name: 3}
    assert radio_source._samples == 2 and returned_source._samples == 4

    records = old.records + radio.records + returned.records
    sessions = [s for r in records for s in r['sessions']]
    assert len(sessions) == len(set(sessions))
    assert all(r['expiries'] == [expires] * 4 for r in records)
    assert all(d <= deadline for r in records for d in r['deadlines'])
    assert all('RF_DONE' not in j['outcomes'] for r in old.records + returned.records for j in r['journals'])
    assert all(j['reserved_estimated_airtime_us'] > 0 for r in radio.records for j in r['journals'])
    assert all('/chunks/' not in r['path'] or r['path'].split('/')[-1] not in verified_before
               for r in radio.records + returned.records)
    # Reopen the original durable journal after every subsequent admission.
    # No new authority or wire session is created for the failed grant.
    journal = IpJournal(*old.failed_journal)
    try:
        assert journal.snapshot() == failed['journals'][0]
    finally:
        journal.close()
    kp_cache = ll.VerifiedCache(destination, trust, digest)
    assert all(kp_cache.piece(e['sha256']) == cache.piece(e['sha256']) for e in manifest['chunks'])
