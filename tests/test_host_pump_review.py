"""Independent host wakeup controls; modeled completion supplies no RF evidence."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.burst_protocol import POLICY
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.host_pump import run_host_pump
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_worker import (
    SerialWorker, GfskSerialWorker, Gfsk250SerialWorker,
)
from meshticulum.bench.tx_burst import Stage
from tests import test_burst_sf5_recovery as model
from tests.test_host_timing_example import CLEANUP, examples, run_model


@pytest.mark.parametrize('hint', [1, 'ready', object(), False, None])
def test_nonboolean_capacity_hint_never_removes_the_bounded_wait(hint):
    count, waits = [0], []
    def step():
        count[0] += 1
        return count[0] == 2
    run_host_pump(step, check=lambda: None, wait=waits.append,
        clock_us=lambda: 1, deadline_us=10_000, progress=lambda: count[0],
        can_repump=lambda: hint)
    assert waits == [.005]


@pytest.mark.parametrize('where', ['check', 'capacity', 'wait'])
def test_post_step_callback_failure_preserves_identity_and_prevents_another_step(where):
    error = KeyboardInterrupt('source cancellation')
    calls = []
    def fail(*args):
        raise error
    def step():
        calls.append('step')
        return False
    with pytest.raises(KeyboardInterrupt) as caught:
        run_host_pump(step, check=fail if where == 'check' else lambda: None,
            wait=fail if where == 'wait' else lambda _: pytest.fail('unexpected wait'),
            clock_us=lambda: 1, deadline_us=10_000,
            progress=lambda: len(calls),
            can_repump=fail if where == 'capacity' else lambda: where != 'wait')
    assert caught.value is error and calls == ['step']


@pytest.mark.parametrize('worker_type', [SerialWorker, GfskSerialWorker, Gfsk250SerialWorker])
@pytest.mark.parametrize('capability', [None, False, 0])
def test_legacy_serial_modem_channels_without_callable_readiness_use_fallback(worker_type, capability):
    channel = SimpleNamespace(wait_readable=capability,
        read=lambda _: pytest.fail('readiness consumed bytes'),
        write=lambda *args: pytest.fail('readiness wrote bytes'))
    assert worker_type(channel).wait_for_activity(.005) is None


@pytest.mark.parametrize('result', [None, 0, 1, 'ready', object()])
def test_callable_channel_must_return_exact_boolean_readiness(result):
    worker = SerialWorker(SimpleNamespace(wait_readable=lambda _: result))
    with pytest.raises(HardwareError, match='invalid_serial_activity_result'):
        worker.wait_for_activity(.005)


def test_host_unknown_retention_cannot_make_readiness_spin_or_refund_capacity():
    waits = []
    channel = SimpleNamespace(wait_readable=lambda timeout: waits.append(timeout) or False)
    worker = BurstSerialWorker(channel, policy=POLICY)
    worker._open = object()
    worker._burst_count = 4
    worker._physical[9] = dict(event=SimpleNamespace(stage=Stage.UNKNOWN), host_unknown=True)
    assert worker.wait_for_activity(.005) is False
    assert waits == [.005] and not worker.has_capacity
    assert 9 in worker._physical
    terminal = object()
    worker._terminals.append(terminal)
    assert worker.wait_for_activity(.005) is True
    assert list(worker._terminals) == [terminal] and not worker.has_capacity
    assert waits == [.005]


def test_hat_without_readiness_retains_sleep_and_complete_cleanup(examples, run_model, monkeypatch):
    invoke, calls, _ = run_model()
    example = examples[0]
    original_run, original_authority = example.run_object, example.RunAuthority
    monkeypatch.setattr(example, 'LinuxHAT', lambda: SimpleNamespace(
        boot_id='01020304-0506-0708-0000-000000000001',
        close=lambda: calls.append('transport.close')))
    monkeypatch.setattr(example, 'HATWorker', lambda transport, **kwargs:
        example.SerialWorker(transport, host_timing=kwargs['journal'].host_timing))
    def run_as_hat(args, raw, *rest, **kwargs):
        args = SimpleNamespace(**{**vars(args), 'interface': 'hat'})
        raw = json.dumps({**json.loads(raw), 'antenna_confirmed': True}).encode()
        authority = original_authority()
        authority.plan_bytes = raw
        monkeypatch.setattr(example, 'RunAuthority', lambda *a, **k: authority)
        return original_run(args, raw, *rest, **kwargs)
    monkeypatch.setattr(example, 'run_object', run_as_hat)
    assert invoke()['sender_peer_receipt'] is True
    assert calls == [('sleep', .005), *CLEANUP]


@pytest.mark.parametrize('preamble', [8, 12])
def test_sf7_queue_keeps_clean_delivery_and_paid_outcomes_with_host_driver(tmp_path, monkeypatch, preamble):
    profile = replace(model.PROFILE, spreading_factor=7, preamble=preamble)
    monkeypatch.setattr(model, 'PROFILE', profile)
    monkeypatch.setattr(model, 'ADMISSION', RadioAdmission('MY', ((profile.frequency_hz,
        profile.bandwidth_hz, profile.spreading_factor, profile.coding_rate,
        profile.chip_power_dbm, profile.preamble, profile.sync_word),),
        10000, 180000, 256, 60_000_000))
    pairs = []
    for enabled in (False, True):
        results, air = model.exercise(tmp_path / str(enabled), host_pump=enabled,
                                      receipt_confirmation=True)
        model.assert_complete(results, air)
        assert not any(frame.collision or frame.lost for frame in air.frames)
        assert all(result['snapshot']['retransmits'] == 0 for result in results)
        pairs.append([result['snapshot']['attempt_journal'] for result in results])
    assert pairs[0] == pairs[1]
