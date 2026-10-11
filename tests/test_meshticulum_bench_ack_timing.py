"""Real flow/AEAD composition over deterministic scheduled half-duplex radios.

The clocks model blocking driver/USB returns independently. These are source
experiments, not RF throughput or hardware qualification.
"""
from collections import deque
import hashlib

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.serial_protocol import RawReceive
from meshticulum.bench.serial_worker import AttemptResult, estimated_airtime_us
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import HEADER, FrameKind, SessionCipher
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.radio_profiles import SOURCE_ADMISSION, profile_for
from tests.test_meshticulum_bench_runtime import BOOT, provision

class Clock:
    def __init__(self):
        self.mono = 100000000
    def __call__(self):
        return 1000000000 + self.mono - 100000000, self.mono
    def advance(self, us):
        self.mono += us


class Air:
    def __init__(self):
        self.frames = []
        self.collisions = 0
        self.drop_once = False
    def emit(self, worker, packet, start, end):
        frame = dict(worker=worker, packet=packet, start=start, end=end, lost=False, delivered=False)
        if self.drop_once and FrameKind(HEADER.unpack(packet[:HEADER.size])[2]) is FrameKind.DATA:
            frame['lost'] = True
            self.drop_once = False
        for old in self.frames:
            if old['worker'] is not worker and max(start, old['start']) < min(end, old['end']):
                old['lost'] = frame['lost'] = True
                self.collisions += 1
        self.frames.append(frame)
    def receive(self, worker):
        for frame in self.frames:
            if frame['worker'] is worker or frame['delivered'] or frame['end'] + 10000 > worker.clock.mono:
                continue
            frame['delivered'] = True
            if not frame['lost']:
                return frame['end'] / 1000000, RawReceive(-40.0, 12.0, frame['packet'])


class HalfDuplexWorker:
    def __init__(self, journal, clock, profile, air, delays):
        self.journal, self.clock, self.profile, self.air = journal, clock, profile, air
        self.delays = delays
        self.sent = []
        self.fenced = False
        self.owned = True
    def transmit(self, packet):
        estimate = estimated_airtime_us(self.profile, len(packet))
        reservation = (estimate * 110 + 99) // 100 + 1000
        request = self.journal.reserve(packet, reservation)
        kind = FrameKind(HEADER.unpack(packet[:HEADER.size])[2])
        start = self.clock.mono + 10000
        self.air.emit(self, packet, start, start + estimate)
        count = len(self.sent)
        self.sent.append(packet)
        delay = self.delays[count % len(self.delays)] if kind is FrameKind.DATA else 10000
        self.clock.advance(10000 + estimate + delay)
        self.journal.settle(request, packet, 'RF_DONE', 'deterministic_half_duplex_model')
        return AttemptResult(request, 'RF_DONE', reservation, None)
    def receive(self, timeout=0):
        return self.air.receive(self)


def exercise(tmp_path, delays, *, loss=False, size=16384):
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = bytes(range(256)) * (size // 256)
    profile = profile_for('sf7-500')
    air = Air()
    opened, endpoints, workers, clocks = [], [], [], []
    try:
        for unit in ('unit-a', 'unit-b'):
            clock = Clock(); path = tmp_path / unit
            provision(path, unit, data, clock, profile)
            journal = AttemptJournal(path / 'attempts.db', binding=bytes(32), max_attempts=256,
                max_airtime_us=60000000, create=True, request_clock=lambda c=clock: c()[0])
            worker = HalfDuplexWorker(journal, clock, profile, air, delays)
            authority = RunAuthority(path, radio_admission=SOURCE_ADMISSION, expected_unit=unit,
                host_boot_id=BOOT, clock=clock, owner_probe=lambda w=worker: w.owned)
            store = SessionStore(path / 'wire.db', authority.grant, authority.local_peer,
                                 boot_id=BOOT, clock=clock, create=True)
            cipher = SessionCipher(authority.grant, authority.local_peer, store)
            ledger = Ledger(BudgetPolicy(180000000, 60000000, 120000000, 1000000,
                quantization_us=1), authority.ledger_epoch, clock()[1])
            endpoint = BenchFlow(authority=authority, worker=worker, cipher=cipher, journal=journal,
                flow_key=authority.flow_key, contract=authority.contract, profile=authority.carrier_profile,
                ledger=ledger, scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
                sends=unit == 'unit-a', clock=clock)
            opened.append((endpoint, store, authority, journal))
            endpoints.append(endpoint); workers.append(worker); clocks.append(clock)
        sender, receiver = endpoints
        sender.send(data)
        air.drop_once = loss
        for _ in range(50000):
            index = min(range(2), key=lambda i: clocks[i].mono)
            endpoints[index].step()
            clocks[index].advance(5000)
            if sender.flow.complete and receiver.flow.complete:
                assert bytes(receiver.received) == data
                elapsed = clocks[0].mono - 100000000
                return dict(elapsed_us=elapsed, useful_Bps=size * 1e6 / elapsed,
                    retries=sender.flow.retransmits, collisions=air.collisions,
                    sender_attempts=workers[0].journal.snapshot()['attempts'],
                    receiver_attempts=workers[1].journal.snapshot()['attempts'],
                    acked=sender.flow.acked_frames(), receiver_bytes=len(receiver.received),
                    fenced=any(w.journal.snapshot()['fenced'] for w in workers))
        raise AssertionError('model did not complete under original bounded resources')
    finally:
        for endpoint, store, authority, journal in opened:
            endpoint.close(); store.close(); authority.close(); journal.close()


@pytest.mark.parametrize('delays', [(40000, 40000, 180000, 40000),
                                  (45000, 45000, 200000, 45000),
                                  (40000,), (45000, 45000, 260000, 45000)])
def test_driver_gap_control_windows_complete_without_collisions(tmp_path, delays):
    result = exercise(tmp_path, delays)
    assert result['collisions'] == result['retries'] == 0
    assert result['sender_attempts'] == result['acked'] == 85
    assert result['receiver_bytes'] == 16384 and not result['fenced']
    assert result['elapsed_us'] < 20000000


def test_lost_data_still_recovers_without_inferred_credit(tmp_path):
    result = exercise(tmp_path, (40000,), loss=True)
    assert result['retries'] >= 1 and result['acked'] == 85 and not result['fenced']
