from tests.radio_profiles import SOURCE_ADMISSION
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Local composed controls; the worker is a fault-injected radio model, not RF."""
from collections import deque
from dataclasses import asdict
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import secrets

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority, revoke
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.objects import VerifiedChunks
from meshticulum.bench.serial_protocol import Outcome, RadioProfile, RawReceive, TxEvidence
from meshticulum.bench.serial_worker import AttemptResult
from tests.radio_profiles import MODES, profile_for
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import HEADER, FrameKind, SessionCipher, WireError
from meshticulum.scheduler.budget import BudgetPolicy, Ledger

BOOT = '21f9c178-6090-40d9-9359-ecf1c7b950b1'
PROFILE = RadioProfile(921375000, 125000, 8, 5, 0, 8, 0x42, 90000, 256, 60000000)


class Clock:
    epoch = 1000000000
    mono = 100000000

    def __call__(self): return self.epoch, self.mono
    def advance(self, us): self.epoch += us; self.mono += us


class ModelWorker:
    def __init__(self, journal, clock):
        self.journal, self.clock = journal, clock
        self.fenced = False
        self.incoming, self.sent = deque(), []
        self.peer = None
        self.drop_kind = None
        self.corrupt_once = False
        self.replay_once = False
        self.owned = True

    def transmit(self, packet):
        request = self.journal.reserve(packet, 200000)
        self.journal.settle(request, packet, 'RF_DONE', 'source_chip_model')
        self.sent.append(packet)
        kind = FrameKind(HEADER.unpack(packet[:HEADER.size])[2])
        if kind is self.drop_kind:
            self.drop_kind = None
        else:
            if self.corrupt_once:
                self.peer.incoming.append(packet[:-1] + bytes([packet[-1] ^ 1]))
                self.corrupt_once = False
            else:
                self.peer.incoming.append(packet)
                if self.replay_once:
                    self.peer.incoming.append(packet)
                    self.replay_once = False
        return AttemptResult(request, 'RF_DONE', 200000, None)

    def receive(self, timeout=0):
        if self.incoming:
            return self.clock()[1] / 1000000, RawReceive(-70.0, 3.0, self.incoming.popleft())


def provision(path, unit, payload, clock, profile=PROFILE):
    path.mkdir(mode=0o700)
    peers = [{'chain_id': 'local-bench-test', 'agent_id': x * 64} for x in ('a', 'b')]
    value = {'schema': 'meshticulum.bench-run.v1', 'unit': unit, 'host_boot_id': BOOT,
        'local_peer': peers[unit == 'unit-b'], 'initiator': peers[0], 'responder': peers[1],
        'session_id': '11' * 16, 'generation': '22' * 16,
        'issued_epoch_us': clock()[0] - 1000000, 'expires_epoch_us': clock()[0] + 180000000,
        'profile': asdict(profile), 'sender_unit': 'unit-a', 'country': 'MY',
        'antenna_confirmed': True, 'max_frames': 512,
        'object': {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload),
                   'chunk_index': 0, 'object_id': hashlib.sha256(payload).hexdigest()}}
    for name, data in [('grant.json', json.dumps(value).encode()), ('secret.bin', b'K' * 32),
                       ('active', b'active\n')]:
        fd = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as f: f.write(data)
    return value


@pytest.fixture(params=list(MODES))
def pair(tmp_path, request):
    clock = Clock()
    data = bytes(range(256)) * 4
    opened = []
    flows = []
    workers = []
    for unit in ('unit-a', 'unit-b'):
        path = tmp_path / unit
        provision(path, unit, data, clock, profile_for(request.param))
        journal = AttemptJournal(path / 'attempts.db', binding=bytes(32), max_attempts=256,
            max_airtime_us=60000000, create=True, request_clock=lambda: clock()[0])
        worker = ModelWorker(journal, clock)
        authority = RunAuthority(path, radio_admission=SOURCE_ADMISSION, expected_unit=unit, host_boot_id=BOOT,
            clock=clock, owner_probe=lambda w=worker: w.owned)
        store = SessionStore(path / 'wire.db', authority.grant, authority.local_peer,
                             boot_id=BOOT, clock=clock, create=True)
        cipher = SessionCipher(authority.grant, authority.local_peer, store)
        ledger = Ledger(BudgetPolicy(180000000, 60000000, 120000000, 1000000,
                                     quantization_us=1), authority.ledger_epoch, clock()[1])
        endpoint = BenchFlow(authority=authority, worker=worker, cipher=cipher,
            journal=journal, flow_key=authority.flow_key, contract=authority.contract,
            profile=authority.carrier_profile, ledger=ledger, scheduler_epoch=authority.scheduler_epoch,
            broker_epoch=authority.broker_epoch, sends=unit == 'unit-a', clock=clock)
        opened.append((endpoint, store, authority, journal))
        flows.append(endpoint); workers.append(worker)
    workers[0].peer, workers[1].peer = workers[1], workers[0]
    try:
        yield data, clock, flows, workers
    finally:
        for endpoint, store, authority, journal in opened:
            endpoint.close(); store.close(); authority.close(); journal.close()


def finish(pair):
    data, clock, (sender, receiver), workers = pair
    for _ in range(1600):
        sender.step(); receiver.step()
        clock.advance(50000)
        if sender.flow.complete and receiver.flow.complete:
            assert bytes(receiver.received) == data
            return
    raise AssertionError((sender.snapshot(), receiver.snapshot()))


def test_real_composition_waits_for_authenticated_startup_credit(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data)
    for _ in range(5):
        sender.step(); clock.advance(50000)
    assert not workers[0].sent
    finish(pair)
    assert workers[1].sent and FrameKind(HEADER.unpack(workers[1].sent[0][:HEADER.size])[2]) is FrameKind.ACK
    assert sender.flow.acked_frames() > 0
    assert sender.snapshot()['complete']


def test_tx_events_preserve_driver_and_host_worker_timings_separately(pair):
    data, clock, (sender, receiver), workers = pair
    original = workers[0].transmit

    def measured(packet):
        result = original(packet)
        evidence = TxEvidence(Outcome.RF_DONE, estimated_airtime_us=12000,
                              driver_elapsed_us=9000, irq_flags=1, driver_error=0)
        return AttemptResult(result.request, result.outcome, result.reserved_estimated_us,
                             evidence, 'coprocessor_monotonic')

    workers[0].transmit = measured
    sender.send(data)
    finish(pair)
    events = [event for event in sender.snapshot()['events']
              if event['event'] == 'tx' and event['outer_kind'] == 'DATA']
    assert events
    assert all(type(event['worker_call_elapsed_us']) is int
               and event['worker_call_elapsed_us'] >= 0 for event in events)
    assert all(event['driver_estimated_airtime_us'] == 12000
               and event['driver_elapsed_us'] == 9000
               and event['driver_elapsed_clock_basis'] == 'coprocessor_monotonic'
               and event['driver_irq_flags'] == 1 and event['driver_error'] == 0
               for event in events)


def test_consumed_delivery_uses_one_ack_before_persistence_interval(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data)
    receiver.step()  # real authenticated startup credit
    sender.step()    # one data packet
    clock.advance(500000)
    receiver.step()
    assert len(receiver.received) == 193
    if receiver.burst_frames > 1:
        clock.advance(receiver.burst_quiet_us + 1)
        receiver.step()
    sent = len(workers[1].sent)
    assert sent == 2  # startup and one data/credit acknowledgment
    clock.advance(450000)
    receiver.step()  # no new data, loss, expiry or persistence event
    assert len(workers[1].sent) == sent


def test_ack_progress_ends_reverse_wait_but_corrupt_ack_cannot(pair):
    data, clock, (sender, receiver), workers = pair
    if sender.burst_frames > 1:
        pytest.skip('Per-packet reverse wait is only used by the baseline profile')
    sender.send(data)
    receiver.step()
    # ModelWorker has zero transmission duration. Advance the receiver out of
    # its startup ACK guard before modeling arrival of the first DATA.
    clock.advance(receiver.receive_gap_us + 20000)
    sender.step()
    gap_end = sender.next_send_us
    clock.advance(20000)
    receiver.step()
    assert workers[0].incoming
    ack = workers[0].incoming.popleft()
    workers[0].incoming.append(ack[:-1] + bytes([ack[-1] ^ 1]))
    sender.step()
    assert sender.next_send_us == gap_end
    workers[0].incoming.append(ack)
    sender.step()
    # Successful ACK admits the next frame inside the old conservative wait.
    assert sender.flow.acked_frames() == 1 and len(workers[0].sent) == 2
    assert clock()[1] < gap_end


def test_fast_burst_is_bounded_by_credit_and_gets_one_current_ack(pair):
    data, clock, (sender, receiver), workers = pair
    if sender.burst_frames == 1:
        pytest.skip('Burst policy is only enabled on explicitly faster profiles')
    sender.send(data); receiver.step(); clock.advance(20000)
    for index in range(4):
        sender.step(); receiver.step(); clock.advance(20000)
        assert len(workers[1].sent) == (2 if index == 3 else 1)
    assert len(workers[0].sent) == 4
    # Hold the ACK at the receiver boundary: the sender cannot spend a fifth
    # frame until that actual authenticated credit/receipt reaches it.
    ack = workers[0].incoming.popleft()
    sender.step()
    assert len(workers[0].sent) == 4
    workers[0].incoming.append(ack); clock.advance(2000); sender.step()
    assert sender.flow.acked_frames() == 4 and len(workers[0].sent) == 5
    clock.advance(20000)
    finish(pair)


def test_lost_frame_in_fast_burst_gets_bounded_partial_ack_and_recovers(pair):
    data, clock, (sender, receiver), workers = pair
    if sender.burst_frames == 1:
        pytest.skip('Burst policy is only enabled on explicitly faster profiles')
    sender.send(data); receiver.step(); clock.advance(2000); workers[0].drop_kind = FrameKind.DATA
    for _ in range(4):
        sender.step(); receiver.step(); clock.advance(2000)
    assert len(workers[1].sent) == 1
    clock.advance(receiver.burst_quiet_us + 1); receiver.step()
    assert len(workers[1].sent) == 2  # partial SACK instead of deadlock
    clock.advance(20000)
    finish(pair)
    assert sender.flow.retransmits >= 1


def test_revocation_during_held_burst_ack_prevents_transmission(pair):
    data, clock, (sender, receiver), workers = pair
    if sender.burst_frames == 1:
        pytest.skip('Burst policy is only enabled on explicitly faster profiles')
    sender.send(data); receiver.step(); clock.advance(2000); sender.step(); receiver.step()
    assert receiver.burst_received == 1 and len(workers[1].sent) == 1
    revoke(receiver.authority.path)
    clock.advance(receiver.burst_quiet_us + 1)
    with pytest.raises(WireError, match='authority_not_live'):
        receiver.step()
    assert len(workers[1].sent) == 1


@pytest.mark.parametrize('mode', list(MODES))
def test_directional_costs_use_real_packet_bounds_and_reject_unlisted_modes(tmp_path, mode):
    from meshticulum.bench.serial_worker import estimated_airtime_us
    clock = Clock(); path = tmp_path / 'admitted'
    profile = profile_for(mode)
    provision(path, 'unit-a', b'payload', clock, profile)
    authority = RunAuthority(path, radio_admission=SOURCE_ADMISSION, expected_unit='unit-a', host_boot_id=BOOT,
                             clock=clock, owner_probe=lambda: True)
    try:
        cost = authority.carrier_profile
        assert cost.forward_seconds >= estimated_airtime_us(profile, 255) / 1e6
        assert cost.reverse_control_seconds >= estimated_airtime_us(profile, 70) / 1e6
        assert cost.reverse_control_seconds < cost.forward_seconds
        assert 0 < cost.min_rto_seconds <= cost.initial_rto_seconds <= cost.max_rto_seconds
    finally:
        authority.close()
    bad = tmp_path / 'unlisted'; plan = provision(bad, 'unit-a', b'payload', clock)
    plan['profile']['bandwidth_hz'] = 62500
    (bad / 'grant.json').write_text(json.dumps(plan))
    with pytest.raises(WireError, match='unsupported_admitted_bench_profile'):
        RunAuthority(bad, radio_admission=SOURCE_ADMISSION, expected_unit='unit-a', host_boot_id=BOOT, clock=clock, owner_probe=lambda: True)
    assert not (bad / 'started').exists()


@pytest.mark.parametrize('failure', ['lost_startup_ack', 'corrupt_data', 'replay_data'])
def test_composed_authenticated_loss_and_replay_recovery(pair, failure):
    data, clock, (sender, receiver), workers = pair
    if failure == 'lost_startup_ack': workers[1].drop_kind = FrameKind.ACK
    if failure == 'corrupt_data': workers[0].corrupt_once = True
    if failure == 'replay_data': workers[0].replay_once = True
    sender.send(data)
    finish(pair)
    if failure != 'lost_startup_ack':
        assert receiver.counts['rx_refused'] >= 1


def test_provisioned_revocation_stops_every_lane_before_worker_io(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data)
    revoke(receiver.authority.path)
    with pytest.raises(WireError, match='authority_not_live'):
        receiver.step()
    assert not workers[1].sent


def test_missing_actual_broker_owner_cannot_dispatch_startup_ack(pair):
    data, clock, (sender, receiver), workers = pair
    workers[1].owned = False
    receiver.step()
    assert not workers[1].sent
    assert receiver.link.last_result.reason == 'broker_authority_refused'


def test_used_grant_cannot_reinitialize_nonce_or_attempt_state(pair):
    authority = pair[2][0].authority
    with pytest.raises(FileExistsError):
        RunAuthority(authority.path, radio_admission=SOURCE_ADMISSION, expected_unit='unit-a', host_boot_id=BOOT,
                     clock=pair[1], owner_probe=lambda: True)


def test_changed_policy_invalidates_authority_without_widening_expiry(pair):
    authority = pair[2][0].authority
    path = authority.path / 'grant.json'
    plan = json.loads(path.read_text()); plan['expires_epoch_us'] += 1000000
    path.write_text(json.dumps(plan))
    assert not authority.live()


def test_boot_and_unconfirmed_antenna_refusals_do_not_consume_grant(tmp_path):
    clock = Clock(); directory = tmp_path / 'run'
    plan = provision(directory, 'unit-a', b'payload', clock)
    with pytest.raises(WireError, match='wrong_bench_host'):
        RunAuthority(directory, radio_admission=SOURCE_ADMISSION, expected_unit='unit-a', host_boot_id='11' * 16,
                     clock=clock, owner_probe=lambda: True)
    plan['antenna_confirmed'] = False
    (directory / 'grant.json').write_text(json.dumps(plan))
    with pytest.raises(WireError, match='antenna_unconfirmed'):
        RunAuthority(directory, radio_admission=SOURCE_ADMISSION, expected_unit='unit-a', host_boot_id=BOOT,
                     clock=clock, owner_probe=lambda: True)
    assert not (directory / 'started').exists()


def test_expiry_and_clock_rollback_refuse_more_work(pair):
    clock, authority = pair[1], pair[2][0].authority
    clock.epoch -= 1
    assert not authority.live()
    clock.epoch += 1
    clock.advance(181000000)
    assert not authority.live()


def test_all_sealed_frames_fit_radio_mtu_and_include_positive_restart_safe_counters(pair):
    data, clock, flows, workers = pair
    flows[0].send(data); finish(pair)
    for worker in workers:
        counters = [HEADER.unpack(packet[:HEADER.size])[-2] for packet in worker.sent]
        assert counters == list(range(1, len(counters) + 1))
        assert all(len(packet) <= 255 for packet in worker.sent)
    assert max(map(len, workers[0].sent)) == 255
    json.dumps(flows[0].snapshot())


def test_verified_chunks_resume_without_old_session_and_check_whole_object(tmp_path):
    data = b'public synthetic chunk one' + b'public synthetic chunk two'
    object_id = hashlib.sha256(data).hexdigest()
    chunks = [data[:26], data[26:]]
    descriptors = [{'object_id': object_id, 'chunk_index': i, 'bytes': len(chunk),
                    'sha256': hashlib.sha256(chunk).hexdigest()} for i, chunk in enumerate(chunks)]
    store = VerifiedChunks(tmp_path / 'objects')
    assert store.commit(descriptors[0], chunks[0])
    del store
    recovered = VerifiedChunks(tmp_path / 'objects')
    assert recovered.get(descriptors[0]) == chunks[0]
    assert recovered.get(descriptors[1]) is None
    assert not recovered.commit(descriptors[0], chunks[0])
    with pytest.raises(WireError, match='object_incomplete'):
        recovered.assemble(descriptors)
    recovered.commit(descriptors[1], chunks[1])
    assert recovered.assemble(descriptors) == data
    with pytest.raises(WireError, match='object_order'):
        recovered.assemble(list(reversed(descriptors)))
    (tmp_path / 'objects' / object_id / '0.bin').write_bytes(b'corrupted')
    with pytest.raises(WireError, match='stored_chunk_corrupt'):
        recovered.get(descriptors[0])


def test_partial_unverified_chunk_does_not_become_a_resume_receipt(tmp_path):
    data = b'synthetic payload'
    descriptor = {'object_id': hashlib.sha256(data).hexdigest(), 'chunk_index': 0,
                  'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    store = VerifiedChunks(tmp_path / 'objects')
    assert store.get(descriptor) is None
    orphan = tmp_path / 'objects' / descriptor['object_id'] / '0.bin'
    orphan.write_bytes(data); orphan.chmod(0o600)
    assert store.get(descriptor) is None
    assert store.commit(descriptor, data)
    assert store.get(descriptor) == data
    bad = dict(descriptor, sha256='a' * 64)
    with pytest.raises(WireError, match='received_chunk_mismatch'):
        store.commit(bad, data)


def test_dispatch_holds_real_revocation_lock_until_worker_returns(pair):
    receiver = pair[2][1]
    worker = pair[3][1]
    original = worker.transmit
    checked = []
    def while_held(packet):
        fd = os.open(receiver.authority.path / 'active', os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checked.append(True)
        finally:
            os.close(fd)
        return original(packet)
    worker.transmit = while_held
    receiver.step()
    assert checked
    revoke(receiver.authority.path)
    assert not receiver.authority.live()


def test_settled_handle_objects_are_retained_within_explicit_run_bound(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data); finish(pair)
    for flow in (sender, receiver):
        handles = flow.owner.retained
        assert len(handles) == len(flow.owner._settled_handles)
        assert {id(handle) for handle in handles} == flow.owner._settled_handles
        assert len(handles) < 1024


def test_four_physical_frames_fence_same_pump_until_real_ack(pair):
    data, clock, (sender, receiver), workers = pair
    if sender.burst_frames == 1:
        pytest.skip('Fast-profile arbitration only')
    sender.send(data); receiver.step(); clock.advance(20000)
    for _ in range(4):
        sender.step(); clock.advance(20000)
    assert len(workers[0].sent) == 4 and sender.owner.no_send
    hold = sender.next_send_us
    sender.step()
    assert len(workers[0].sent) == 4 and sender.next_send_us == hold
    receiver.step(); sender.step()
    assert sender.flow.acked_frames() == 4 and len(workers[0].sent) == 5


def test_unknown_attempt_is_charged_once_then_immediately_fences(pair):
    data, clock, (sender, receiver), workers = pair
    sender.send(data); receiver.step(); clock.advance(20000)
    worker = workers[0]
    def unknown(packet):
        request = worker.journal.reserve(packet, 200000)
        worker.journal.settle(request, packet, 'UNKNOWN', 'model_missing_driver_evidence')
        worker.fenced = True
        return AttemptResult(request, 'UNKNOWN', 200000, None)
    worker.transmit = unknown
    with pytest.raises(WireError, match='worker_run_fenced'):
        sender.step()
    assert worker.journal.snapshot()['attempts'] == 1 and sender.owner.no_send


@pytest.mark.parametrize('fault', ['invalid_total', 'invalid_fin', 'out_of_span', 'foreign_flow', 'stale_generation'])
def test_authenticated_refused_data_cannot_open_or_extend_ack_quiet(pair, fault):
    from meshticulum.flow.flow import build_data_frame
    data, clock, (sender, receiver), workers = pair
    if receiver.burst_frames == 1:
        pytest.skip('Fast-profile arbitration only')
    values = dict(flow_id=receiver.flow.flow_id, generation=receiver.flow.generation,
                  seq=0, total=2, last=False, payload=b'authentic_but_refused')
    if fault == 'invalid_total': values['total'] = 129
    elif fault == 'invalid_fin': values['last'] = True
    elif fault == 'out_of_span': values.update(seq=3, total=2)
    elif fault == 'foreign_flow': values['flow_id'] += 1
    elif fault == 'stale_generation': values['generation'] += 1
    packet = sender.cipher.seal(build_data_frame(**values), FrameKind.DATA,
                                authorize=sender.authority.live)
    workers[1].incoming.append(packet); receiver.step()
    assert receiver.burst_received == receiver.burst_started_us == receiver.ack_due_us == 0


def held_sender(pair):
    data,clock,(sender,receiver),workers=pair
    if sender.burst_frames==1:pytest.skip('Fast-profile control window only')
    sender.send(data);receiver.step();clock.advance(20000)
    for _ in range(4):sender.step();clock.advance(20000)
    assert sender.owner.no_send and len(workers[0].sent)==4
    return clock,sender,receiver,workers


def test_cancel_during_control_hold_never_resumes_any_lane(pair):
    clock,sender,receiver,workers=held_sender(pair)
    sender.flow.cancel();clock.advance(sender.sender_control_hold_us+1)
    with pytest.raises(WireError,match='reliable_flow_cancelled'):sender.step()
    assert len(workers[0].sent)==4 and not sender.flow.complete


def test_original_expiry_during_control_hold_refuses_before_worker_io(pair):
    clock,sender,receiver,workers=held_sender(pair)
    clock.advance(181000000)
    with pytest.raises(WireError,match='authority_not_live'):sender.step()
    assert len(workers[0].sent)==4


def test_authenticated_nonprogress_ACK_cannot_release_control_window(pair):
    from meshticulum.flow.flow import build_ack_frame
    clock,sender,receiver,workers=held_sender(pair)
    hold=sender.next_send_us
    inner=build_ack_frame(flow_id=sender.flow.flow_id,generation=sender.flow.generation,
        ack_seq=0,credit=4,sack=0,grant_revision=1)
    packet=receiver.cipher.seal(inner,FrameKind.ACK,authorize=receiver.authority.live)
    workers[0].incoming.append(packet);sender.step()
    assert sender.next_send_us==hold and sender.flow.acked_frames()==0
    assert len(workers[0].sent)==4
