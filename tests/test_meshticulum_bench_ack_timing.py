"""Real flow/AEAD composition over deterministic scheduled half-duplex radios.

The clocks model blocking driver/USB returns independently. These are source
experiments, not RF throughput or hardware qualification.
"""
from collections import deque
from dataclasses import replace
import hashlib

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.serial_protocol import RawReceive, RadioProfile
from meshticulum.bench.serial_worker import AttemptResult, estimated_airtime_us
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import HEADER, FrameKind, SessionCipher
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.radio_profiles import profile_for
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
        self.drop_ack_number = None
        self.ack_count = 0
    def emit(self, worker, packet, start, end):
        frame = dict(worker=worker, packet=packet, start=start, end=end, lost=False, delivered=False)
        if self.drop_once and FrameKind(HEADER.unpack(packet[:HEADER.size])[2]) is FrameKind.DATA:
            frame['lost'] = True
            self.drop_once = False
        if FrameKind(HEADER.unpack(packet[:HEADER.size])[2]) is FrameKind.ACK:
            if self.ack_count == self.drop_ack_number:
                frame['lost'] = True
            self.ack_count += 1
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
    def __init__(self, journal, clock, profile, air, delays, host_step_us=0):
        self.journal, self.clock, self.profile, self.air = journal, clock, profile, air
        self.delays = delays
        self.host_step_us = host_step_us
        self.sent = []
        self.ack_bursts = []
        self.fenced = False
        self.owned = True
    def transmit(self, packet):
        estimate = estimated_airtime_us(self.profile, len(packet))
        reservation = (estimate * 110 + 99) // 100 + 1000
        request = self.journal.reserve(packet, reservation)
        kind = FrameKind(HEADER.unpack(packet[:HEADER.size])[2])
        if kind is FrameKind.ACK and hasattr(self, 'endpoint'):
            endpoint = self.endpoint
            self.ack_bursts.append({'received': endpoint.burst_received,
                'since_first_us': self.clock.mono - endpoint.burst_started_us,
                'since_last_us': self.clock.mono - endpoint.last_activity_us,
                'due_us': endpoint.ack_due_us, 'started_us': self.clock.mono})
        start = self.clock.mono + 10000
        self.air.emit(self, packet, start, start + estimate)
        count = len(self.sent)
        self.sent.append(packet)
        delay = self.delays[count % len(self.delays)] if kind is FrameKind.DATA else 10000
        self.clock.advance(10000 + estimate + delay)
        self.journal.settle(request, packet, 'RF_DONE', 'deterministic_half_duplex_model')
        return AttemptResult(request, 'RF_DONE', reservation, None)
    def receive(self, timeout=0):
        self.clock.advance(self.host_step_us)
        return self.air.receive(self)


def exercise(tmp_path, delays, *, loss=False, size=16384, drop_ack_number=None,
             profile=None, host_step_us=0, legacy_rto=False, legacy_max_hold=False):
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = bytes(range(256)) * (size // 256)
    profile = profile or profile_for('sf7-500')
    admission = RadioAdmission('MY', ((profile.frequency_hz, profile.bandwidth_hz,
        profile.spreading_factor, profile.coding_rate, profile.chip_power_dbm,
        profile.preamble, profile.sync_word),), 10000, 180000, 256, 60000000)
    air = Air()
    air.drop_ack_number = drop_ack_number
    opened, endpoints, workers, clocks = [], [], [], []
    try:
        for unit in ('unit-a', 'unit-b'):
            clock = Clock(); path = tmp_path / unit
            provision(path, unit, data, clock, profile)
            journal = AttemptJournal(path / 'attempts.db', binding=bytes(32), max_attempts=256,
                max_airtime_us=60000000, create=True, request_clock=lambda c=clock: c()[0])
            worker = HalfDuplexWorker(journal, clock, profile, air, delays, host_step_us)
            authority = RunAuthority(path, radio_admission=admission, expected_unit=unit,
                host_boot_id=BOOT, clock=clock, owner_probe=lambda w=worker: w.owned)
            if legacy_rto:
                # Reproduce the prior supported profile in this source control;
                # no frozen-flow retry state or permission is replaced.
                authority.carrier_profile = replace(authority.carrier_profile,
                    initial_rto_seconds=.5, min_rto_seconds=.269472)
            store = SessionStore(path / 'wire.db', authority.grant, authority.local_peer,
                                 boot_id=BOOT, clock=clock, create=True)
            cipher = SessionCipher(authority.grant, authority.local_peer, store)
            ledger = Ledger(BudgetPolicy(180000000, 60000000, 120000000, 1000000,
                quantization_us=1), authority.ledger_epoch, clock()[1])
            endpoint = BenchFlow(authority=authority, worker=worker, cipher=cipher, journal=journal,
                flow_key=authority.flow_key, contract=authority.contract, profile=authority.carrier_profile,
                ledger=ledger, scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
                sends=unit == 'unit-a', clock=clock)
            worker.endpoint = endpoint
            if legacy_max_hold:
                endpoint.burst_max_hold_us = 500000
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
                    ack_bursts=workers[1].ack_bursts,
                    policy=sender.snapshot()['timing_policy'],
                    initial_rto=sender.flow.limits.initial_rto_s,
                    minimum_rto=sender.flow.limits.min_rto_s,
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


@pytest.mark.parametrize('delays', [(40000,), (45000,)])
def test_lost_cumulative_ack_is_repeated_before_data_resumes(tmp_path, delays):
    # Startup ACK is zero; ACK fourteen states receipt through DATA55. Losing
    # it forces the real frozen flow to persist its canonical grant after500ms.
    result = exercise(tmp_path, delays, drop_ack_number=14)
    assert result['collisions'] == result['retries'] == 0
    assert result['sender_attempts'] == result['acked'] == 85
    assert result['receiver_attempts'] == 24
    assert result['receiver_bytes'] == 16384 and not result['fenced']
    assert result['elapsed_us'] < 15000000


SF5_P12 = RadioProfile(921375000, 500000, 5, 5, 0, 12, 0x42, 90000, 256, 60000000)


def sf5_native_cost_model(tmp_path, **faults):
    # DATA worker104ms and receive/check work70ms reproduce the slower native
    # host pacing. The air/clock is modeled; this is no RF speed measurement.
    return exercise(tmp_path, (60000,), size=8192, profile=SF5_P12,
                    host_step_us=70000, **faults)


def test_old_total_burst_cap_preempts_an_active_three_frame_train(tmp_path):
    old = sf5_native_cost_model(tmp_path / 'old', legacy_max_hold=True)
    interrupted = [row for row in old['ack_bursts'] if row['received'] == 3
                   and row['since_first_us'] >= 500000 and row['since_last_us'] < 250000]
    assert interrupted and old['collisions'] > 0 and old['retries'] > 0
    revised = sf5_native_cost_model(tmp_path / 'revised')
    assert revised['collisions'] == revised['retries'] == 0
    assert revised['sender_attempts'] == revised['acked'] == 43
    assert revised['receiver_bytes'] == 8192 and not revised['fenced']
    full = [row for row in revised['ack_bursts'] if row['received'] == 4]
    assert full and all(row['due_us'] <= row['started_us'] for row in full)
    assert all(row['since_last_us'] <= 70000 for row in full)
    assert revised['policy']['burst_quiet_us'] == 250000
    assert revised['policy']['burst_max_hold_us'] == 1000000
    assert revised['policy']['sender_control_hold_us'] == 1000000


def test_phy_only_retry_floor_retries_a_complete_lossless_train(tmp_path):
    old = sf5_native_cost_model(tmp_path / 'old', legacy_rto=True)
    assert old['collisions'] == 0 and old['retries'] > 0
    revised = sf5_native_cost_model(tmp_path / 'revised')
    assert revised['collisions'] == revised['retries'] == 0
    assert revised['initial_rto'] == revised['minimum_rto'] == 1.75


@pytest.mark.parametrize('faults,required_retries', [
    ({'loss': True}, 1),
    ({'drop_ack_number': 1}, 0),
    ({'loss': True, 'drop_ack_number': 1}, 1),
])
def test_partial_and_lost_bursts_keep_an_ack_opportunity(tmp_path, faults, required_retries):
    result = sf5_native_cost_model(tmp_path, **faults)
    assert result['collisions'] == 0 and result['retries'] == required_retries
    assert result['acked'] == 43 and result['receiver_bytes'] == 8192
    assert result['sender_attempts'] == 43 + required_retries and not result['fenced']
