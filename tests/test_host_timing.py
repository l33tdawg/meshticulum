# SPDX-License-Identifier: Apache-2.0
"""Diagnostic clock failures must not become transport failures or authority."""
from contextlib import nullcontext
import json

import pytest

from meshticulum.bench import host_timing as timing
from meshticulum.bench.host_timing import HostPhaseTimings, Phase, check_timing, phase, timed


def clock(monkeypatch, *values):
    values = iter(values)
    def next_tick():
        value = next(values)
        if isinstance(value, BaseException):
            raise value
        return value
    monkeypatch.setattr(timing, 'perf_counter_ns', next_tick)


def row(value, label=Phase.QUEUE_CALL):
    return value.snapshot()['phases'][label.value]


def test_disabled_context_and_decorator_read_no_clock_and_preserve_results(monkeypatch):
    def forbidden():
        raise AssertionError('disabled instrumentation read a clock')
    monkeypatch.setattr(timing, 'perf_counter_ns', forbidden)
    marker = object()
    class Worker:
        @timed(Phase.QUEUE_CALL)
        def work(self):
            return marker
    assert isinstance(phase(None, Phase.QUEUE_CALL), nullcontext)
    with phase(None, Phase.QUEUE_CALL):
        assert Worker().work() is marker
    assert Worker.work.__name__ == 'work'


def test_nested_intervals_are_inclusive_and_do_not_share_start_state(monkeypatch):
    clock(monkeypatch, 10, 20, 40, 80)
    value = HostPhaseTimings()
    with phase(value, Phase.QUEUE_CALL):
        with phase(value, Phase.QUEUE_RESERVE):
            pass
    assert row(value)['total_ns'] == 70
    assert row(value, Phase.QUEUE_RESERVE)['total_ns'] == 20
    assert value.snapshot()['inclusive'] is True


def test_snapshot_only_contains_fixed_phase_keys_and_bounded_aggregate_fields(monkeypatch):
    clock(monkeypatch, 23, 31)
    value = HostPhaseTimings()
    secret = ValueError('private-message-payload-and-request-123')
    with pytest.raises(ValueError) as caught:
        with phase(value, Phase.QUEUE_CALL):
            raise secret
    assert caught.value is secret
    report = value.snapshot()
    assert report['schema'] == 'meshticulum.host-phase-timing.v1'
    assert report['clock'] == 'time.perf_counter_ns'
    assert set(report['phases']) == {label.value for label in Phase}
    assert all(set(stats) == {'count', 'total_ns', 'min_ns', 'max_ns',
                             'invalid', 'dropped', 'saturated'}
               for stats in report['phases'].values())
    assert row(value) == dict(count=1, total_ns=8, min_ns=8, max_ns=8,
                              invalid=0, dropped=0, saturated=False)
    assert 'private-message' not in json.dumps(report)
    report['phases']['queue_call']['count'] = 999
    assert row(value)['count'] == 1
    assert row(value, Phase.SERIAL_READ)['min_ns'] is None


@pytest.mark.parametrize('when', ['entry', 'exit'])
def test_ordinary_clock_failures_drop_measurement_without_changing_result(monkeypatch, when):
    failure = RuntimeError('private-clock-error')
    clock(monkeypatch, *( (failure,) if when == 'entry' else (100, failure)))
    value = HostPhaseTimings()
    marker = object()
    class Worker:
        host_timing = value
        @timed(Phase.QUEUE_CALL)
        def work(self):
            return marker
    assert Worker().work() is marker
    assert row(value) == dict(count=0, total_ns=0, min_ns=None, max_ns=None,
                              invalid=0, dropped=1, saturated=False)
    assert 'private-clock' not in json.dumps(value.snapshot())


@pytest.mark.parametrize('bad', [True, -1, 1.5, None, float('nan'), timing.MAX_VALUE + 1])
def test_invalid_entry_clock_values_are_missing_not_zero_duration(monkeypatch, bad):
    clock(monkeypatch, bad)
    value = HostPhaseTimings()
    ran = []
    with phase(value, Phase.QUEUE_CALL):
        ran.append(True)
    assert ran == [True]
    assert row(value)['count'] == 0
    assert row(value)['dropped'] == row(value)['invalid'] == 1


@pytest.mark.parametrize('end', [True, -1, 9, None, float('inf')])
def test_invalid_or_regressing_exit_clock_preserves_operation_exception(monkeypatch, end):
    clock(monkeypatch, 10, end)
    value = HostPhaseTimings()
    failure = OSError('original-operation')
    with pytest.raises(OSError) as caught:
        with phase(value, Phase.QUEUE_CALL):
            raise failure
    assert caught.value is failure
    assert row(value)['count'] == 0
    assert row(value)['dropped'] == row(value)['invalid'] == 1


@pytest.mark.parametrize('exception', [KeyboardInterrupt, SystemExit])
def test_cancellation_on_entry_stops_operation(monkeypatch, exception):
    cancellation = exception('cancel')
    clock(monkeypatch, cancellation)
    ran = []
    with pytest.raises(exception) as caught:
        with phase(HostPhaseTimings(), Phase.QUEUE_CALL):
            ran.append(True)
    assert caught.value is cancellation
    assert ran == []


@pytest.mark.parametrize('exception', [KeyboardInterrupt, SystemExit])
def test_cancellation_on_normal_exit_remains_effective(monkeypatch, exception):
    cancellation = exception('cancel')
    clock(monkeypatch, 1, cancellation)
    value = HostPhaseTimings()
    with pytest.raises(exception) as caught:
        with phase(value, Phase.QUEUE_CALL):
            pass
    assert caught.value is cancellation
    assert row(value)['dropped'] == 1


@pytest.mark.parametrize('diagnostic_error', [RuntimeError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('operation_error', [OSError, KeyboardInterrupt, SystemExit])
def test_diagnostic_exit_never_replaces_active_operation_exception(
        monkeypatch, diagnostic_error, operation_error):
    clock(monkeypatch, 1, diagnostic_error('diagnostic'))
    value = HostPhaseTimings()
    failure = operation_error('original')
    with pytest.raises(operation_error) as caught:
        with phase(value, Phase.QUEUE_CALL):
            raise failure
    assert caught.value is failure
    assert row(value)['dropped'] == 1


def test_duration_and_counter_saturation_are_explicit(monkeypatch):
    clock(monkeypatch, 0, timing.MAX_VALUE, 0, 5)
    value = HostPhaseTimings()
    with phase(value, Phase.QUEUE_CALL):
        pass
    with phase(value, Phase.QUEUE_CALL):
        pass
    assert row(value)['count'] == 2
    assert row(value)['total_ns'] == timing.MAX_VALUE
    assert row(value)['min_ns'] == 5
    assert row(value)['max_ns'] == timing.MAX_VALUE
    assert row(value)['saturated'] is True
    # Exercise unreachable-in-practice counter limits without an unbounded loop.
    index = timing._INDEX[Phase.QUEUE_CALL]
    value._rows[index] = (timing.MAX_VALUE, 0, None, None,
                          timing.MAX_VALUE, timing.MAX_VALUE, False)
    clock(monkeypatch, 1, 2)
    with phase(value, Phase.QUEUE_CALL):
        pass
    assert row(value)['count'] == timing.MAX_VALUE
    assert row(value)['saturated'] is True
    clock(monkeypatch, -1)
    with phase(value, Phase.QUEUE_CALL):
        pass
    assert row(value)['invalid'] == row(value)['dropped'] == timing.MAX_VALUE


def test_exact_collector_and_fixed_labels_are_validated():
    value = HostPhaseTimings()
    class Subclass(HostPhaseTimings):
        pass
    assert check_timing(None) is None and check_timing(value) is value
    for invalid in (True, object(), Subclass()):
        with pytest.raises(ValueError, match='HostPhaseTimings'):
            check_timing(invalid)
    with pytest.raises(ValueError, match='fixed Phase'):
        phase(value, 'request-123')
    with pytest.raises(ValueError, match='fixed Phase'):
        timed('private-message')


@pytest.mark.parametrize('method', ['_begin', '_finish'])
def test_collector_internal_ordinary_failure_cannot_change_operation(monkeypatch, method):
    clock(monkeypatch, 1, 2)
    value = HostPhaseTimings()
    def failed(*args):
        raise RuntimeError('diagnostic implementation failed')
    monkeypatch.setattr(HostPhaseTimings, method, failed)
    ran = []
    with phase(value, Phase.QUEUE_CALL):
        ran.append(True)
    assert ran == [True] and row(value)['dropped'] == 1


def test_measurement_allocation_failure_uses_disabled_context(monkeypatch):
    value = HostPhaseTimings()
    def failed(*args):
        raise MemoryError('diagnostic allocation failed')
    monkeypatch.setattr(timing, '_Measurement', failed)
    with phase(value, Phase.QUEUE_CALL):
        pass
    assert row(value)['count'] == 0 and row(value)['dropped'] == 1


def test_decorator_ignores_ordinary_diagnostic_attribute_failure():
    class Worker:
        @property
        def host_timing(self):
            raise RuntimeError('diagnostic unavailable')
        @timed(Phase.QUEUE_CALL)
        def work(self):
            return 17
    assert Worker().work() == 17
