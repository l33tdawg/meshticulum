"""Host profiling example wiring uses model resources and never opens ports.

The owner/authority below are deliberately test doubles. These checks concern
optional diagnostics and cleanup ordering, not grant or hardware qualification.
"""
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.burst_protocol import POLICY
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.host_timing import HostPhaseTimings, Phase, phase
from meshticulum.bench.serial_protocol import FirmwareIdentity, RadioProfile
from meshticulum.bench.serial_worker import SerialWorker, GfskSerialWorker, Gfsk250SerialWorker

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def examples(monkeypatch):
    def load(name):
        spec = importlib.util.spec_from_file_location(name, ROOT / 'examples' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module
    return load('radio_file'), load('radio_bundle')


@pytest.mark.parametrize('which', ['file', 'bundle'])
@pytest.mark.parametrize('enabled', [False, True])
def test_both_transfer_parsers_make_host_profiling_opt_in(examples, monkeypatch, capsys, which, enabled):
    file, bundle = examples
    module = file if which == 'file' else bundle
    args = ['transfer', '--manifest-sha256', '0' * 64,
        '--grant-directory', 'unused-grant', '--grant-sha256', '1' * 64,
        '--file-issued-epoch-us', '1', '--file-expires-epoch-us', '2',
        '--admission', 'unused-admission', '--admission-sha256', '2' * 64,
        '--firmware-source', 'unused-source', '--firmware-source-sha256', '3' * 64,
        '--unit', 'unit-a', '--interface', 'usb', '--device', '/never-open', '--usb-serial', 'unused']
    if which == 'file':
        args += ['--manifest', 'unused-manifest', '--chunks', 'unused-chunks', '--chunk', '0']
    else:
        args += ['--pack', 'unused-pack', '--trust-root', 'unused-root',
                 '--trust-root-sha256', '4' * 64, '--part', '0', '--state', 'unused-state']
    if enabled:
        args.append('--profile-host')
    observed = []
    def transfer(parsed):
        observed.append(parsed.profile_host)
        return {'model_parser_only': True}
    monkeypatch.setattr(module, 'transfer_chunk' if which == 'file' else 'transfer', transfer)
    monkeypatch.setattr(sys, 'argv', ['example', *args])
    module.main()
    assert observed == [enabled]
    assert json.loads(capsys.readouterr().out) == {'model_parser_only': True}


@pytest.fixture
def run_model(examples, monkeypatch, tmp_path):
    example = examples[0]
    tmp_path.chmod(0o700)
    def run(*, enabled=True, operation_error=None, cleanup_error=None):
        calls, collectors = [], []
        profile = RadioProfile(921375000, 500000, 7, 5, 0, 8, 0x42, 60000, 128, 20_000_000)
        raw = json.dumps({'profile': asdict(profile)}).encode()
        limits = example.OperationLimits.from_plan(json.loads(raw))
        authority = SimpleNamespace(plan_bytes=raw, operation_limits=limits,
            profile=profile, data_burst_policy=None, sends=True,
            grant=SimpleNamespace(digest=b'g' * 32), local_peer=object(),
            application_live=lambda: True,
            deadlines=SimpleNamespace(application_deadline_monotonic=60.),
            close=lambda: calls.append('authority.close'))
        class Channel:
            def __init__(self, *args, host_timing=None, **kwargs):
                collectors.append(host_timing)
            def close(self):
                calls.append('transport.close')
        class Worker:
            def __init__(self, channel, *, host_timing=None):
                self.host_timing = host_timing
                collectors.append(host_timing)
            def discover(self, **kwargs):
                return FirmwareIdentity(bytes.fromhex('01020304050607080000000000000001'), 1, 'timing-test')
            def arm(self, **kwargs):
                assert kwargs['authorize']()
            def disarm(self):
                with phase(self.host_timing, Phase.RELEASE_COMMIT):
                    calls.append('worker.disarm')
        def journal(*args, **kwargs):
            value = AttemptJournal(*args, **kwargs)
            collectors.append(value.host_timing)
            original_close = value.close
            def close():
                calls.append('journal.close')
                original_close()
            value.close = close
            return value
        def factory(owner, worker, journal, cipher):
            collector = journal.host_timing
            collectors.append(collector)
            class Session:
                endpoint = SimpleNamespace(receipt_confirmation_enabled=False)
                steps = 0
                def step(self):
                    with phase(collector, Phase.FLOW_STEP):
                        self.steps += 1
                        if operation_error is not None:
                            raise operation_error
                        return self.steps == 2
                def close(self):
                    calls.append('session.close')
                    if cleanup_error is not None:
                        raise cleanup_error
            return Session()
        monkeypatch.setattr(example, 'ExclusiveSerial', Channel)
        monkeypatch.setattr(example, 'SerialWorker', Worker)
        monkeypatch.setattr(example, 'AttemptJournal', journal)
        monkeypatch.setattr(example, 'HeldHardware', lambda *a, **k: SimpleNamespace(
            current=lambda: True, close=lambda: calls.append('owner.close')))
        monkeypatch.setattr(example, 'RunAuthority', lambda *a, **k: authority)
        monkeypatch.setattr(example, 'host_boot', lambda: 'source-model')
        monkeypatch.setattr(example, 'SessionStore', lambda *a, **k: SimpleNamespace(
            retire=lambda: calls.append('store.retire'), close=lambda: calls.append('store.close')))
        monkeypatch.setattr(example, 'SessionCipher', lambda *a, **k: object())
        monkeypatch.setattr(example.time, 'sleep', lambda seconds: calls.append(('sleep', seconds)))
        args = SimpleNamespace(profile_host=enabled, interface='usb', device='/never-open',
            usb_serial='unused', grant_directory=tmp_path, unit='unit-a', chunk=0)
        window = SimpleNamespace(current=lambda: True, deadline_us=60_000_000,
                                 pin_mcu=lambda identity: None, check=lambda: None)
        invoke = lambda: example.run_object(args, raw, None, 'timing-test', None, window, factory)
        return invoke, calls, collectors
    return run


CLEANUP = ['session.close', 'worker.disarm', 'store.retire', 'store.close',
           'journal.close', 'authority.close', 'owner.close', 'transport.close']


@pytest.mark.parametrize('enabled', [False, True])
def test_shared_collector_is_reported_after_successful_cleanup(run_model, enabled):
    invoke, calls, collectors = run_model(enabled=enabled)
    result = invoke()
    assert calls == [('sleep', .005), *CLEANUP]
    assert result['sender_peer_receipt'] is True
    assert result['whole_file_verified'] is False
    assert all(value is collectors[0] for value in collectors)
    if enabled:
        assert type(collectors[0]) is HostPhaseTimings
        stats = result['host_phase_timings']['phases']
        assert stats['host_loop_sleep']['count'] == 1
        assert stats['release_commit']['count'] == 1  # Captured during cleanup.
        assert stats['journal_transaction']['count'] >= 2
        assert stats['flow_step']['count'] == 2
    else:
        assert collectors[0] is None
        assert 'host_phase_timings' not in result


def test_failure_report_keeps_original_errors_and_includes_later_cleanup(run_model):
    original, cleanup = KeyboardInterrupt('original'), OSError('cleanup')
    invoke, calls, collectors = run_model(operation_error=original, cleanup_error=cleanup)
    with pytest.raises(BaseExceptionGroup) as caught:
        invoke()
    assert caught.value.exceptions == (original, cleanup)
    assert calls == CLEANUP
    assert caught.value.host_phase_timings['phases']['release_commit']['count'] == 1
    assert all(value is collectors[0] for value in collectors)


@pytest.mark.parametrize('diagnostic_error', [RuntimeError, KeyboardInterrupt, SystemExit])
def test_snapshot_failure_does_not_replace_original_exception_group(run_model, monkeypatch, diagnostic_error):
    original, cleanup = OSError('original'), ValueError('cleanup')
    invoke, calls, _ = run_model(operation_error=original, cleanup_error=cleanup)
    def fail_snapshot(self):
        raise diagnostic_error('private diagnostic error')
    monkeypatch.setattr(HostPhaseTimings, 'snapshot', fail_snapshot)
    with pytest.raises(BaseExceptionGroup) as caught:
        invoke()
    assert caught.value.exceptions == (original, cleanup)
    assert caught.value.host_phase_timings_unavailable is True
    assert not hasattr(caught.value, 'host_phase_timings')
    assert calls == CLEANUP


def test_ordinary_snapshot_failure_preserves_success(run_model, monkeypatch):
    invoke, calls, _ = run_model()
    def fail_snapshot(self):
        raise RuntimeError('private diagnostic error')
    monkeypatch.setattr(HostPhaseTimings, 'snapshot', fail_snapshot)
    result = invoke()
    assert result['sender_peer_receipt'] is True
    assert result['host_phase_timings_unavailable'] is True
    assert 'host_phase_timings' not in result
    assert 'private diagnostic' not in json.dumps(result)
    assert calls == [('sleep', .005), *CLEANUP]


@pytest.mark.parametrize('worker_class', [SerialWorker, GfskSerialWorker, Gfsk250SerialWorker, BurstSerialWorker])
@pytest.mark.parametrize('enabled', [False, True])
def test_all_serial_worker_constructors_accept_shared_collector_without_io(worker_class, enabled):
    class UnusableChannel:
        def __getattribute__(self, name):
            raise AssertionError('constructor touched channel: ' + name)
    channel = UnusableChannel()
    collector = HostPhaseTimings() if enabled else None
    kwargs = {'policy': dict(POLICY)} if worker_class is BurstSerialWorker else {}
    worker = worker_class(channel, host_timing=collector, **kwargs)
    assert worker.channel is channel and worker.host_timing is collector
    with pytest.raises(ValueError, match='HostPhaseTimings'):
        worker_class(channel, host_timing=object(), **kwargs)


def test_chunk_transfer_forwards_journal_collector_to_flow(examples, monkeypatch):
    example = examples[0]
    collector, observed = HostPhaseTimings(), []
    monkeypatch.setattr(example, 'BudgetPolicy', lambda *a, **k: object())
    monkeypatch.setattr(example, 'Ledger', lambda *a, **k: object())
    def flow(**kwargs):
        observed.append(kwargs['host_timing'])
        return object()
    monkeypatch.setattr(example, 'BenchFlow', flow)
    descriptor = {'source_fixture': True}
    authority = SimpleNamespace(plan={'object': descriptor},
        profile=SimpleNamespace(lease_ms=1000, max_boot_estimated_airtime_us=500_000),
        ledger_epoch=object(), flow_key=object(), contract=object(), carrier_profile=object(),
        scheduler_epoch=object(), broker_epoch=object(), sends=True,
        application_deadline_us=example.ACK_GRACE_US + 1)
    example.ChunkTransfer(authority, object(), SimpleNamespace(host_timing=collector),
        object(), descriptor, object(), clock=lambda: (0, 0))
    assert observed == [collector]
