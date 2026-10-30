"""File owner lifecycle controls with model hardware, never native/RF proof."""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.host_timing import HostPhaseTimings
from meshticulum.bench.serial_protocol import FirmwareIdentity, RadioProfile


@pytest.fixture
def model(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('file_owner_example',
        Path(__file__).resolve().parents[1] / 'examples/radio_file.py')
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    calls, workers, authorities, journals = [], [], [], []
    flags = dict(owner_live=True, disarm_failure=False, owner_close_failure=False)
    class Channel:
        def __init__(self, *args, **kwargs):
            self.host_timing = kwargs.get('host_timing')
            calls.append('transport.open')
        def close(self):
            calls.append('transport.close')
    class Owner:
        def __init__(self, *args, **kwargs):
            calls.append('owner.open')
        def current(self):
            return flags['owner_live']
        def close(self):
            calls.append('owner.close')
            if flags['owner_close_failure']:
                raise OSError('owner cleanup')
    profile = RadioProfile(921375000, 500000, 7, 5, 0, 8, 0x42, 60000, 128, 20_000_000)
    raw = json.dumps({'profile': asdict(profile)}).encode()
    def authority(path, **kwargs):
        assert kwargs['owner_probe']()
        value = SimpleNamespace(plan_bytes=raw, operation_limits=example.OperationLimits.from_plan(json.loads(raw)),
            profile=profile, data_burst_policy=None, sends=True,
            grant=SimpleNamespace(digest=bytes([len(authorities)+1])*32), local_peer=object(),
            application_live=lambda: kwargs['owner_probe'](),
            deadlines=SimpleNamespace(application_deadline_monotonic=60.),
            close=lambda: calls.append('authority.close'))
        authorities.append(value)
        return value
    class Worker:
        def __init__(self, channel, *, host_timing=None):
            self.channel, self.timing = channel, host_timing
            self.journal = None
            workers.append(self)
        def discover(self, **kwargs):
            return FirmwareIdentity(bytes.fromhex('01020304050607080000000000000001'), 1, 'model')
        def arm(self, **kwargs):
            assert self.journal is None and kwargs['authorize']()
            self.journal = kwargs['journal']
            assert self.channel.host_timing is self.timing
            calls.append('worker.arm')
        def disarm(self):
            calls.append('worker.disarm')
            if flags['disarm_failure']:
                raise OSError('disarm uncertain')
    def journal(*args, **kwargs):
        value = AttemptJournal(*args, **kwargs)
        journals.append(value)
        return value
    def factory(authority, worker, journal, cipher):
        request = journal.reserve(b'ciphertext', 100)
        journal.settle(request, b'ciphertext', 'RF_DONE', 'source_fixture')
        return SimpleNamespace(endpoint=SimpleNamespace(receipt_confirmation_enabled=False),
            step=lambda: True, close=lambda: calls.append('session.close'))
    monkeypatch.setattr(example, 'ExclusiveSerial', Channel)
    monkeypatch.setattr(example, 'HeldHardware', Owner)
    monkeypatch.setattr(example, 'host_boot', lambda: 'model-boot')
    monkeypatch.setattr(example, 'RunAuthority', authority)
    monkeypatch.setattr(example, 'SerialWorker', Worker)
    monkeypatch.setattr(example, 'AttemptJournal', journal)
    monkeypatch.setattr(example, 'SessionStore', lambda *a, **k: SimpleNamespace(
        retire=lambda: calls.append('store.retire'), close=lambda: calls.append('store.close')))
    monkeypatch.setattr(example, 'SessionCipher', lambda *a, **k: object())
    args = SimpleNamespace(interface='usb', device=Path('/never-open'), usb_serial='model',
        unit='sender', state=tmp_path/'state', file_issued_epoch_us=1,
        file_expires_epoch_us=240_000_001, profile_host=True, chunk=0)
    window = SimpleNamespace(raw=b'original-file-window', current=lambda: True,
        check=lambda: None, pin_mcu=lambda identity: None, deadline_us=60_000_000)
    def run(hardware, index):
        directory = tmp_path/str(index)
        directory.mkdir(mode=0o700)
        options = SimpleNamespace(**vars(args), grant_directory=directory)
        return example.run_object(options, raw, None, 'model', None, window, factory, hardware=hardware)
    return SimpleNamespace(example=example, args=args, window=window, run=run,
        calls=calls, workers=workers, authorities=authorities, journals=journals, flags=flags)


def test_file_ownership_reused_but_each_part_gets_fresh_authority_worker_and_debt(model):
    with model.example.FileHardware(model.args) as hardware:
        first, second = model.run(hardware, 0), model.run(hardware, 1)
        assert model.calls.count('transport.open') == model.calls.count('owner.open') == 1
        assert 'transport.close' not in model.calls
        assert len({id(w) for w in model.workers}) == 2
        assert len({id(a) for a in model.authorities}) == 2
        assert len({id(j) for j in model.journals}) == 2
        assert model.workers[0].channel is model.workers[1].channel is hardware.transport
        assert model.workers[0].timing is not model.workers[1].timing
        assert hardware.transport.host_timing is None
        assert first['attempt_journal']['attempts'] == second['attempt_journal']['attempts'] == 1
        assert first['attempt_journal']['reserved_estimated_airtime_us'] == 100
        assert model.calls.count('worker.disarm') == model.calls.count('store.retire') == 2
    assert model.calls[-2:] == ['owner.close', 'transport.close']


def test_failed_disarm_poisons_file_owner_and_retains_paid_journal(model):
    with model.example.FileHardware(model.args) as hardware:
        model.flags['disarm_failure'] = True
        with pytest.raises(ExceptionGroup):
            model.run(hardware, 0)
        assert hardware.poisoned and not hardware.active
        with pytest.raises(ValueError, match='not_available'):
            model.run(hardware, 1)
    original = model.journals[0]
    retained = AttemptJournal(model.args.state.parent/'0/attempts.db', binding=original.binding,
        max_attempts=original.max_attempts, max_airtime_us=original.max_airtime_us)
    try:
        assert retained.snapshot()['attempts'] == 1
        assert retained.snapshot()['reserved_estimated_airtime_us'] == 100
    finally:
        retained.close()


@pytest.mark.parametrize('field,value', [('unit', 'other'), ('state', Path('/other')),
    ('device', Path('/different-device')), ('file_expires_epoch_us', 999)])
def test_file_owner_cannot_be_rebound(model, field, value):
    with model.example.FileHardware(model.args) as hardware:
        changed = SimpleNamespace(**vars(model.args))
        setattr(changed, field, value)
        with pytest.raises(ValueError, match='binding_changed'):
            hardware.borrow(changed, model.window, None)
        assert not hardware.active


def test_original_window_owner_and_single_borrow_are_enforced(model):
    with model.example.FileHardware(model.args) as hardware:
        hardware.borrow(model.args, model.window, HostPhaseTimings())
        with pytest.raises(ValueError, match='not_available'):
            hardware.borrow(model.args, model.window, None)
        with pytest.raises(ValueError, match='still_borrowed'):
            hardware.close()
        hardware.release(True)
        model.window.raw = b'extended-file-window'
        with pytest.raises(ValueError, match='original_file_window_changed'):
            hardware.borrow(model.args, model.window, None)
        model.window.raw = b'original-file-window'
        model.flags['owner_live'] = False
        with pytest.raises(ValueError, match='owner_changed'):
            hardware.borrow(model.args, model.window, None)


def test_cross_thread_loan_refused_and_cleanup_preserves_original_error(model):
    with pytest.raises(BaseExceptionGroup) as caught:
        with model.example.FileHardware(model.args) as hardware:
            hardware.borrow(model.args, model.window, None)
            hardware.release(True)
            errors = []
            def borrow_elsewhere():
                try:
                    hardware.borrow(model.args, model.window, None)
                except ValueError as error:
                    errors.append(str(error))
            thread = threading.Thread(target=borrow_elsewhere)
            thread.start(); thread.join()
            assert errors == ['example_file_hardware_binding_changed']
            model.flags['owner_close_failure'] = True
            raise KeyboardInterrupt('original')
    assert isinstance(caught.value.exceptions[0], KeyboardInterrupt)
    assert model.calls[-2:] == ['owner.close', 'transport.close']


def test_foreign_thread_cannot_release_poison_or_close_live_owner(model):
    with model.example.FileHardware(model.args) as hardware:
        hardware.borrow(model.args, model.window, None)
        errors = []
        def mutate_elsewhere():
            for action in (lambda: hardware.release(True), hardware.poison, hardware.close):
                try:
                    action()
                except ValueError as error:
                    errors.append(str(error))
        thread = threading.Thread(target=mutate_elsewhere)
        thread.start(); thread.join()
        assert errors == ['example_file_hardware_binding_changed'] * 3
        assert hardware.active and not hardware.closed and not hardware.poisoned
        hardware.release(True)
