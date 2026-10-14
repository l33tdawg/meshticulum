"""Durable TX/RX handoff controls; modeled SPI is not native RF evidence."""
from types import SimpleNamespace
import hashlib
from pathlib import Path
import types

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
    assert result.driver_elapsed_clock_basis == 'model_monotonic'
    records = row(journal)
    assert len(records) == 1 and records[0][0] == 'RF_DONE'
    assert records[0][1] == result.reserved_estimated_us
    assert journal.snapshot()['reserved_estimated_airtime_us'] == result.reserved_estimated_us
    assert not journal.snapshot()['fenced'] and chip.tx_calls == 1


def test_receive_starts_after_known_done_before_result_settlement(rig):
    chip, journal, worker = rig
    settled = []
    original_settle, transfer = journal.settle, chip.transfer
    def settle(*args):
        original_settle(*args)
        settled.append(args[2])
    def checked_transfer(command):
        if command[0] == 0x82:
            assert settled == [] and row(journal)[0][0] == 'RESERVED'
            assert not worker.radio.transmitting and chip.tx_calls == 1
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
        if calls == 3:  # pre-reserve, pre-SPI, then fresh RX-handoff authority
            assert row(journal)[0][0] == 'RESERVED'
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


def test_unsettled_result_contains_early_receive_and_retains_reservation(rig):
    chip, journal, worker = rig
    def fail_settle(*_args): raise OSError('durable settlement failed')
    journal.settle = fail_settle
    with pytest.raises(OSError, match='durable settlement failed'):
        worker.transmit(b'encrypted packet')
    assert row(journal)[0][0] == 'RESERVED' and worker.fenced and chip.tx_calls == 1
    assert any(c[0] == 0x82 for c in chip.commands)
    assert not worker.radio.receiving and chip.resets[-1] is False


def test_receive_handoff_interrupt_settles_known_done_before_propagating(rig):
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


@pytest.mark.parametrize('fault',['revoked','expired','lease_expired','interrupt'])
def test_settlement_cutoff_contains_early_receive_without_erasing_done(rig,fault):
    chip,journal,worker=rig;granted=True;settle=journal.settle
    def delayed_settle(*args):
        nonlocal granted
        assert chip.mode==5 and worker.radio.receiving and args[2]=='RF_DONE'
        settle(*args)
        if fault=='revoked':granted=False
        if fault=='expired':chip.time=worker.deadline
        if fault=='lease_expired':chip.time=worker.lease_deadline
    def authorize():
        if fault=='interrupt' and row(journal) and row(journal)[0][0]=='RF_DONE':raise KeyboardInterrupt()
        return granted
    journal.settle=delayed_settle;worker._test_authorize_state['callback']=authorize
    if fault=='interrupt':
        with pytest.raises(KeyboardInterrupt):worker.transmit(b'encrypted packet')
    else:assert_done(chip,journal,worker.transmit(b'encrypted packet'))
    assert row(journal)[0][0]=='RF_DONE' and worker.fenced and not worker.radio.receiving
    assert chip.tx_calls==1 and chip.resets[-1] is False
    with pytest.raises(HardwareError):worker.receive()


def settlement_ack_model(tmp_path,worker_type,*,settlement_us=35000,authorize_us=2000):
    """Injected local timings, not measured hardware or throughput."""
    tmp_path.mkdir(mode=0o700);chip=Chip();context=b'c'*32;calls=0
    journal=AttemptJournal(tmp_path/'attempts.sqlite',binding=journal_binding(context,chip.boot_id,PROFILE),
        max_attempts=20,max_airtime_us=10000000,create=True)
    try:
        def authorize():
            nonlocal calls
            calls+=1;chip.sleep(authorize_us/1e6);return True
        worker=worker_type(chip,journal=journal,profile=PROFILE,context_digest=context,
            authorize=authorize,authority_deadline=200,antenna_confirmed=True,
            monotonic=lambda:chip.time,time_basis='model-monotonic')
        calls=0;done=[];rx_ready=[];heard=[]
        transfer,poll,settle=chip.transfer,worker.radio.poll_transmit,journal.settle
        def record_poll():
            result=poll()
            if result is not None:done.append(chip.time)
            return result
        def record_transfer(command):
            reply=transfer(command)
            if command[0]==0x82:rx_ready.append(chip.time)
            return reply
        def result_fsync(*args):
            # A reverse ACK arrives5ms into35ms of injected result persistence.
            # The already durable RESERVED row exists before this interval.
            assert row(journal)[0][0]=='RESERVED' and args[2]=='RF_DONE'
            chip.sleep(.005)
            heard.append(chip.mode==5)
            if heard[-1]:chip.rx,chip.flags=b'modeled encrypted ACK',2
            chip.sleep((settlement_us-5000)/1e6)
            return settle(*args)
        chip.transfer=record_transfer;worker.radio.poll_transmit=record_poll;journal.settle=result_fsync
        started=chip.time;result=worker.transmit(b'encrypted packet');finished=chip.time
        assert_done(chip,journal,result)
        receive=worker.receive()
        return dict(ack_received=receive is not None and receive[1].packet==b'modeled encrypted ACK',
            receive_ready_after_done_us=round((rx_ready[0]-done[0])*1e6),
            transmit_call_elapsed_us=round((finished-started)*1e6),
            transmit_authorization_calls=calls-(3 if receive is not None else 1))
    finally:journal.close()


def test_reverse_ack_during_result_fsync_is_received_and_deaf_gap_excludes_fsync(tmp_path):
    # Retain the exact historical source so this control also runs from an
    # archive or a clone containing only the public main branch.
    raw=(Path(__file__).parent/'fixtures'/'hat_worker_6e55cb1.py').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='ecefade896f41f6f70f21fbcc8160b8fe194266c7a8c71dedee219d15dc873bc'
    source=raw.decode('utf-8')
    baseline=types.ModuleType('meshticulum.bench._early_rx_baseline')
    baseline.__package__='meshticulum.bench';exec(compile(source,'_early_rx_baseline.py','exec'),baseline.__dict__)
    old=settlement_ack_model(tmp_path/'baseline',baseline.HATWorker)
    revised=settlement_ack_model(tmp_path/'revised',HATWorker)
    assert old['ack_received'] is False and revised['ack_received'] is True
    assert old['receive_ready_after_done_us']-revised['receive_ready_after_done_us']==35000
    # One extra fresh post-settlement authorization is retained. It increases
    # modeled total work by2ms; the35ms gain is listening readiness, not B/s.
    assert revised['transmit_call_elapsed_us']-old['transmit_call_elapsed_us']==2000


def test_successful_legacy_none_return_is_not_a_pending_receive_refusal(rig):
    chip,journal,worker=rig;original=worker.radio.start_transmit
    def legacy(*args,**kwargs):
        original(*args,**kwargs)
    worker.radio.start_transmit=legacy
    result=worker.transmit(b'encrypted packet')
    assert_done(chip,journal,result)


@pytest.mark.parametrize('fault',['revoked','expired','lease_expired','irq_failure'])
def test_pending_receive_does_not_bypass_fresh_permission_or_containment(rig,fault):
    chip,journal,worker=rig
    worker.radio.start_receive(authority_deadline=worker.lease_deadline);chip.flags=2
    calls=0
    def authorize():
        nonlocal calls
        calls+=1
        if calls==3:
            if fault=='revoked':return False
            if fault=='expired':chip.time=worker.deadline
            if fault=='lease_expired':chip.time=worker.lease_deadline
        return True
    worker._test_authorize_state['callback']=authorize
    original=chip.transfer
    def transfer(command):
        if fault=='irq_failure' and command[0]==0x12:raise OSError('unproved IRQ')
        return original(command)
    chip.transfer=transfer
    result=worker.transmit(b'encrypted packet')
    assert result.outcome=='UNKNOWN' and row(journal)[0][0]=='UNKNOWN'
    assert worker.fenced and journal.snapshot()['fenced'] and chip.tx_calls==0
    assert journal.snapshot()['reserved_estimated_airtime_us']==result.reserved_estimated_us>0
    assert chip.resets[-1] is False
    with pytest.raises(HardwareError):worker.receive()


def test_next_burst_packet_can_arrive_during_upper_layer_work(rig):
    chip, journal, worker = rig
    worker.radio.start_receive(authority_deadline=worker.lease_deadline)
    chip.rx, chip.flags = b'first encrypted packet', 2
    first = worker.receive(timeout=0)
    assert first[1].packet == b'first encrypted packet'
    # Model the next RF packet arriving while the caller authenticates and
    # processes the previous packet. The chip accepts it only in RX mode.
    chip.sleep(.025)
    if chip.mode == 5:
        chip.rx, chip.flags = b'next encrypted packet', 2
    second = worker.receive(timeout=0)
    assert second is not None and second[1].packet == b'next encrypted packet'
    assert worker.radio.receiving and chip.mode == 5 and not worker.fenced
    assert chip.tx_calls == journal.snapshot()['attempts'] == 0


@pytest.mark.parametrize('flags', [0x200, 0x20, 0x22, 0x42])
def test_ack_can_arrive_during_upper_work_after_empty_timeout_or_error(rig, flags):
    chip, journal, worker = rig
    worker.radio.start_receive(authority_deadline=worker.lease_deadline)
    chip.flags = flags
    assert worker.receive(timeout=0) is None
    # Model the peer's encrypted ACK arriving during the upper loop's work.
    # Standby cannot receive it; the worker must already have rearmed RX.
    chip.sleep(.025)
    if chip.mode == 5:
        chip.rx, chip.flags = b'encrypted peer ACK', 2
    received = worker.receive(timeout=0)
    assert received is not None and received[1].packet == b'encrypted peer ACK'
    assert worker.radio.receiving and chip.mode == 5 and not worker.fenced
    assert chip.tx_calls == journal.snapshot()['attempts'] == 0


@pytest.mark.parametrize('fault', ['refused', 'expired', 'lease_expired',
                                 'radio', 'driver', 'post_rearm_refused'])
@pytest.mark.parametrize('flags', [2, 0x200, 0x20, 0x22, 0x42])
def test_rx_rearm_keeps_current_authority_and_contains_original_owner(rig, fault, flags):
    chip, journal, worker = rig
    original_radio = worker.radio
    foreign = SimpleNamespace(io=Chip())
    worker.radio.start_receive(authority_deadline=worker.lease_deadline)
    chip.flags = flags
    before = len(chip.commands)
    calls = 0
    def authorize():
        nonlocal calls
        calls += 1
        if calls == 2:
            if fault == 'refused': return False
            if fault == 'expired': chip.time = worker.deadline
            if fault == 'lease_expired': chip.time = worker.lease_deadline
            if fault == 'radio': worker.radio = foreign
        if calls == 3 and fault == 'post_rearm_refused': return False
        return True
    worker._test_authorize_state['callback'] = authorize
    transfer = chip.transfer
    def failed_rx(command):
        if command[0] == 0x82: raise OSError('partial RX command')
        return transfer(command)
    if fault == 'driver': chip.transfer = failed_rx
    with pytest.raises((HardwareError, OSError)):
        worker.receive(timeout=0)
    assert worker.fenced and original_radio.fenced
    assert chip.resets[-1] is False and chip.tx_calls == 0
    assert journal.snapshot()['attempts'] == 0
    assert not foreign.io.commands and not foreign.io.resets
    if fault in ('refused', 'expired', 'lease_expired', 'radio'):
        assert not any(c[0] == 0x82 for c in chip.commands[before:])


@pytest.mark.parametrize('flags', [2, 0x200, 0x20, 0x22, 0x42])
def test_rx_rearm_cannot_extend_original_finite_window(rig, flags):
    chip, _, worker = rig
    original_end = min(worker.deadline, worker.lease_deadline)
    worker.radio.start_receive(authority_deadline=original_end)
    chip.flags = flags
    start_receive = worker.radio.start_receive
    deadlines = []
    def changed_authority():
        worker.deadline += 100
        worker.lease_deadline += 100
        return True
    def record_receive(*, authority_deadline):
        deadlines.append(authority_deadline)
        return start_receive(authority_deadline=authority_deadline)
    worker._test_authorize_state['callback'] = changed_authority
    worker.radio.start_receive = record_receive
    assert (worker.receive(timeout=0) is not None) is (flags == 2)
    assert deadlines == [original_end]
