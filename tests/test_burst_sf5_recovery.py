"""Queued SF5 over deterministic independent hosts and half-duplex modeled air.

Real flow/crypto/FULL-WAL journals and serial workers run in two cooperative
host threads. Only radio/USB clocks and air are modeled; no device is opened.
Blocking USB or RF waits yield to the other host, unlike atomic train delivery.
These fault controls establish source behavior, not an RF performance result.
"""
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.burst_protocol import (
    ACK, CAPABILITY, CANCEL, CLOCK, DATA, EVENT, OPEN, POLICY, STATUS, TOKEN,
    INTER_PACKET_RX_US, REVERSE_LISTEN_US,
)
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, Outcome, RadioProfile
from meshticulum.bench.serial_worker import SerialWorker, estimated_airtime_us, journal_binding
from meshticulum.bench.state import SessionStore
from meshticulum.bench.tx_burst import START_GUARD_US, Stage
from meshticulum.bench.wire import FrameKind, SessionCipher
from meshticulum.flow.flow import parse_frame
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.test_burst_worker import BOOT as RADIO_BOOT, VERSION
from tests.test_meshticulum_bench_runtime import BOOT, provision


PROFILE = RadioProfile(921375000, 500000, 5, 5, 0, 12, 0x42, 60000, 256, 60_000_000)
ADMISSION = RadioAdmission('MY', ((921375000, 500000, 5, 5, 0, 12, 0x42),),
                           10000, 180000, 256, 60_000_000)
PAYLOAD = bytes(index % 251 for index in range(1665))  # 8*193 plus a short ninth DATA


class ModelStopped(Exception):
    pass


class Timeline:
    """One deterministic physical clock; independent hosts block separately."""
    def __init__(self):
        self.now = 100_000_000
        self.cv = threading.Condition()
        self.waiting = [(self.now, False), (self.now, False)]
        self.turn = None
        self.events, self.serial = [], 0
        self.stop_flows = self.cancelled = False
        self.switches = 0

    def clock(self):
        return 1_000_000_000 + self.now - 100_000_000, self.now

    def enter(self, index):
        with self.cv:
            while self.turn != index and not self.cancelled:
                self.cv.wait()
            if self.cancelled:
                raise ModelStopped()

    def sleep(self, index, us, *, incoming=False):
        with self.cv:
            assert self.turn == index
            self.waiting[index] = (self.now + max(1, us), incoming)
            self.turn = None
            self.cv.notify_all()
            while self.turn != index and not self.cancelled:
                self.cv.wait()
            if self.cancelled:
                raise ModelStopped()

    def wake(self, index):
        with self.cv:
            waiting = self.waiting[index]
            if waiting is not None and waiting[1]:
                self.waiting[index] = (self.now, True)

    def schedule(self, at, callback):
        self.serial += 1
        heapq.heappush(self.events, (at, self.serial, callback))

    def done(self, index):
        with self.cv:
            self.waiting[index] = None
            self.turn = None
            self.cv.notify_all()

    def run(self):
        real_deadline = time.monotonic() + 30
        try:
            with self.cv:
                while any(value is not None for value in self.waiting):
                    assert time.monotonic() < real_deadline, 'modeled hosts did not yield'
                    if self.turn is not None:
                        self.cv.wait(.05)
                        continue
                    due = [v[0] for v in self.waiting if v is not None]
                    if self.events:
                        due.append(self.events[0][0])
                    self.now = min(due)
                    assert self.now < 155_000_000, 'original modeled run bound exhausted'
                    while self.events and self.events[0][0] <= self.now:
                        _, _, callback = heapq.heappop(self.events)
                        callback()
                    available = [i for i, v in enumerate(self.waiting)
                                 if v is not None and v[0] <= self.now]
                    if available:
                        self.turn = min(available)
                        self.waiting[self.turn] = (self.now, False)
                        self.switches += 1
                        self.cv.notify_all()
        finally:
            with self.cv:
                self.cancelled = True
                self.cv.notify_all()


@dataclass
class AirFrame:
    channel: object
    packet: bytes
    inner: dict
    start: int
    end: int
    lost: bool = False
    collision: bool = False
    listening_at_start: bool = False


class Air:
    def __init__(self, timeline, *, drop_data=None, drop_final_ack=False):
        self.timeline = timeline
        self.drop_data = dict(drop_data or {})
        self.drop_final_ack = drop_final_ack
        self.frames, self.channels = [], []
        self.sealed = {}

    def transmit(self, channel, packet, start, finish):
        inner = self.sealed[packet]
        duration = estimated_airtime_us(PROFILE, len(packet))
        frame = AirFrame(channel, packet, inner, start, start + duration)
        if 'seq' in inner and self.drop_data.get(inner['seq'], 0):
            self.drop_data[inner['seq']] -= 1
            frame.lost = True
        elif 'ack_seq' in inner and inner['ack_seq'] == 9 and self.drop_final_ack:
            self.drop_final_ack = False
            frame.lost = True
        self.frames.append(frame)

        def begin():
            frame.listening_at_start = self.channels[1 - channel.index].armed
            for previous in self.frames:
                if (previous is not frame and previous.channel is not channel
                        and max(frame.start, previous.start) < min(frame.end, previous.end)):
                    frame.lost = previous.lost = True
                    frame.collision = previous.collision = True

        def end():
            peer = self.channels[1 - channel.index]
            if not frame.lost and frame.listening_at_start and peer.armed:
                peer.offer(Frame(Message.RECEIVE, RADIO_BOOT, 0,
                                 struct.pack('!hh', -600, 100) + packet))
            finish(frame.end)

        self.timeline.schedule(start, begin)
        self.timeline.schedule(frame.end, end)
        return frame


class RadioChannel:
    """Profile-aware keyless MCU record peer with asynchronous physical events."""
    def __init__(self, timeline, air, index):
        self.timeline, self.air, self.index = timeline, air, index
        self.inbound = bytearray()
        self.events, self.ack_receipts = {}, {}
        self.arm_request = self.arm_end = self.token = self.open_end = 0
        self.context = bytes(32)
        self.attempts = self.airtime = self.train_count = 0
        self.next_start_us = 0
        self.armed = self.fenced = False
        self.peak = 0
        self.writes = []

    def monotonic(self):
        return self.timeline.now / 1_000_000

    def now_us(self):
        return self.timeline.now

    def offer(self, frame):
        self.inbound.extend(frame.encode())
        self.timeline.wake(self.index)

    def event(self, request, stage, at):
        token, _, _, digest, estimate, size, end, _, _, _ = self.events[request]
        self.events[request] = (token, stage, at, digest, estimate, size, end,
                               estimate if stage is Stage.RF_DONE else 0,
                               1 if stage is Stage.RF_DONE else 0, 0)
        self.offer(Frame(Message.BURST_EVENT, RADIO_BOOT, request,
                         EVENT.pack(*self.events[request])))

    def write(self, raw, deadline):
        assert self.monotonic() < deadline
        # Source timing model for 64-byte USB chunks, not a host performance claim.
        self.timeline.sleep(self.index, 1000 + 2000 * (math.ceil(len(raw) / 64) - 1))
        frame, = Decoder().feed(raw)
        self.writes.append(frame)
        def reply(kind, body=b''):
            self.offer(Frame(kind, RADIO_BOOT, frame.request, body))
        if frame.type is Message.HELLO:
            reply(Message.HELLO_REPLY, struct.pack('!BBH', 1, 1, 255)
                  + (VERSION.encode() + b'\0').ljust(16, b'\0'))
        elif frame.type is Message.BURST_CAPABILITIES:
            reply(Message.BURST_CAPABILITIES_REPLY, CAPABILITY)
        elif frame.type is Message.ARM:
            self.armed = True
            self.arm_request, self.arm_end = frame.request, self.now_us() + PROFILE.lease_ms * 1000
            reply(Message.ARM_REPLY, frame.body)
        elif frame.type is Message.DISARM:
            assert all(event[1] in (Stage.RF_DONE, Stage.NOT_SENT, Stage.UNKNOWN)
                       for event in self.events.values()), 'model supports drained cleanup only'
            self.armed = False
            reply(Message.DISARM_REPLY, b'\0')
        elif frame.type is Message.BURST_CLOCK:
            reply(Message.BURST_CLOCK_REPLY, CLOCK.pack(self.now_us(), self.arm_request, self.arm_end))
        elif frame.type is Message.BURST_OPEN:
            self.context, arm, self.open_end = OPEN.unpack(frame.body)
            assert arm == self.arm_request and self.open_end <= self.arm_end
            self.token = frame.request
            reply(Message.BURST_OPEN_REPLY, frame.body)
        elif frame.type is Message.BURST_DATA:
            token, end, estimate, kind, size, digest = DATA.unpack_from(frame.body)
            packet = frame.body[DATA.size:]
            assert token == self.token and kind == 1 and size == len(packet)
            assert digest == hashlib.sha256(packet).digest()
            assert estimate == estimated_airtime_us(PROFILE, size)
            assert self.now_us() < end <= self.open_end
            self.attempts += 1
            self.airtime += estimate
            self.events[frame.request] = (token, Stage.QUEUED, self.now_us(), digest,
                                          estimate, size, end, 0, 0, 0)
            self.peak = max(self.peak, len(self.events))
            start = max(self.now_us() + START_GUARD_US, self.next_start_us)
            self.timeline.schedule(start, lambda: self.event(frame.request, Stage.STARTED, start))
            physical = self.air.transmit(self, packet, start,
                lambda at: self.event(frame.request, Stage.RF_DONE, at))
            self.train_count = (self.train_count + 1) % 4
            self.next_start_us = physical.end + (INTER_PACKET_RX_US if self.train_count else REVERSE_LISTEN_US)
            reply(Message.BURST_EVENT, EVENT.pack(*self.events[frame.request]))
        elif frame.type is Message.BURST_ACK:
            token, request, stage, at, digest = ACK.unpack(frame.body)
            if frame.request in self.ack_receipts:
                assert self.ack_receipts[frame.request] == frame.body
            else:
                held = self.events[request]
                assert (token, stage, at, digest) == (held[0], held[1], held[2], held[3])
                del self.events[request]
                self.ack_receipts[frame.request] = frame.body
            reply(Message.BURST_ACK_REPLY, frame.body)
        elif frame.type is Message.BURST_STATUS:
            reply(Message.BURST_STATUS_REPLY, STATUS.pack(self.token, self.context, self.arm_request,
                self.open_end, sum(v[1] not in (Stage.RF_DONE, Stage.NOT_SENT, Stage.UNKNOWN)
                for v in self.events.values()), len(self.events), 0, self.fenced, 0,
                self.now_us(), self.next_start_us, self.attempts, self.airtime))
        elif frame.type is Message.BURST_QUERY:
            assert TOKEN.unpack(frame.body) == (self.token,)
            reply(Message.BURST_EVENT, EVENT.pack(*self.events[frame.request]))
        elif frame.type is Message.BURST_CANCEL:
            # These recovery controls close only after physical settlement.
            # Do not claim to model pending cancellation/abort RF semantics.
            assert all(event[1] in (Stage.RF_DONE, Stage.NOT_SENT, Stage.UNKNOWN)
                       for event in self.events.values()), 'model supports drained cleanup only'
            self.fenced = True
            reply(Message.BURST_CANCEL_REPLY, CANCEL.pack(self.token, 1, 0))
        elif frame.type is Message.TRANSMIT:
            assert not self.events
            estimate = estimated_airtime_us(PROFILE, len(frame.body))
            self.attempts += 1
            self.airtime += estimate
            self.air.transmit(self, frame.body, self.now_us() + START_GUARD_US,
                lambda at: reply(Message.TX_RESULT, struct.pack('!B32sQQIh', Outcome.RF_DONE,
                    hashlib.sha256(frame.body).digest(), estimate, estimate, 1, 0)))
        else:
            raise AssertionError(frame.type)

    def read(self, timeout):
        self.timeline.sleep(self.index, 200 if self.inbound else max(100, round(timeout * 1_000_000)),
                            incoming=not self.inbound)
        raw = bytes(self.inbound[:4096])
        del self.inbound[:4096]
        return raw


def exercise(tmp_path, *, receiver_cap=64, drop_data=None, drop_final_ack=False):
    timeline = Timeline()
    air = Air(timeline, drop_data=drop_data, drop_final_ack=drop_final_ack)
    air.channels = [RadioChannel(timeline, air, i) for i in range(2)]
    results = [{}, {}]
    tmp_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    for unit in ('unit-a', 'unit-b'):
        plan = provision(tmp_path / unit, unit, PAYLOAD, timeline.clock, PROFILE)
        plan['data_burst'] = dict(POLICY)
        (tmp_path / unit / 'grant.json').write_text(json.dumps(plan))

    def host(index):
        opened = []
        endpoint = None
        try:
            timeline.enter(index)
            path, channel = tmp_path / ('unit-a' if index == 0 else 'unit-b'), air.channels[index]
            authority = RunAuthority(path, expected_unit=path.name, host_boot_id=BOOT,
                clock=timeline.clock, owner_probe=lambda: True, radio_admission=ADMISSION)
            opened.append(authority.close)
            worker = (BurstSerialWorker(channel, policy=authority.data_burst_policy)
                      if index == 0 else SerialWorker(channel))
            identity = worker.discover(expected_board_id=1, expected_version=VERSION)
            journal = AttemptJournal(path / 'attempts.db', create=True,
                binding=journal_binding(authority.grant.digest, identity, PROFILE),
                max_attempts=20 if index == 0 else receiver_cap, max_airtime_us=60_000_000,
                request_clock=lambda: timeline.clock()[0])
            opened.append(journal.close)
            worker.arm(journal=journal, profile=PROFILE, context_digest=authority.grant.digest,
                authorize=authority.application_live,
                authority_deadline=authority.deadlines.application_deadline_monotonic)
            opened.append(worker.disarm)
            store = SessionStore(path / 'wire.db', authority.grant, authority.local_peer,
                                 boot_id=BOOT, clock=timeline.clock, create=True)
            opened.extend((store.close, store.retire))
            cipher = SessionCipher(authority.grant, authority.local_peer, store)
            original_seal = cipher.seal
            def observe_seal(payload, kind, *, authorize):
                packet = original_seal(payload, kind, authorize=authorize)
                # Observation before/after sealing never opens incoming ciphertext
                # or changes the real replay/credit state.
                air.sealed[packet] = parse_frame(payload)
                return packet
            cipher.seal = observe_seal
            ledger = Ledger(BudgetPolicy(180_000_000, 60_000_000, 120_000_000, 1_000_000,
                                         quantization_us=1), authority.ledger_epoch, timeline.now)
            endpoint = BenchFlow(authority=authority, worker=worker, cipher=cipher, journal=journal,
                flow_key=authority.flow_key, contract=authority.contract, profile=authority.carrier_profile,
                ledger=ledger, scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
                sends=index == 0, clock=timeline.clock)
            opened.append(endpoint.close)
            if index == 0:
                endpoint.send(PAYLOAD)
            received_at = None
            while not timeline.stop_flows:
                complete = endpoint.step()
                if complete:
                    if index == 0:
                        timeline.stop_flows = True
                        break
                    received_at = received_at or timeline.now
                    if timeline.now >= received_at + 20_000_000:
                        break
                timeline.sleep(index, 5000)
            results[index].update(snapshot=endpoint.snapshot(), received=bytes(endpoint.received),
                                  received_at=received_at, complete=endpoint.flow.complete,
                                  settled_states=journal.db.execute('SELECT state,COUNT(*) FROM attempts GROUP BY state').fetchall())
        except BaseException as error:
            results[index]['error'] = error
            if endpoint is not None:
                results[index].update(snapshot=endpoint.snapshot(), received=bytes(endpoint.received),
                                      complete=endpoint.flow.complete)
            timeline.stop_flows = True
        finally:
            for close in reversed(opened):
                try:
                    close()
                except BaseException as error:
                    results[index].setdefault('cleanup_errors', []).append(error)
            timeline.done(index)

    threads = [threading.Thread(target=host, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    try:
        timeline.run()
    finally:
        for thread in threads:
            thread.join(2)
        assert not any(thread.is_alive() for thread in threads), 'modeled host thread leaked'
    return results, air


def assert_complete(results, air):
    assert all('error' not in r and not r.get('cleanup_errors') for r in results), results
    assert all(r['complete'] for r in results)
    assert results[0]['snapshot']['acked_frames'] == 9
    assert results[1]['received'] == PAYLOAD
    assert all(not r['snapshot']['attempt_journal']['fenced'] for r in results)
    assert results[0]['snapshot']['physical_queue']['pending'] == 0
    assert results[0]['snapshot']['physical_queue']['worker_pending'] == 0
    assert not air.channels[0].events
    assert not any(channel.armed for channel in air.channels)


def test_air_model_waits_for_rf_end_and_marks_half_duplex_overlap():
    timeline, received, terminals = Timeline(), [[], []], []
    air = Air(timeline)
    air.channels = [SimpleNamespace(index=i, armed=True, offer=received[i].append) for i in range(2)]
    first, second = b'a' * 255, b'b' * 70
    air.sealed = {first: dict(seq=0), second: dict(ack_seq=0)}
    start = timeline.now + START_GUARD_US
    data = air.transmit(air.channels[0], first, start, lambda at: terminals.append(at))
    air.transmit(air.channels[1], second, start + 1000, lambda at: terminals.append(at))
    assert received == [[], []] and not terminals  # admission is not RF completion
    while timeline.events:
        at, _, callback = heapq.heappop(timeline.events)
        timeline.now = at
        callback()
    assert len(terminals) == 2 and received == [[], []]
    assert all(frame.collision and frame.lost for frame in air.frames)
    assert data.end - data.start == estimated_airtime_us(PROFILE, 255)


def test_nine_data_queued_sf5_on_independent_half_duplex_hosts(tmp_path):
    results, air = exercise(tmp_path)
    assert_complete(results, air)
    assert results[0]['snapshot']['retransmits'] == 0
    assert not any(frame.collision for frame in air.frames)
    assert 1 < air.channels[0].peak <= 4
    data = [frame for frame in air.frames if 'seq' in frame.inner]
    assert len(data) == 9
    for number, (first, second) in enumerate(zip(data, data[1:]), 1):
        assert second.start >= first.end + (REVERSE_LISTEN_US if number % 4 == 0 else INTER_PACKET_RX_US)


def test_final_ack_loss_recovers_from_fresh_ciphertext_retry_after_receiver_complete(tmp_path):
    results, air = exercise(tmp_path, drop_final_ack=True)
    assert_complete(results, air)
    final_acks = [frame for frame in air.frames if frame.inner.get('ack_seq') == 9]
    assert len(final_acks) >= 2 and final_acks[0].lost and not final_acks[-1].lost
    tail = [frame for frame in air.frames if frame.inner.get('seq') == 8]
    assert len(tail) == 2 and tail[0].inner == tail[1].inner
    assert tail[0].packet != tail[1].packet
    assert results[0]['snapshot']['retransmits'] == 1
    # The frozen completed-receiver branch reasserts receipt without adding to
    # its OPEN-state duplicate counter. Ten authenticated DATA still deliver
    # exactly the original nine-frame object once.
    assert results[1]['snapshot']['counts']['rx_DATA'] == 10
    assert tail[1].start > results[1]['received_at']


def test_missing_data_recovers_without_synthesized_receipt_or_credit(tmp_path):
    results, air = exercise(tmp_path, drop_data={1: 1})
    assert_complete(results, air)
    assert results[0]['snapshot']['retransmits'] >= 1
    missing = [frame for frame in air.frames if frame.inner.get('seq') == 1]
    assert missing[0].lost and not missing[-1].lost
    assert missing[0].packet != missing[-1].packet


def test_receiver_cap_can_expire_before_final_receipt_and_original_allowance_recovers(tmp_path):
    # Four explicit air losses, identically injected in both runs. This is a
    # controlled source reproduction, not an assertion that the RF losses had
    # these causes. Slow retry backoff allows unchanged partial ACK persistence
    # to consume a 20-attempt receiver budget before the missing DATA arrives.
    faults = {0: 1, 2: 1, 5: 2}
    limited, limited_air = exercise(tmp_path / 'cap20', receiver_cap=20, drop_data=faults)
    sender, receiver = limited
    assert not sender['complete'] and sender['snapshot']['acked_frames'] == 8
    assert str(sender['error']) == 'reliable_flow_retry_ceiling'
    assert receiver['complete'] and receiver['received'] == PAYLOAD
    assert receiver['snapshot']['attempt_journal'] == dict(attempts=20,
        reserved_estimated_airtime_us=261140, fenced=False, recovered_unknown=0)
    assert sender['snapshot']['attempt_journal'] == dict(attempts=14,
        reserved_estimated_airtime_us=537249, fenced=False, recovered_unknown=0)
    assert sender['snapshot']['retransmits'] == 5
    acks = [frame for frame in limited_air.frames if 'ack_seq' in frame.inner]
    assert len(acks) == 20 and all(frame.inner['ack_seq'] < 9 for frame in acks)
    assert acks[-1].end < receiver['received_at']
    assert not limited_air.channels[0].events
    assert not any(channel.armed for channel in limited_air.channels)
    assert not any(frame.collision for frame in limited_air.frames)
    assert not any(r.get('cleanup_errors') for r in limited)

    recovered, recovered_air = exercise(tmp_path / 'cap64', receiver_cap=64, drop_data=faults)
    assert_complete(recovered, recovered_air)
    assert recovered[0]['snapshot']['attempt_journal']['attempts'] == 13
    assert recovered[0]['snapshot']['retransmits'] == 4
    assert recovered[1]['snapshot']['attempt_journal']['attempts'] == 22
    final = [frame for frame in recovered_air.frames if frame.inner.get('ack_seq') == 9]
    assert len(final) == 1 and not final[0].lost
    assert not any(frame.collision for frame in recovered_air.frames)
    assert limited_air.drop_data == recovered_air.drop_data == {0: 0, 2: 0, 5: 0}
