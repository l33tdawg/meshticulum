"""Explicit simulated monotonic epoch; these source controls are not physical RF proof."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.hat_worker import HATWorker, journal_binding
from meshticulum.bench.linux_hat import HardwareError, LinuxHAT
from tests.test_meshticulum_bench_sx1262 import Chip, PROFILE


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    chip = Chip()
    active = {'armed': False, 'calls': 0, 'callback': lambda: None, 'authorize': lambda: True}
    # Caller clock is allowed to trigger owner revocation before authorization.
    # The explicit trusted model clock is only a side-effect-free chip.time read.
    def transport_clock():
        if active['armed']:
            active['calls'] += 1
            active['callback']()
        return chip.time
    chip.monotonic = transport_clock
    context = b'c' * 32
    journal = AttemptJournal(tmp_path / 'attempts.sqlite',
        binding=journal_binding(context, chip.boot_id, PROFILE), max_attempts=20,
        max_airtime_us=10000000, create=True)
    worker = HATWorker(chip, journal=journal, profile=PROFILE, context_digest=context,
        authorize=lambda: active['authorize'](), authority_deadline=200, antenna_confirmed=True,
        monotonic=lambda: chip.time, time_basis='model-monotonic')
    try:
        yield chip, journal, worker, active
    finally:
        journal.close()


def ready(chip, worker):
    worker.radio.start_receive(authority_deadline=worker.lease_deadline)
    chip.commands.clear()


@pytest.mark.parametrize('phase', ['entry', 'after_configure', 'post_receive'])
def test_transport_clock_revocation_never_causes_later_rx_or_packet_return(rig, phase):
    chip, journal, worker, active = rig
    if phase != 'after_configure': ready(chip, worker)
    if phase == 'post_receive': chip.flags = 2
    call = 1 if phase == 'entry' else 2
    granted, cutoff = [True], []
    active['authorize'] = lambda: granted[0]
    def revoke():
        if active['calls'] == call:
            granted[0] = False
            cutoff.append(len(chip.commands))
    active.update(armed=True, callback=revoke)
    with pytest.raises(HardwareError, match='hat_authority_refused'):
        worker.receive()
    assert cutoff and not chip.commands[cutoff[0]:]
    assert worker.fenced and chip.resets[-1] is False
    assert journal.snapshot()['attempts'] == 0 and chip.tx_calls == 0


@pytest.mark.parametrize('where', ['before_configure', 'before_poll', 'post_receive'])
def test_slow_authorization_crossing_original_lease_refuses_before_next_effect(rig, where):
    chip, journal, worker, active = rig
    if where != 'before_configure': ready(chip, worker)
    if where == 'post_receive': chip.flags = 2
    count, cutoff = [0], []
    def authorize():
        count[0] += 1
        trigger = {'before_configure': 1, 'before_poll': 1, 'post_receive': 2}[where]
        if count[0] == trigger:
            cutoff.append(len(chip.commands))
            chip.time = worker.lease_deadline + .001
        return True
    active['authorize'] = authorize
    with pytest.raises(HardwareError, match='hat_authority_expired'):
        worker.receive()
    assert cutoff and not chip.commands[cutoff[0]:]
    assert worker.fenced and chip.tx_calls == 0


@pytest.mark.parametrize('phase', ['transport_clock', 'authorize'])
@pytest.mark.parametrize('fault', ['worker_io', 'radio_io', 'radio', 'clock', 'driver_clock',
                                  'time_basis', 'boot', 'profile', 'journal', 'authorize', 'guard'])
def test_callback_ref_mutation_refuses_owned_rx_and_never_contains_foreign(rig, phase, fault):
    chip, journal, worker, active = rig
    ready(chip, worker)
    foreign = Chip()
    original_radio = worker.radio
    def mutate():
        if fault == 'worker_io': worker.io = foreign
        elif fault == 'radio_io': original_radio.io = foreign
        elif fault == 'radio': worker.radio = SimpleNamespace(io=foreign)
        elif fault == 'clock': worker._clock = lambda: 0
        elif fault == 'driver_clock': original_radio._now = lambda: 0
        elif fault == 'time_basis': worker.time_basis = 'host-monotonic'
        elif fault == 'boot': chip.boot_id = foreign.boot_id[:-1] + '2'
        elif fault == 'profile': worker.profile = replace(PROFILE, lease_ms=60001)
        elif fault == 'journal': worker.journal = object()
        elif fault == 'authorize': worker.authorize = lambda: True
        elif fault == 'guard': worker._owned = lambda: 1000000
    if phase == 'transport_clock': active.update(armed=True, callback=mutate)
    else:
        def authorize(): mutate(); return True
        active['authorize'] = authorize
    with pytest.raises(HardwareError): worker.receive()
    assert worker.fenced and not chip.commands and not foreign.commands and not foreign.resets
    assert journal.snapshot()['attempts'] == 0


@pytest.mark.parametrize('error', [RuntimeError, KeyboardInterrupt])
def test_callback_failure_contains_original_and_propagates(rig, error):
    chip, journal, worker, active = rig
    ready(chip, worker)
    def refused(): raise error('callback failed')
    active['authorize'] = refused
    with pytest.raises(error): worker.receive()
    assert worker.fenced and chip.resets[-1] is False and not chip.commands


def test_driver_clock_does_not_invoke_caller_callback_after_permission(rig):
    chip, journal, worker, active = rig
    calls = []
    active.update(armed=True, callback=lambda: calls.append('clock'))
    active['authorize'] = lambda: calls.append('authorize') or True
    assert worker.receive() is None
    assert calls == ['clock', 'authorize', 'clock', 'authorize']
    assert any(c[0] == 0x82 for c in chip.commands) and any(c[0] == 0x12 for c in chip.commands)
    assert not worker.fenced and journal.snapshot()['attempts'] == 0


def test_default_refuses_implicit_simulated_epoch_before_spi(rig):
    chip, journal, worker, active = rig
    chip.commands.clear()
    with pytest.raises(HardwareError, match='hat_clock_basis_unverified'):
        HATWorker(chip, journal=journal, profile=PROFILE, context_digest=b'c'*32,
            authorize=lambda: True, authority_deadline=200, antenna_confirmed=True)
    assert not chip.commands


def test_native_uses_existing_host_epoch_without_translating_old_deadline(tmp_path):
    # The Linux transport hardware calls are modeled; the default clock is the
    # real captured host clock. No LinuxHAT.__init__, devices or RF are invoked.
    tmp_path.chmod(0o700)
    chip = Chip()
    transport = LinuxHAT.__new__(LinuxHAT)
    transport.boot_id = chip.boot_id
    for name in ('transfer', 'busy', 'sleep', 'reset', 'receive_switch'):
        setattr(transport, name, getattr(chip, name))
    context = b'c' * 32
    journal = AttemptJournal(tmp_path/'attempts.sqlite',
        binding=journal_binding(context, chip.boot_id, PROFILE), max_attempts=20,
        max_airtime_us=10000000, create=True)
    try:
        with pytest.raises(HardwareError, match='hat_authority_expired'):
            HATWorker(transport, journal=journal, profile=PROFILE, context_digest=context,
                authorize=lambda: True, authority_deadline=1, antenna_confirmed=True)
        assert not chip.commands
        worker = HATWorker(transport, journal=journal, profile=PROFILE, context_digest=context,
            authorize=lambda: True, authority_deadline=time.monotonic()+120, antenna_confirmed=True)
        assert worker._clock is time.monotonic and worker.time_basis == 'host-monotonic'
        with pytest.raises(HardwareError, match='hat_clock_basis_unverified'):
            HATWorker(transport, journal=journal, profile=PROFILE, context_digest=context,
                authorize=lambda: True, authority_deadline=200, antenna_confirmed=True,
                monotonic=lambda: 100, time_basis='model-monotonic')
    finally: journal.close()


def test_received_timestamp_and_original_lease_use_same_declared_epoch(rig):
    chip, journal, worker, active = rig
    ready(chip, worker); chip.flags = 2
    at, packet = worker.receive()
    assert packet.packet == chip.rx and at == chip.time and at < worker.lease_deadline


def test_original_lease_cannot_be_extended_by_mutated_deadline_fields(rig):
    chip, journal, worker, active = rig
    ready(chip, worker)
    original = worker.lease_deadline
    worker.deadline = worker.lease_deadline = 1000000
    chip.time = original + .001
    with pytest.raises(HardwareError, match='hat_authority_expired'): worker.receive()
    assert not chip.commands and worker.fenced


@pytest.mark.parametrize('fault', ['both_handles', 'worker_io', 'radio', 'clock'])
def test_preentry_replacement_never_operates_or_contains_foreign(rig, fault):
    chip, journal, worker, active = rig
    ready(chip, worker)
    foreign = Chip()
    foreign_radio = SimpleNamespace(io=foreign, contain=lambda: foreign.reset(False))
    if fault in ('both_handles', 'worker_io'): worker.io = foreign
    if fault in ('both_handles', 'radio'): worker.radio = foreign_radio
    if fault == 'clock': worker._clock = lambda: 0
    with pytest.raises(HardwareError): worker.receive()
    assert worker.fenced and not foreign.commands and not foreign.resets
    assert chip.resets[-1] is False and not chip.commands


@pytest.mark.parametrize('owner', ['pid', 'thread'])
def test_changed_owner_does_not_operate_or_contain_original(rig, monkeypatch, owner):
    chip, journal, worker, active = rig
    ready(chip, worker)
    prior_resets = list(chip.resets)
    import meshticulum.bench.hat_worker as module
    if owner == 'pid': monkeypatch.setattr(module.os, 'getpid', lambda: -1)
    else: monkeypatch.setattr(module.threading, 'get_ident', lambda: -1)
    with pytest.raises(HardwareError): worker.receive()
    assert not chip.commands and chip.resets == prior_resets


@pytest.mark.parametrize('target', ['authorize', '_owned'])
def test_between_operation_substitution_cannot_replace_captured_authority_or_owner(rig, target):
    chip, journal, worker, active = rig
    ready(chip, worker)
    assert worker.receive() is None
    chip.commands.clear()
    foreign_calls = []
    setattr(worker, target, lambda: foreign_calls.append(target) or True)
    with pytest.raises(HardwareError): worker.receive()
    assert not foreign_calls and not chip.commands and worker.fenced
    assert journal.snapshot()['attempts'] == 0


def test_dispatch_is_not_a_mutable_callback_field(rig):
    chip, journal, worker, active = rig
    with pytest.raises(AttributeError): worker._dispatch = lambda: 1000000
    assert worker.receive() is None and not worker.fenced



def test_linuxhat_subclass_cannot_select_model_fallback(tmp_path):
    class NativeSubclass(LinuxHAT): pass
    transport = NativeSubclass.__new__(NativeSubclass)
    transport.boot_id = Chip.boot_id
    tmp_path.chmod(0o700)
    context = b'c' * 32
    journal = AttemptJournal(tmp_path/'attempts.sqlite',
        binding=journal_binding(context, transport.boot_id, PROFILE), max_attempts=20,
        max_airtime_us=10000000, create=True)
    try:
        with pytest.raises(HardwareError, match='hat_clock_basis_unverified'):
            HATWorker(transport, journal=journal, profile=PROFILE, context_digest=context,
                authorize=lambda: True, authority_deadline=200, antenna_confirmed=True,
                monotonic=lambda: 100, time_basis='model-monotonic')
    finally: journal.close()


def test_node_monotonic_ns_and_linuxhat_default_share_existing_epoch():
    lower = time.monotonic_ns() / 1000000000
    native = LinuxHAT.monotonic()
    upper = time.monotonic_ns() / 1000000000
    assert lower <= native <= upper



@pytest.mark.parametrize('phase', ['transport_clock', 'authorize'])
def test_fence_set_by_external_callback_prevents_later_rx(rig, phase):
    chip, journal, worker, active = rig
    ready(chip, worker)
    def fence(): worker.fenced = True
    if phase == 'transport_clock': active.update(armed=True, callback=fence)
    else: active['authorize'] = lambda: fence() or True
    with pytest.raises(HardwareError): worker.receive()
    assert not chip.commands and worker.fenced
    worker.fenced = worker.radio.fenced = False
    active.update(armed=False, authorize=lambda: True)
    with pytest.raises(HardwareError): worker.receive()
    assert not chip.commands and worker.fenced



@pytest.mark.parametrize('operation', ['assign', 'delete'])
def test_initialized_mangled_guard_cannot_be_replaced_between_operations(rig, operation):
    chip, journal, worker, active = rig
    ready(chip, worker)
    active['authorize'] = lambda: False
    with pytest.raises(HardwareError, match='hat_owner_guard_changed'):
        if operation == 'assign': worker._HATWorker__dispatch = lambda **kw: 200
        else: del worker._HATWorker__dispatch
    foreign = []
    worker.authorize = lambda: foreign.append(1) or True
    with pytest.raises(HardwareError): worker.receive()
    assert not foreign and not chip.commands and worker.fenced
    assert journal.snapshot()['attempts'] == 0


@pytest.mark.parametrize('phase', ['transport_clock', 'authorize'])
def test_mangled_guard_replacement_in_callback_refuses_and_contains_original(rig, phase):
    chip, journal, worker, active = rig
    ready(chip, worker)
    def replace_guard(): worker._HATWorker__dispatch = lambda **kw: 200
    if phase == 'transport_clock': active.update(armed=True, callback=replace_guard)
    else: active['authorize'] = lambda: replace_guard() or True
    with pytest.raises(HardwareError, match='hat_owner_guard_changed'): worker.receive()
    assert worker.fenced and not chip.commands and chip.resets[-1] is False



@pytest.mark.parametrize('error', [RuntimeError, KeyboardInterrupt])
def test_failed_prespi_callback_never_contains_replacement_and_settles_original(rig, error):
    chip, journal, worker, active = rig
    foreign = Chip()
    foreign_radio = SimpleNamespace(io=foreign, contain=lambda: foreign.reset(False))
    calls = []
    def authorize():
        calls.append(1)
        if len(calls) == 2:
            worker.radio = foreign_radio
            raise error('changed before SPI')
        return True
    active['authorize'] = authorize
    with pytest.raises(error): worker.transmit(b'encrypted packet')
    assert not foreign.resets and not foreign.commands
    assert chip.resets[-1] is False and chip.tx_calls == 0 and worker.fenced
    state = journal.snapshot()
    assert state['attempts'] == 1 and state['fenced']
    assert journal.db.execute('SELECT state FROM attempts').fetchone()[0] == 'UNKNOWN'


def test_settlement_failure_never_contains_foreign_driver(rig):
    chip, journal, worker, active = rig
    foreign = Chip()
    def failed_settle(*_args):
        worker.radio = SimpleNamespace(io=foreign, contain=lambda: foreign.reset(False))
        raise OSError('settlement failed')
    journal.settle = failed_settle
    with pytest.raises(OSError): worker.transmit(b'encrypted packet')
    assert not foreign.commands and not foreign.resets and chip.resets[-1] is False
    assert worker.fenced and chip.tx_calls == 1
    assert journal.db.execute('SELECT state FROM attempts').fetchone()[0] == 'RESERVED'



@pytest.mark.parametrize('fault', ['radio', 'worker_io', 'radio_io'])
def test_disarm_never_closes_foreign_after_reference_refusal(rig, fault):
    chip, journal, worker, active = rig
    ready(chip, worker)
    foreign = Chip()
    closed = []
    original_radio = worker.radio
    if fault == 'radio': worker.radio = SimpleNamespace(io=foreign, close=lambda: closed.append(1))
    elif fault == 'worker_io': worker.io = foreign
    else: original_radio.io = foreign
    with pytest.raises(HardwareError): worker.receive()
    if fault == 'radio_io':
        with pytest.raises(HardwareError, match='hat_cleanup_owner_changed'): worker.disarm()
    else: worker.disarm()
    assert not closed and not foreign.commands and not foreign.resets


def test_expired_authority_still_allows_original_owned_disarm(rig):
    chip, journal, worker, active = rig
    ready(chip, worker)
    chip.time = worker.lease_deadline + 1
    active['authorize'] = lambda: False
    worker.disarm()
    assert not worker.radio.receiving and worker.lease_deadline == 0
    with pytest.raises(HardwareError): worker.receive()



def test_dictionary_shadow_cannot_replace_slot_owned_dispatch(rig):
    chip, journal, worker, active = rig
    ready(chip, worker)
    active['authorize'] = lambda: False
    original = worker._dispatch
    foreign = []
    worker.__dict__['_HATWorker__dispatch'] = lambda **kw: 200
    assert worker._dispatch is original
    worker.authorize = lambda: foreign.append(1) or True
    with pytest.raises(HardwareError): worker.receive()
    assert not foreign and not chip.commands and worker.fenced
    assert journal.snapshot()['attempts'] == 0
