# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Crash and transport-fault controls; synthetic channel, never RF evidence."""
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import struct

import pytest

from meshticulum.bench.attempts import AttemptError, AttemptJournal


def test_abrupt_process_exit_keeps_wal_reservation_and_fences_recovery(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'abrupt.sqlite'
    code = '''import os,sys
from meshticulum.bench.attempts import AttemptJournal
j=AttemptJournal(sys.argv[1],binding=b'x'*32,max_attempts=4,max_airtime_us=10000,create=True,request_clock=lambda:123)
assert j.db.execute('PRAGMA journal_mode').fetchone()==('wal',)
assert j.db.execute('PRAGMA synchronous').fetchone()==(2,)
assert j.reserve(b'packet',3000)==123
os._exit(0)
'''
    subprocess.run([sys.executable, '-c', code, str(path)], check=True)
    assert Path(str(path) + '-wal').exists()
    reopened = AttemptJournal(path, binding=b'x'*32, max_attempts=4, max_airtime_us=10000)
    try:
        assert reopened.snapshot() == {'attempts': 1, 'reserved_estimated_airtime_us': 3000,
                                       'fenced': True, 'recovered_unknown': 1}
        with pytest.raises(AttemptError, match='fenced'):
            reopened.reserve(b'next', 3000)
    finally:
        reopened.close()
    # The final close checkpoints the public evidence DB; collection never
    # needs to copy a live WAL independently of its database.
    assert not Path(str(path) + '-wal').exists()
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, Outcome, RadioProfile
from meshticulum.bench.serial_worker import SerialWorker, estimated_airtime_us, journal_binding

BOOT = bytes.fromhex('0000545662f61b440000000000000012')
VERSION = 'mtb1-0123456789'
PROFILE = RadioProfile(917375000, 125000, 8, 5, 0, 8, 0x42, 60000, 10, 10000000)


class Channel:
    def __init__(self, fault=None):
        self.time = 100.0
        self.fault = fault
        self.inbound = bytearray()
        self.writes = []
        self.on_transmit = lambda frame: None

    def monotonic(self):
        return self.time

    def offer(self, frame):
        self.inbound += frame.encode()

    def write(self, data, deadline):
        assert self.time < deadline
        frames = Decoder().feed(data)
        assert len(frames) == 1
        frame = frames[0]
        self.writes.append(frame)
        reply = lambda kind, body: self.offer(Frame(kind, BOOT, frame.request, body))
        if frame.type is Message.HELLO:
            reply(Message.HELLO_REPLY, struct.pack('!BBH', 1, 1, 255) + VERSION.encode() + b'\0')
        elif frame.type is Message.DISARM:
            reply(Message.DISARM_REPLY, b'\0')
        elif frame.type is Message.ARM:
            reply(Message.ARM_REPLY, bytes(30) if self.fault == 'wrong_profile' else frame.body)
        elif frame.type is Message.TRANSMIT:
            self.on_transmit(frame)
            if self.fault == 'partial_write':
                raise OSError('lost after some bytes')
            if self.fault == 'refused':
                reply(Message.REFUSED, b'\x08')
                return
            digest = hashlib.sha256(frame.body).digest()
            estimate = estimated_airtime_us(PROFILE, len(frame.body))
            body = struct.pack('!B32sQQIh', Outcome.STARTED, digest, estimate, 0, 0, 0)
            reply(Message.TX_RESULT, body)
            if self.fault == 'refused_after_start':
                reply(Message.REFUSED, b'\x08')
                return
            if self.fault == 'started_only':
                return
            outcome = Outcome.UNKNOWN if self.fault == 'unknown' else Outcome.RF_DONE
            body = struct.pack('!B32sQQIh', outcome,
                               bytes(32) if self.fault == 'wrong_digest' else digest,
                               estimate, estimate + 1000, 0x201 if self.fault == 'contradictory_irq' else 1, 0)
            self.offer(Frame(Message.TX_RESULT, bytes(16) if self.fault == 'wrong_boot' else BOOT,
                             frame.request, body))

    def read(self, timeout):
        if self.inbound:
            data = bytes(self.inbound[:4096])
            del self.inbound[:4096]
            return data
        self.time += max(timeout, 0.0001)
        return b''


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    opened = []

    def make(fault=None, authorize=lambda: True):
        channel = Channel(fault)
        worker = SerialWorker(channel)
        identity = worker.discover(expected_board_id=1, expected_version=VERSION)
        path = tmp_path / (str(len(opened)) + '.sqlite')
        context = b'c' * 32
        journal = AttemptJournal(path, binding=journal_binding(context, identity, PROFILE),
            max_attempts=PROFILE.max_boot_attempts,
            max_airtime_us=PROFILE.max_boot_estimated_airtime_us, create=True,
            request_clock=lambda: 100000000)
        opened.append(journal)
        worker.arm(journal=journal, profile=PROFILE, context_digest=context,
                   authorize=authorize, authority_deadline=200.0)
        return worker, channel, journal, path
    yield make
    for journal in opened:
        journal.close()


def test_reservation_is_durable_before_first_transport_write(setup):
    worker, channel, journal, path = setup()
    def observe(frame):
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT request,packet_sha,state FROM attempts').fetchone()
        assert row == (frame.request, hashlib.sha256(frame.body).digest(), 'RESERVED')
    channel.on_transmit = observe
    result = worker.transmit(b'sealed packet')
    assert result.outcome == 'RF_DONE'
    assert journal.snapshot()['attempts'] == 1
    assert journal.snapshot()['reserved_estimated_airtime_us'] == result.reserved_estimated_us
    assert result.driver.outcome is Outcome.RF_DONE  # not a peer delivery assertion


@pytest.mark.parametrize('fault', ['partial_write', 'started_only', 'wrong_digest',
                                  'wrong_boot', 'contradictory_irq', 'unknown', 'refused_after_start'])
def test_uncertain_attempts_keep_debt_and_fence_further_radio_work(setup, fault):
    worker, channel, journal, path = setup(fault)
    result = worker.transmit(b'sealed packet')
    assert result.outcome == 'UNKNOWN' and journal.snapshot()['fenced']
    assert journal.snapshot()['attempts'] == 1
    assert journal.snapshot()['reserved_estimated_airtime_us'] == result.reserved_estimated_us
    before = len(channel.writes)
    with pytest.raises(HardwareError, match='radio_authority_refused'):
        worker.transmit(b'next packet')
    assert len(channel.writes) == before


def test_refusal_does_not_refund_airtime_or_claim_completion(setup):
    worker, channel, journal, path = setup('refused')
    result = worker.transmit(b'sealed packet')
    assert result.outcome == 'REFUSED' and result.driver is None
    assert journal.snapshot()['reserved_estimated_airtime_us'] > 0


def test_revocation_between_reservation_and_write_sends_nothing(setup):
    live = [True]
    worker, channel, journal, path = setup(authorize=lambda: live[0])
    original = journal.reserve
    def revoke(packet, estimate):
        request = original(packet, estimate)
        live[0] = False
        return request
    journal.reserve = revoke
    before = len(channel.writes)
    result = worker.transmit(b'sealed packet')
    assert result.outcome == 'NOT_SENT' and len(channel.writes) == before
    assert journal.snapshot()['attempts'] == 1


def test_missing_arm_echo_prevents_transmit(setup):
    with pytest.raises(HardwareError, match='arm_not_confirmed'):
        setup('wrong_profile')


def test_radio_lease_exhaustion_refuses_before_reserving(setup):
    worker, channel, journal, path = setup()
    channel.time = 159.85
    with pytest.raises(HardwareError, match='radio_lease_too_short'):
        worker.transmit(b'sealed packet')
    assert journal.snapshot()['attempts'] == 0


def test_receive_burst_is_bounded_and_remains_unauthenticated(setup):
    worker, channel, journal, path = setup()
    for number in range(40):
        channel.offer(Frame(Message.RECEIVE, BOOT, 0, struct.pack('!hh', -700, 40) + bytes([number])))
    received = worker.receive()
    assert received[1].packet == b'\0'
    assert worker.dropped_receive == 8 and len(worker._received) == 31
    assert journal.snapshot()['attempts'] == 0


def test_process_loss_converts_pending_to_unknown_and_retains_debt(tmp_path):
    tmp_path.chmod(0o700)
    options = dict(binding=b'x'*32, max_attempts=3, max_airtime_us=1000000)
    path = tmp_path / 'crash.sqlite'
    journal = AttemptJournal(path, create=True, **options)
    request = journal.reserve(b'packet', 200000)
    journal.close()  # crash equivalent: no settlement reached durable storage
    reopened = AttemptJournal(path, **options)
    assert reopened.snapshot() == {'attempts': 1, 'reserved_estimated_airtime_us': 200000,
                                   'fenced': True, 'recovered_unknown': 1}
    with pytest.raises(AttemptError, match='attempt_run_fenced'):
        reopened.reserve(b'packet', 200000)
    with pytest.raises(AttemptError, match='attempt_already_settled'):
        reopened.settle(request, b'packet', 'RF_DONE', 'late reply')
    reopened.close()


def test_live_journal_cannot_be_opened_by_second_owner(tmp_path):
    tmp_path.chmod(0o700)
    options = dict(binding=b'x'*32, max_attempts=3, max_airtime_us=1000000)
    path = tmp_path / 'owner.sqlite'
    journal = AttemptJournal(path, create=True, **options)
    journal.reserve(b'packet', 200000)
    with pytest.raises(BlockingIOError):
        AttemptJournal(path, **options)
    assert not journal.snapshot()['fenced']
    journal.close()


def test_attempt_limits_and_exact_settlement_survive_reopen(tmp_path):
    tmp_path.chmod(0o700)
    options = dict(binding=b'x'*32, max_attempts=2, max_airtime_us=1000000)
    path = tmp_path / 'budget.sqlite'
    journal = AttemptJournal(path, create=True, request_clock=lambda: 100, **options)
    request = journal.reserve(b'one', 600000)
    with pytest.raises(AttemptError, match='wrong_attempt_settlement'):
        journal.settle(request, b'other', 'RF_DONE', 'bad binding')
    journal.settle(request, b'one', 'REFUSED', 'firmware refused')
    journal.close()
    reopened = AttemptJournal(path, request_clock=lambda: 90, **options)
    next_request = reopened.reserve(b'two', 400000)
    assert next_request > request
    reopened.settle(next_request, b'two', 'RF_DONE', 'irq')
    with pytest.raises(AttemptError, match='attempt_budget_exhausted'):
        reopened.reserve(b'three', 1)
    reopened.close()


def test_wrong_session_or_missing_journal_cannot_reinitialize(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'state.sqlite'
    options = dict(binding=b'x'*32, max_attempts=2, max_airtime_us=1000000)
    with pytest.raises(FileNotFoundError):
        AttemptJournal(path, **options)
    journal = AttemptJournal(path, create=True, **options)
    journal.close()
    with pytest.raises(AttemptError, match='attempt_state_binding_mismatch'):
        AttemptJournal(path, **(options | {'binding': b'y'*32}))
    with pytest.raises(FileExistsError):
        AttemptJournal(path, create=True, **options)


def test_source_airtime_known_vectors_and_slowest_supported_bound():
    # Semtech LoRa equation, SF7/BW125/CR4:5/preamble8/CRC/explicit header.
    sf7 = replace(PROFILE, spreading_factor=7)
    assert estimated_airtime_us(sf7, 16) == 51456
    assert estimated_airtime_us(sf7, 255) == 399616
    slow = replace(PROFILE, spreading_factor=12, bandwidth_hz=62500, coding_rate=8, preamble=64)
    assert estimated_airtime_us(slow, 255) < 33000000
    fast = replace(PROFILE, spreading_factor=5, bandwidth_hz=500000)
    assert estimated_airtime_us(fast, 255) == 34576
    assert 193 * 1000000 / estimated_airtime_us(fast, 255) > 5500
