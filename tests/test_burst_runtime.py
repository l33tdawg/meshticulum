"""Real flow/crypto/journal/serial composition with a wire-level radio model.

The radio peer/time are modeled. These checks do not qualify firmware or RF.
"""
from dataclasses import replace
import json
import math
import struct
from types import SimpleNamespace

import pytest

from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.burst_protocol import ACK, POLICY, policy_for_depth, policy_for_flow_window
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.serial_protocol import Decoder, Frame, Message
from meshticulum.bench.serial_worker import SerialWorker, journal_binding
from meshticulum.bench.state import SessionStore
from meshticulum.bench.tx_burst import Stage
from meshticulum.bench.wire import SessionCipher, WireError
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.radio_profiles import SOURCE_ADMISSION, profile_for
from tests.test_burst_worker import BOOT as RADIO_BOOT, VERSION, Channel
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision


class RadioChannel(Channel):
    """Exact USB records with explicitly stepped local RF/result delivery."""
    def __init__(self, clock, *, train_depth=4, profile=None):
        self.clock = clock
        self.peer = None
        self.packets, self.delivered, self.held = {}, set(), {}
        options = {} if profile is None else {'profile': profile}
        super().__init__(train_depth=train_depth, **options)
        self.on_data = lambda frame, packet: self.packets.__setitem__(frame.request, packet)

    @property
    def time(self):
        return self.clock()[1] / 1_000_000

    @time.setter
    def time(self, value):
        difference = round(value * 1_000_000) - self.clock()[1]
        assert difference >= 0
        self.clock.advance(difference)

    def deliver(self, packet):
        self.peer.offer(Frame(Message.RECEIVE, RADIO_BOOT, 0,
                              struct.pack('!hh', -730, 110) + packet))

    def write(self, raw, deadline):
        frame, = Decoder().feed(raw)
        super().write(raw, deadline)
        if frame.type is Message.ARM:
            self.arm_end = self.now_us() + int.from_bytes(frame.body[14:18], 'big') * 1000
        elif frame.type is Message.TRANSMIT:
            self.deliver(frame.body)
        elif frame.type is Message.BURST_ACK:
            _, request, _, _, _ = ACK.unpack(frame.body)
            self.packets.pop(request, None)

    def finish_packets(self, *, withhold=False):
        for request, packet in tuple(self.packets.items()):
            if request in self.delivered:
                continue
            self.event(request, Stage.STARTED)
            estimate = self.events[request][4]
            self.clock.advance(estimate + 5000)
            self.deliver(packet)
            self.delivered.add(request)
            if withhold:
                self.held[request] = self.now_us()
            else:
                self.event(request, Stage.RF_DONE)

    def release_held(self, stage=Stage.RF_DONE):
        for request, at in tuple(self.held.items()):
            self.event(request, stage, at_us=at)
            del self.held[request]


@pytest.fixture
def composition(tmp_path):
    opened = []
    def make(data=b'x' * 100, *, burst=True, train_depth=4, flow_window=None):
        clock = Clock()
        directory = tmp_path / str(len(opened))
        directory.mkdir(mode=0o700)
        endpoints, journals, authorities, stores, workers = [], [], [], [], []
        profile = profile_for('sf7-500')
        admission = SOURCE_ADMISSION
        if train_depth == 8:
            profile = replace(profile, spreading_factor=5, preamble=12)
            admission = RadioAdmission('MY', ((921375000, 500000, 5, 5, 0, 12, 0x42),),
                                       10000, 180000, 256, 60_000_000)
        channels = [RadioChannel(clock, train_depth=train_depth, profile=profile) for _ in range(2)]
        channels[0].peer, channels[1].peer = channels[1], channels[0]
        # Provision the shared original session times before serial negotiation
        # advances either clock. A later endpoint cannot mint a later grant.
        for unit in ('unit-a', 'unit-b'):
            path = directory / unit
            plan = provision(path, unit, data, clock, profile)
            if burst:
                plan['data_burst'] = (policy_for_depth(train_depth) if flow_window is None
                                      else policy_for_flow_window(flow_window))
                (path / 'grant.json').write_text(json.dumps(plan))
        for index, unit in enumerate(('unit-a', 'unit-b')):
            path = directory / unit
            authority = RunAuthority(path, expected_unit=unit, host_boot_id=BOOT,
                clock=clock, owner_probe=lambda: True, radio_admission=admission)
            worker = (BurstSerialWorker(channels[index], policy=authority.data_burst_policy)
                      if burst and index == 0 else SerialWorker(channels[index]))
            identity = worker.discover(expected_board_id=1, expected_version=VERSION)
            journal = AttemptJournal(path / 'attempts.db',
                binding=journal_binding(authority.grant.digest, identity, profile),
                max_attempts=profile.max_boot_attempts,
                max_airtime_us=profile.max_boot_estimated_airtime_us,
                create=True, request_clock=lambda: clock()[0])
            worker.arm(journal=journal, profile=profile, context_digest=authority.grant.digest,
                authorize=authority.application_live,
                authority_deadline=authority.deadlines.application_deadline_monotonic)
            store = SessionStore(path / 'wire.db', authority.grant, authority.local_peer,
                                 boot_id=BOOT, clock=clock, create=True)
            ledger = Ledger(BudgetPolicy(180000000, 60000000, 120000000, 1000000,
                                        quantization_us=1), authority.ledger_epoch, clock()[1])
            endpoint = BenchFlow(authority=authority, worker=worker,
                cipher=SessionCipher(authority.grant, authority.local_peer, store),
                journal=journal, flow_key=authority.flow_key, contract=authority.contract,
                profile=authority.carrier_profile, ledger=ledger,
                scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
                sends=index == 0, clock=clock)
            endpoints.append(endpoint); journals.append(journal); authorities.append(authority)
            stores.append(store); workers.append(worker)
        case = SimpleNamespace(clock=clock, data=data, sender=endpoints[0], receiver=endpoints[1],
            channels=channels, workers=workers, journals=journals, authorities=authorities, stores=stores)
        opened.append(case)
        return case
    yield make
    for case in opened:
        try:
            for endpoint in (case.sender, case.receiver):
                endpoint.close()
        finally:
            for store, authority, journal in zip(case.stores, case.authorities, case.journals):
                store.close(); authority.close(); journal.close()


def queue_one(case):
    case.sender.send(case.data)
    for _ in range(50):
        case.receiver.step()
        case.sender.step()
        if case.workers[0].pending_physical:
            return
        case.clock.advance(10_000)
    raise AssertionError('DATA did not reach exact native queue acceptance')


def test_full_serial_composition_queues_four_and_verifies_whole_plaintext(composition):
    case = composition(bytes(range(256)) * 5)
    case.sender.send(case.data)
    peak = 0
    for _ in range(500):
        sender_done = case.sender.step()
        receiver_done = case.receiver.step()
        count = len(case.workers[0].pending_physical)
        peak = max(peak, count)
        if count >= 4 or case.sender.flow.queued_bytes() == 0:
            case.channels[0].finish_packets()
        if sender_done and receiver_done:
            break
        case.clock.advance(10_000)
    else:
        raise AssertionError((case.sender.snapshot(), case.receiver.snapshot()))
    assert peak == 4
    assert bytes(case.receiver.received) == case.data
    assert case.sender.flow.retransmits == 0 and case.sender._completion_ready()
    assert not case.workers[0].pending_physical and not case.channels[0].events
    assert case.sender.counts['tx_QUEUED'] == case.sender.counts['tx_RF_DONE']
    assert all(event['worker_call_elapsed_us'] is None for event in case.sender.events
               if event['event'] == 'tx' and event['outer_kind'] == 'DATA')
    assert not case.journals[0].snapshot()['fenced']


def test_authenticated_peer_receipt_cannot_close_before_physical_drain(composition):
    case = composition()
    queue_one(case)
    handovers = case.sender.link.handed_over
    case.channels[0].finish_packets(withhold=True)
    case.receiver.step()
    assert not case.sender.step()
    assert case.sender.flow.complete and case.workers[0].pending_physical
    assert case.journals[0].db.execute('SELECT state FROM attempts').fetchall() == [('RESERVED',)]
    case.channels[0].release_held()
    assert case.sender.step()
    assert case.sender.link.handed_over == handovers == 1
    assert case.journals[0].db.execute('SELECT state FROM attempts').fetchall() == [('RF_DONE',)]
    assert not case.channels[0].events


def test_unknown_after_peer_receipt_fences_without_discarding_receipt(composition):
    case = composition()
    queue_one(case)
    case.channels[0].finish_packets(withhold=True)
    case.receiver.step(); assert not case.sender.step()
    case.channels[0].release_held(Stage.UNKNOWN)
    with pytest.raises(WireError, match='fenced'):
        case.sender.step()
    assert case.sender.flow.complete
    assert case.journals[0].snapshot()['fenced']
    assert case.journals[0].db.execute('SELECT state FROM attempts').fetchall() == [('UNKNOWN',)]


def test_pending_retry_is_refused_before_resealing_or_new_reservation(composition, monkeypatch):
    case = composition()
    queue_one(case)
    case.channels[0].finish_packets(withhold=True)
    before = case.journals[0].snapshot()['attempts']
    def no_reseal(*args, **kwargs):
        raise AssertionError('pending DATA consumed another nonce')
    monkeypatch.setattr(case.sender.cipher, 'seal', no_reseal)
    case.clock.advance(4_000_000)
    case.sender.step()
    assert case.sender.counts['pending_DATA_refused'] >= 1
    assert case.journals[0].snapshot()['attempts'] == before
    assert case.sender.flow.retransmits == 0
    case.channels[0].release_held()
    case.receiver.step()
    assert case.sender.step()
    assert case.sender.link.handed_over == 1


def test_close_cancels_queued_retry_even_after_late_authenticated_receipt(composition):
    case = composition()
    queue_one(case)
    case.channels[0].finish_packets()
    case.sender.step()
    case.clock.advance(4_000_000)
    case.sender.step()
    assert len([frame for frame in case.channels[0].writes if frame.type is Message.BURST_DATA]) == 2
    assert case.workers[0].pending_physical
    case.receiver.step()
    assert not case.sender.step() and case.sender.flow.complete
    case.sender.close()
    assert any(frame.type is Message.BURST_CANCEL for frame in case.channels[0].writes)
    assert len(case.channels[0].delivered) == 1
    assert case.journals[0].db.execute('SELECT state FROM attempts ORDER BY request').fetchall() == [
        ('RF_DONE',), ('NOT_SENT',)]
    assert case.journals[0].snapshot()['attempts'] == 2


def test_retry_horizon_does_not_enlarge_ack_coalescing_quiet_time(composition):
    case = composition()
    authority = case.authorities[1]
    assert authority.carrier_profile.initial_rto_seconds >= 2.22
    assert authority.source_initial_rto_seconds < authority.carrier_profile.initial_rto_seconds
    expected = max(case.receiver.burst_quiet_us,
                   math.ceil(authority.source_initial_rto_seconds * 500000))
    assert case.receiver.burst_quiet_us == expected
    assert case.receiver.burst_quiet_us < authority.carrier_profile.initial_rto_seconds * 500000


def test_legacy_grant_uses_only_synchronous_serial_path(composition):
    case = composition(b'legacy', burst=False)
    case.sender.send(case.data)
    for _ in range(80):
        sender_done = case.sender.step(); receiver_done = case.receiver.step()
        if sender_done and receiver_done:
            break
        case.clock.advance(10_000)
    assert sender_done and receiver_done and bytes(case.receiver.received) == case.data
    assert all(frame.type is not Message.BURST_DATA for frame in case.channels[0].writes)
