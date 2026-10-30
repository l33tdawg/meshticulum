"""Bounded host driving tests; virtual time is not measured radio throughput."""
from types import SimpleNamespace

import pytest

from meshticulum.bench.host_pump import run_host_pump
from meshticulum.bench.linux_hat import HardwareError


def drive(*, steps=10, accepted=True, capacity=True, cancel_at=None, deadline=100_000):
    state = SimpleNamespace(now=1, calls=0, queued=0, checks=0, waits=[], at=[])
    def step():
        state.calls += 1
        state.at.append(state.now)
        if accepted:
            state.queued += 1
        return state.calls == steps
    def check():
        state.checks += 1
        if state.checks == cancel_at:
            raise KeyboardInterrupt('cancelled')
    def wait(seconds):
        state.waits.append(seconds)
        state.now += round(seconds * 1_000_000)
    state.run = lambda: run_host_pump(step, check=check, wait=wait,
        clock_us=lambda: state.now, deadline_us=deadline,
        progress=lambda: state.queued, can_repump=lambda: capacity)
    return state


def test_useful_refill_has_at_most_four_consecutive_steps():
    value = drive()
    value.run()
    assert value.at == [1]*4 + [5001]*4 + [10001]*2
    assert value.waits == [.005, .005]
    assert value.checks == 9
    # The old loop would deliberately wait after all nine incomplete steps.
    assert sum(value.waits) < 9 * .005


@pytest.mark.parametrize('accepted,capacity', [(False, True), (True, False), (False, False)])
def test_refusal_or_backpressure_does_not_hot_spin(accepted, capacity):
    value = drive(steps=5, accepted=accepted, capacity=capacity)
    value.run()
    assert value.waits == [.005] * 4
    assert value.at == [1, 5001, 10001, 15001, 20001]


def test_original_deadline_shortens_wait_and_stops_before_another_step():
    value = drive(accepted=False, deadline=1201)
    with pytest.raises(HardwareError, match='host_pump_deadline'):
        value.run()
    assert value.calls == 1
    assert value.waits == [.0012]


def test_cancellation_is_not_caught_or_followed_by_wait_or_dispatch():
    value = drive(cancel_at=2)
    with pytest.raises(KeyboardInterrupt, match='cancelled'):
        value.run()
    assert value.calls == 2 and value.checks == 2 and value.waits == []


def test_early_input_wake_rechecks_operation_before_next_dispatch():
    now, live, calls = [1], [True], []
    def step():
        assert live[0], 'revoked'
        calls.append('step')
        return False
    def wait(timeout):
        assert timeout == .005
        now[0] += 100
        live[0] = False
    with pytest.raises(AssertionError, match='revoked'):
        run_host_pump(step, check=lambda: None, wait=wait,
            clock_us=lambda: now[0], deadline_us=100_000)
    assert calls == ['step']


def test_operation_error_is_preserved_without_waiting():
    error = BaseExceptionGroup('original', [KeyboardInterrupt(), OSError('operation')])
    def step():
        raise error
    with pytest.raises(BaseExceptionGroup) as caught:
        run_host_pump(step, check=lambda: pytest.fail('unexpected check'),
            wait=lambda _: pytest.fail('unexpected wait'), clock_us=lambda: 1, deadline_us=2)
    assert caught.value is error


@pytest.mark.parametrize('deadline', [True, 0, -1, 1.0, float('inf'), None])
def test_invalid_deadline_rejected_before_step(deadline):
    with pytest.raises(HardwareError, match='invalid_host_pump_deadline'):
        run_host_pump(lambda: pytest.fail('unexpected step'), check=lambda: None,
            wait=lambda _: None, clock_us=lambda: 1, deadline_us=deadline)


@pytest.mark.parametrize('before,after', [(True, 2), (0, True), (2, 1), (3, 3), (0., 1.)])
def test_invalid_or_nonincreasing_progress_cannot_request_immediate_repump(before, after):
    samples = iter([before, after, 0])
    steps, waits, now = [0], [], [1]
    def step():
        steps[0] += 1
        return steps[0] == 2
    run_host_pump(step, check=lambda: None, wait=waits.append,
        clock_us=lambda: now[0], deadline_us=100_000,
        progress=lambda: next(samples), can_repump=lambda: pytest.fail('not useful progress'))
    assert waits == [.005]
