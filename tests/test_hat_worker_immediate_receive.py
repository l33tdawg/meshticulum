"""Durable TX/RX handoff controls; modeled SPI is not native RF evidence."""
from types import SimpleNamespace

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.hat_worker import HATWorker, journal_binding
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Outcome, TxEvidence
from tests.test_meshticulum_bench_sx1262 import Chip, PROFILE


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    chip = Chip()
    context = b'c' * 32
    journal = AttemptJournal(tmp_path / 'attempts.sqlite',
        binding=journal_binding(context, chip.boot_id, PROFILE),
        max_attempts=20, max_airtime_us=10000000, create=True)
    authorize_state = {'callback': lambda: True}
    worker = HATWorker(chip, journal=journal, profile=PROFILE, context_digest=context,
        authorize=lambda: authorize_state['callback'](), authority_deadline=200, antenna_confirmed=True,
        monotonic=lambda: chip.time, time_basis='model-monotonic')
    worker._test_authorize_state = authorize_state
    try:
        yield chip, journal, worker
    finally:
        journal.close()


def row(journal):
    return journal.db.execute('SELECT state,estimated_us,evidence FROM attempts').fetchall()


def assert_done(chip, journal, result):
    assert result.outcome == 'RF_DONE' and result.driver.outcome is Outcome.RF_DONE
    records = row(journal)
    assert len(records) == 1 and records[0][0] == 'RF_DONE'
    assert records[0][1] == result.reserved_estimated_us
    assert journal.snapshot()['reserved_estimated_airtime_us'] == result.reserved_estimated_us
    assert not journal.snapshot()['fenced'] and chip.tx_calls == 1


def test_receive_starts_after_durable_done_before_transmit_returns(rig):
    chip, journal, worker = rig
    settled = []
    original_settle, transfer = journal.settle, chip.transfer
    def settle(*args):
        original_settle(*args)
        settled.append(args[2])
    def checked_transfer(command):
        if command[0] == 0x82:
            assert settled == ['RF_DONE'] and row(journal)[0][0] == 'RF_DONE'
        return transfer(command)
    journal.settle, chip.transfer = settle, checked_transfer
    result = worker.transmit(b'encrypted packet')
    assert_done(chip, journal, result)
    assert settled == ['RF_DONE'] and worker.radio.receiving and chip.mode == 5
    assert not worker.fenced


@pytest.mark.parametrize('fault', ['refused', 'raised', 'expired', 'lease_expired',
                                 'driver', 'contain_failure', 'radio', 'worker_io', 'radio_io'])
def test_failed_receive_preserves_known_done_and_fences_without_second_tx(rig, fault):
    chip, journal, worker = rig
    original_radio = worker.radio
    foreign = SimpleNamespace(io=Chip())
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        if calls == 3:  # pre-reserve, pre-SPI, then fresh post-settlement authority
            assert row(journal)[0][0] == 'RF_DONE'
            if fault == 'refused': return False
            if fault == 'raised': raise RuntimeError('revoked')
            if fault == 'expired': chip.time = worker.deadline
            if fault == 'lease_expired': chip.time = worker.lease_deadline
            if fault == 'radio': worker.radio = foreign
            if fault == 'worker_io': worker.io = foreign.io
            if fault == 'radio_io': original_radio.io = foreign.io
            if fault == 'contain_failure': return False
        return True
    worker._test_authorize_state['callback'] = authorize
    transfer = chip.transfer
    def rx_failure(command):
        if command[0] == 0x82: raise OSError('partial RX command')
        return transfer(command)
    if fault == 'driver': chip.transfer = rx_failure
    if fault == 'contain_failure':
        def failed_reset(_high): raise OSError('containment failed')
        chip.reset = failed_reset
    result = worker.transmit(b'encrypted packet')
    assert_done(chip, journal, result)
    assert calls == 3 and worker.fenced and not original_radio.receiving
    assert not foreign.io.commands and not foreign.io.resets
    if fault != 'driver': assert not any(c[0] == 0x82 for c in chip.commands)
    with pytest.raises(HardwareError, match='hat_authority_refused'):
        worker.transmit(b'no retry')
    assert chip.tx_calls == 1 and journal.snapshot()['attempts'] == 1


def test_receive_cannot_extend_original_lease_or_authority(rig):
    chip, journal, worker = rig
    original_end = min(worker.deadline, worker.lease_deadline)
    deadlines = []
    start_receive = worker.radio.start_receive
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        if calls == 3:
            worker.deadline += 100
            worker.lease_deadline += 100
        return True
    def receive(*, authority_deadline):
        deadlines.append(authority_deadline)
        return start_receive(authority_deadline=authority_deadline)
    worker._test_authorize_state['callback'], worker.radio.start_receive = authorize, receive
    result = worker.transmit(b'encrypted packet')
    assert_done(chip, journal, result)
    assert deadlines == [original_end]


@pytest.mark.parametrize('phase', ['pre_reserve', 'pre_spi'])
def test_refused_or_not_sent_never_starts_receive(rig, phase):
    chip, journal, worker = rig
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        return calls != (1 if phase == 'pre_reserve' else 2)
    worker._test_authorize_state['callback'] = authorize
    if phase == 'pre_reserve':
        with pytest.raises(HardwareError, match='hat_authority_refused'):
            worker.transmit(b'encrypted packet')
        assert row(journal) == []
    else:
        result = worker.transmit(b'encrypted packet')
        assert result.outcome == 'NOT_SENT' and row(journal)[0][0] == 'NOT_SENT'
        assert journal.snapshot()['reserved_estimated_airtime_us'] == result.reserved_estimated_us
    assert chip.tx_calls == 0 and not any(c[0] == 0x82 for c in chip.commands)


@pytest.mark.parametrize('fault', ['start_failure', 'timeout', 'irq_read_error'])
def test_unknown_transmission_never_starts_receive(rig, fault):
    chip, journal, worker = rig
    chip.fault = fault
    result = worker.transmit(b'encrypted packet')
    assert result.outcome == 'UNKNOWN' and row(journal)[0][0] == 'UNKNOWN'
    assert worker.fenced and journal.snapshot()['fenced'] and chip.tx_calls == 1
    assert not any(c[0] == 0x82 for c in chip.commands)


def test_incomplete_driver_evidence_never_starts_receive(rig):
    chip, journal, worker = rig
    worker.radio.poll_transmit = lambda: TxEvidence(Outcome.STARTED, 1, 1, 0, 0)
    result = worker.transmit(b'encrypted packet')
    assert result.outcome == 'UNKNOWN' and worker.fenced and row(journal)[0][0] == 'UNKNOWN'
    assert not any(c[0] == 0x82 for c in chip.commands)


def test_unsettled_journal_never_starts_receive(rig):
    chip, journal, worker = rig
    def fail_settle(*_args): raise OSError('durable settlement failed')
    journal.settle = fail_settle
    with pytest.raises(OSError, match='durable settlement failed'):
        worker.transmit(b'encrypted packet')
    assert row(journal)[0][0] == 'RESERVED' and worker.fenced and chip.tx_calls == 1
    assert not any(c[0] == 0x82 for c in chip.commands)


def test_post_settlement_interrupt_contains_and_preserves_done(rig):
    chip, journal, worker = rig
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        if calls == 3: raise KeyboardInterrupt()
        return True
    worker._test_authorize_state['callback'] = authorize
    with pytest.raises(KeyboardInterrupt):
        worker.transmit(b'encrypted packet')
    assert row(journal)[0][0] == 'RF_DONE' and worker.fenced and chip.tx_calls == 1
    assert chip.resets[-1] is False and not any(c[0] == 0x82 for c in chip.commands)
