"""Wire-level queued worker tests using a modeled, keyless serial peer."""
import hashlib
import json
import sqlite3
import struct
from dataclasses import replace

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.burst_protocol import (
    ACK, CAPABILITY, CANCEL, CLOCK, DATA, EVENT, OPEN, POLICY, STATUS, TOKEN,
)
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, Outcome, RadioProfile
from meshticulum.bench.serial_worker import estimated_airtime_us, journal_binding
from meshticulum.bench.tx_burst import Stage

BOOT = bytes.fromhex('01020304050607080000000000000001')
VERSION = 'burst-test'
PROFILE = RadioProfile(921375000, 500000, 7, 5, 0, 8, 0x42, 60000, 128, 20_000_000)


class Channel:
    def __init__(self, fault=None):
        self.time = 100.
        self.inbound = bytearray()
        self.writes = []
        self.fault = fault
        self.arm_request = self.arm_end = self.token = self.open_end = 0
        self.context = bytes(32)
        self.events = {}
        self.ack_receipts = {}
        self.fenced = False
        self.attempts = self.airtime = self.mutation = 0
        self.next_start_us = 0
        self.on_data = lambda frame, packet: None
        self.on_ack = lambda frame: None
        self.on_status = lambda: None
        self.ack_loss = fault == 'lost_ack'

    def monotonic(self):
        return self.time

    def now_us(self):
        return 10_000_000 + round((self.time - 100) * 1_000_000)

    def offer(self, frame):
        self.inbound += frame.encode()

    def event(self, request, stage, *, at_us=None, changed_digest=False):
        token, _, _, digest, estimate, size, end, _, _, _ = self.events[request]
        driver = (estimate + 500, 1, 0) if stage is Stage.RF_DONE else (0, 0, 0)
        self.events[request] = (token, stage, self.now_us() if at_us is None else at_us,
            b'x' * 32 if changed_digest else digest, estimate, size, end, *driver)
        self.offer(Frame(Message.BURST_EVENT, BOOT, request, EVENT.pack(*self.events[request])))

    def write(self, raw, deadline):
        assert self.time < deadline
        self.time += .0003
        frames = Decoder().feed(raw)
        assert len(frames) == 1
        frame = frames[0]
        self.writes.append(frame)
        def reply(kind, body=b''):
            self.offer(Frame(kind, BOOT, frame.request, body))
        if frame.type in (Message.ARM, Message.DISARM, Message.BURST_OPEN,
                          Message.BURST_DATA, Message.BURST_ACK, Message.BURST_CANCEL,
                          Message.TRANSMIT):
            if frame.type is Message.BURST_ACK and frame.request in self.ack_receipts:
                assert self.ack_receipts[frame.request] == frame.body
                reply(Message.BURST_ACK_REPLY, frame.body)
                return
            assert frame.request > self.mutation, (frame.request, self.mutation)
            self.mutation = frame.request
        if frame.type is Message.HELLO:
            reply(Message.HELLO_REPLY, struct.pack('!BBH', 1, 1, 255)
                  + (VERSION.encode() + b'\0').ljust(16, b'\0'))
        elif frame.type is Message.BURST_CAPABILITIES:
            reply(Message.BURST_CAPABILITIES_REPLY,
                  b'?' * len(CAPABILITY) if self.fault == 'capability' else CAPABILITY)
        elif frame.type is Message.DISARM:
            reply(Message.DISARM_REPLY, b'\0')
        elif frame.type is Message.ARM:
            self.arm_request = frame.request
            self.arm_end = self.now_us() + PROFILE.lease_ms * 1000
            reply(Message.ARM_REPLY, frame.body)
        elif frame.type is Message.BURST_CLOCK:
            reply(Message.BURST_CLOCK_REPLY, CLOCK.pack(
                self.now_us() - (100_000 if self.fault == 'clock_regression' else 0),
                self.arm_request, self.arm_end))
            if self.fault == 'slow_clock':
                self.time += .051
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
            assert self.now_us() < end <= min(self.open_end, self.now_us() + 2_000_000)
            self.on_data(frame, packet)
            if self.fault == 'refused':
                reply(Message.REFUSED, b'\x08')
                return
            self.attempts += 1
            self.airtime += estimate
            self.events[frame.request] = (token, Stage.QUEUED, self.now_us(), digest,
                                          estimate, size, end, 0, 0, 0)
            if self.fault == 'partial_write':
                raise OSError('bytes reached MCU, reply lost')
            if self.fault == 'terminal_first':
                self.event(frame.request, Stage.RF_DONE)
            else:
                reply(Message.BURST_EVENT, EVENT.pack(*self.events[frame.request]))
        elif frame.type is Message.BURST_QUERY:
            assert TOKEN.unpack(frame.body) == (self.token,)
            if frame.request in self.events:
                reply(Message.BURST_EVENT, EVENT.pack(*self.events[frame.request]))
            else:
                reply(Message.REFUSED, b'\x08')
        elif frame.type is Message.BURST_ACK:
            token, request, stage, at_us, digest = ACK.unpack(frame.body)
            held = self.events[request]
            assert (token, stage, at_us, digest) == (held[0], held[1], held[2], held[3])
            self.on_ack(frame)
            del self.events[request]
            self.ack_receipts[frame.request] = frame.body
            if self.ack_loss:
                self.ack_loss = False
                return
            reply(Message.BURST_ACK_REPLY, frame.body)
        elif frame.type is Message.BURST_CANCEL:
            assert TOKEN.unpack(frame.body) == (self.token,)
            self.fenced = True
            for request, event in tuple(self.events.items()):
                if event[1] not in (Stage.RF_DONE, Stage.NOT_SENT, Stage.UNKNOWN):
                    self.event(request, Stage.NOT_SENT if event[1] is Stage.QUEUED else Stage.UNKNOWN)
            if self.fault != 'cancel_reply_lost':
                reply(Message.BURST_CANCEL_REPLY, CANCEL.pack(self.token, 1, 0))
        elif frame.type is Message.BURST_STATUS:
            self.on_status()
            reply(Message.BURST_STATUS_REPLY, STATUS.pack(self.token, self.context,
                self.arm_request, self.open_end,
                sum(event[1] not in (Stage.RF_DONE, Stage.NOT_SENT, Stage.UNKNOWN)
                    for event in self.events.values()), len(self.events), 0, self.fenced, 0,
                self.now_us(), self.next_start_us, self.attempts, self.airtime))
        elif frame.type is Message.TRANSMIT:
            assert not self.events
            assert self.now_us() >= self.next_start_us
            digest = hashlib.sha256(frame.body).digest()
            estimate = estimated_airtime_us(PROFILE, len(frame.body))
            reply(Message.TX_RESULT, struct.pack('!B32sQQIh', Outcome.RF_DONE,
                                                digest, estimate, estimate + 500, 1, 0))
        else:
            raise AssertionError(frame.type)

    def read(self, timeout):
        self.time += .0002 if self.inbound else max(timeout, .0001)
        if self.inbound:
            data = bytes(self.inbound[:4096])
            del self.inbound[:4096]
            return data
        return b''


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    journals = []
    def make(fault=None, authorize=lambda: True, profile=PROFILE):
        channel = Channel(fault)
        worker = BurstSerialWorker(channel, policy=dict(POLICY))
        identity = worker.discover(expected_board_id=1, expected_version=VERSION)
        path = tmp_path / (str(len(journals)) + '.sqlite')
        journal = AttemptJournal(path, binding=journal_binding(b'c' * 32, identity, profile),
            max_attempts=128, max_airtime_us=20_000_000, create=True, request_clock=lambda: 100_000_000)
        journals.append(journal)
        worker.arm(journal=journal, profile=profile, context_digest=b'c' * 32,
                   authorize=authorize, authority_deadline=150.)
        return worker, channel, journal, path
    yield make
    for journal in journals:
        journal.close()


def test_exact_metadata_is_durable_before_queue_write_and_acceptance_is_not_completion(setup):
    worker, channel, journal, path = setup()
    def inspect(frame, packet):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT a.request,a.packet_sha,a.bytes,a.state,q.metadata '
                'FROM attempts a JOIN queued_attempts q USING(request)').fetchone()
        assert row[:4] == (frame.request, hashlib.sha256(packet).digest(), len(packet), 'RESERVED')
        metadata = json.loads(row[4])
        assert metadata['open_token'] == channel.token and metadata['boot'] == BOOT.hex()
        assert metadata['logical_id'] == (b'l' * 32).hex()
        assert metadata['estimated_airtime_us'] == estimated_airtime_us(PROFILE, len(packet))
    channel.on_data = inspect
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32, owner_frame_id=4)
    assert result.outcome == 'QUEUED' and result.driver is None
    assert worker.pending_physical == (result.request,)
    assert worker.poll_physical() == []
    assert journal.queued_records()[0]['state'] == 'RESERVED'
    assert worker.pending_airtime_us >= 300_000
    with pytest.raises(HardwareError, match='physical_pending'):
        worker.assert_drained()


def test_four_retained_requests_route_interleaved_evidence_and_settle_before_release(setup):
    worker, channel, journal, _ = setup()
    results = [worker.queue_data(bytes([index]) * 255, logical_id=bytes([index + 1]) * 32)
               for index in range(4)]
    assert not worker.has_capacity and len(worker.pending_physical) == 4
    def inspect_ack(frame):
        request = ACK.unpack(frame.body)[1]
        record = next(record for record in journal.queued_records() if record['request'] == request)
        assert record['state'] == 'RF_DONE' and record['terminal_event'] is not None
        assert not record['released']
    channel.on_ack = inspect_ack
    for result in reversed(results):
        channel.event(result.request, Stage.RF_DONE)
    physical = worker.poll_physical()
    assert {value.request for value in physical} == {value.request for value in results}
    assert all(value.outcome == 'RF_DONE' for value in physical)
    assert not worker.pending_physical and worker.has_capacity and worker.pending_airtime_us == 0
    assert worker.assert_drained()
    assert all(record['released'] for record in journal.queued_records())
    assert journal.snapshot()['attempts'] == 4
    # A fresh batch samples CLOCK again and cannot extend its original OPEN cap.
    old_cap = worker._open.deadline_us
    worker.queue_data(b'next', logical_id=b'n' * 32)
    assert worker._burst_deadline <= old_cap
    assert sum(frame.type is Message.BURST_CLOCK for frame in channel.writes) == 3


def test_terminal_first_and_exact_replay_do_not_need_intermediate_reports(setup):
    worker, channel, journal, _ = setup('terminal_first')
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    event = Frame(Message.BURST_EVENT, BOOT, result.request, EVENT.pack(*channel.events[result.request]))
    channel.offer(event)
    assert [value.outcome for value in worker.poll_physical()] == ['RF_DONE']
    assert worker.poll_physical() == []
    channel.offer(event)  # delayed exact event after native release
    assert worker.poll_physical() == []
    assert journal.snapshot()['attempts'] == 1 and not worker.fenced


def test_duplicate_logical_data_is_refused_before_new_debt_or_write(setup):
    worker, channel, journal, _ = setup()
    worker.queue_data(b'first ciphertext', logical_id=b'l' * 32)
    before = len(channel.writes)
    with pytest.raises(HardwareError, match='duplicate_pending_data'):
        worker.queue_data(b'resealed ciphertext', logical_id=b'l' * 32)
    assert len(channel.writes) == before and journal.snapshot()['attempts'] == 1


def test_receive_does_not_discard_physical_results_and_raw_rx_remains_host_owned(setup):
    worker, channel, _, _ = setup()
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    channel.event(result.request, Stage.RF_DONE)
    channel.offer(Frame(Message.RECEIVE, BOOT, 0, struct.pack('!hh', -730, 110) + b'raw'))
    assert worker.receive()[1].packet == b'raw'
    assert [value.request for value in worker.poll_physical()] == [result.request]


def test_drain_returns_buffered_terminal_once_after_internal_pump_released_every_slot(setup):
    worker, channel, _, _ = setup()
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    channel.event(result.request, Stage.RF_DONE)
    assert worker.receive() is None  # Internal pump settles and releases.
    assert not worker.pending_physical
    assert [value.request for value in worker.drain_physical()] == [result.request]
    assert worker.drain_physical() == []


def test_physical_settlement_and_drain_finish_after_authority_expiry(setup):
    worker, channel, journal, _ = setup()
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    channel.event(result.request, Stage.RF_DONE)
    channel.time = 151.
    assert [value.outcome for value in worker.drain_physical()] == ['RF_DONE']
    assert not worker.pending_physical and journal.queued_records()[0]['released']
    with pytest.raises(HardwareError, match='expired'):
        worker.queue_data(b'new', logical_id=b'n' * 32)


def test_ambiguous_write_keeps_charge_and_fence_then_accounts_without_upgrading(setup):
    worker, channel, journal, _ = setup('partial_write')
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    assert result.outcome == 'UNKNOWN' and worker.fenced and journal.snapshot()['fenced']
    assert journal.queued_records()[0]['state'] == 'UNKNOWN'
    worker.drain_physical()
    assert not worker.pending_physical
    assert journal.queued_records()[0]['state'] == 'UNKNOWN'
    assert journal.snapshot()['attempts'] == 1 and worker.fenced


def test_changed_terminal_digest_fences_and_never_releases(setup):
    worker, channel, journal, _ = setup()
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    channel.event(result.request, Stage.RF_DONE, changed_digest=True)
    with pytest.raises((HardwareError, ValueError), match='packet_changed'):
        worker.poll_physical()
    assert worker.fenced and journal.snapshot()['fenced']
    assert worker.pending_physical == (result.request,)
    assert not any(frame.type is Message.BURST_ACK for frame in channel.writes)


def test_synchronous_control_waits_for_exact_physical_release(setup):
    worker, channel, journal, _ = setup()
    result = worker.queue_data(b'sealed DATA', logical_id=b'l' * 32)
    channel.event(result.request, Stage.RF_DONE)
    control = worker.transmit(b'sealed ACK/control')
    assert control.outcome == 'RF_DONE'
    assert not worker.pending_physical and journal.snapshot()['attempts'] == 2
    types = [frame.type for frame in channel.writes]
    assert types.index(Message.BURST_ACK) < types.index(Message.TRANSMIT)
    assert [value.request for value in worker.poll_physical()] == [result.request]


def test_exact_release_replay_recovers_lost_reply_without_new_mutation(setup):
    worker, channel, journal, _ = setup('lost_ack')
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    channel.event(result.request, Stage.RF_DONE)
    assert [value.outcome for value in worker.poll_physical()] == ['RF_DONE']
    releases = [frame for frame in channel.writes if frame.type is Message.BURST_ACK]
    assert len(releases) == 2 and releases[0] == releases[1]
    assert not worker.fenced and journal.queued_records()[0]['released']


def test_event_during_other_command_wait_is_settled_without_nested_ack_write(setup):
    worker, channel, journal, _ = setup()
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    def complete_during_status():
        channel.on_status = lambda: None
        channel.event(result.request, Stage.RF_DONE)
    channel.on_status = complete_during_status
    worker._status()
    assert journal.queued_records()[0]['state'] == 'RF_DONE'
    assert not any(frame.type is Message.BURST_ACK for frame in channel.writes)
    assert [value.request for value in worker.poll_physical()] == [result.request]
    diagnostics = worker.physical_snapshot(result.request)
    assert [value['event'] for value in diagnostics] == ['submit', 'physical', 'physical', 'release']
    assert diagnostics[-2]['stage'] == 'RF_DONE' and diagnostics[-2]['mcu_event_us'] > 0


def test_regressed_fresh_clock_fences_before_attempt_reservation(setup):
    worker, channel, journal, _ = setup()
    channel.fault = 'clock_regression'
    with pytest.raises(HardwareError, match='clock_identity_changed'):
        worker.queue_data(b'never written', logical_id=b'l' * 32)
    assert worker.fenced and journal.snapshot()['fenced']
    assert journal.snapshot()['attempts'] == 0


def test_cleanup_cancel_settles_paid_queued_items_without_new_rf(setup):
    worker, channel, journal, _ = setup()
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    worker.cancel_pending()
    assert worker.fenced
    assert [value.outcome for value in worker.drain_physical()] == ['NOT_SENT']
    assert not worker.pending_physical and journal.snapshot()['attempts'] == 1
    assert journal.queued_records()[0]['released']
    assert not any(frame.type is Message.TRANSMIT for frame in channel.writes)


def test_synchronous_control_preserves_native_reverse_listen_window(setup):
    worker, channel, _, _ = setup()
    result = worker.queue_data(b'sealed DATA', logical_id=b'l' * 32)
    channel.event(result.request, Stage.RF_DONE)
    channel.next_start_us = channel.now_us() + 200_000
    before = channel.time
    assert worker.transmit(b'sealed control').outcome == 'RF_DONE'
    assert channel.time - before >= .2
    assert [value.request for value in worker.poll_physical()] == [result.request]


def test_unbounded_slow_packet_cannot_strand_a_durable_reservation(setup):
    slow = replace(PROFILE, bandwidth_hz=62500, spreading_factor=12)
    worker, channel, journal, _ = setup(profile=slow)
    before = len(channel.writes)
    with pytest.raises(ValueError, match='invalid_burst_airtime'):
        worker.queue_data(bytes(255), logical_id=b'l' * 32)
    assert journal.snapshot()['attempts'] == 0 and not worker.pending_physical
    assert not any(frame.type is Message.BURST_DATA for frame in channel.writes[before:])


def test_delayed_exact_release_replies_do_not_fill_router_or_repeat_settlement(setup):
    worker, channel, journal, _ = setup()
    result = worker.queue_data(b'sealed', logical_id=b'l' * 32)
    def duplicate(frame):
        channel.offer(Frame(Message.BURST_ACK_REPLY, BOOT, frame.request, frame.body))
    channel.on_ack = duplicate
    channel.event(result.request, Stage.RF_DONE)
    assert len(worker.poll_physical()) == 1
    release = next(frame for frame in channel.writes if frame.type is Message.BURST_ACK)
    duplicate(release)
    assert worker.poll_physical() == []
    assert not worker._routed and journal.queued_records()[0]['released']


def test_lost_cancel_reply_never_restores_future_send_permission(setup):
    worker, channel, journal, _ = setup('cancel_reply_lost')
    worker.queue_data(b'sealed', logical_id=b'l' * 32)
    with pytest.raises(HardwareError, match='serial_reply_deadline'):
        worker.cancel_pending()
    before = sum(frame.type is Message.BURST_DATA for frame in channel.writes)
    assert worker.fenced and journal.snapshot()['fenced']
    with pytest.raises(HardwareError, match='authority_refused'):
        worker.queue_data(b'new', logical_id=b'n' * 32)
    assert sum(frame.type is Message.BURST_DATA for frame in channel.writes) == before


def test_capability_and_slow_clock_refuse_before_queueing(tmp_path, setup):
    channel = Channel('capability')
    with pytest.raises(ValueError, match='capability'):
        BurstSerialWorker(channel, policy=dict(POLICY)).discover(
            expected_board_id=1, expected_version=VERSION)
    with pytest.raises((HardwareError, ValueError), match='sampling_envelope|reply_deadline'):
        setup('slow_clock')
