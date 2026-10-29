# SPDX-License-Identifier: Apache-2.0
"""Optional, bounded host duration aggregates for one event-loop owner.

These inclusive perf-counter intervals are diagnostics, never authority clocks,
CPU time, MCU timing or RF completion. Nested intervals overlap. No identifiers,
timestamps, payloads or exception details are retained. The disabled path reads
no clock. Enabled profiling itself adds overhead to the observed operation.
"""
from contextlib import nullcontext
from enum import Enum
from functools import wraps
from time import perf_counter_ns


class Phase(Enum):
    QUEUE_CALL = 'queue_call'
    QUEUE_PUMP = 'queue_pump'
    QUEUE_CLOCK = 'queue_clock'
    QUEUE_RESERVE = 'queue_reserve'
    QUEUE_SERIAL_WRITE = 'queue_serial_write'
    QUEUE_ADMISSION_WAIT = 'queue_admission_wait'
    QUEUE_TERMINAL_SETTLE = 'queue_terminal_settle'
    RELEASE_REQUEST = 'release_request'
    RELEASE_ROUNDTRIP = 'release_roundtrip'
    RELEASE_COMMIT = 'release_commit'
    JOURNAL_TRANSACTION = 'journal_transaction'
    SERIAL_WRITE = 'serial_write'
    SERIAL_WAIT_WRITABLE = 'serial_wait_writable'
    SERIAL_OS_WRITE = 'serial_os_write'
    SERIAL_PACING_SLEEP = 'serial_pacing_sleep'
    SERIAL_READ = 'serial_read'
    SERIAL_READ_WAIT = 'serial_read_wait'
    SERIAL_OS_READ = 'serial_os_read'
    FLOW_STEP = 'flow_step'
    FLOW_ACCOUNTING = 'flow_accounting'
    FLOW_RECEIVE = 'flow_receive'
    FLOW_DISPATCH = 'flow_dispatch'
    FLOW_PUMP = 'flow_pump'
    HOST_LOOP_SLEEP = 'host_loop_sleep'


MAX_VALUE = 2**63 - 1
_INDEX = {value: index for index, value in enumerate(Phase)}
_EMPTY = (0, 0, None, None, 0, 0, False)
_DISABLED = nullcontext()


def _valid_tick(value):
    return type(value) is int and 0 <= value <= MAX_VALUE


class HostPhaseTimings:
    """Fixed-size, best-effort duration collector; no pluggable callbacks.

``count`` counts valid intervals even when their operation raised. ``invalid``
counts invalid/regressing clock samples; ``dropped`` counts omitted intervals.
All integers saturate, explicitly marked by ``saturated``. Ordinary diagnostic
errors never become transport errors. Process cancellation remains effective.
    """
    __slots__ = ('_rows',)

    def __init__(self):
        self._rows = [_EMPTY for _ in Phase]

    def _drop(self, label, *, invalid=False):
        # Even reporting a diagnostic error is best effort. In particular an
        # allocation failure must not replace an operation's original outcome.
        try:
            index = _INDEX[label]
            count, total, low, high, bad, dropped, saturated = self._rows[index]
            bad += int(invalid)
            dropped += 1
            self._rows[index] = (count, total, low, high,
                min(bad, MAX_VALUE), min(dropped, MAX_VALUE),
                saturated or bad > MAX_VALUE or dropped > MAX_VALUE)
        except Exception:
            pass

    def _begin(self, label):
        try:
            start = perf_counter_ns()
            if not _valid_tick(start):
                self._drop(label, invalid=True)
                return None
            return start
        except Exception:
            self._drop(label)
            return None

    def _finish(self, label, start):
        if start is None:
            return
        try:
            end = perf_counter_ns()
            if not _valid_tick(end) or end < start:
                self._drop(label, invalid=True)
                return
            duration = end - start
            index = _INDEX[label]
            count, total, low, high, bad, dropped, saturated = self._rows[index]
            count += 1
            total += duration
            self._rows[index] = (min(count, MAX_VALUE), min(total, MAX_VALUE),
                duration if low is None else min(low, duration),
                duration if high is None else max(high, duration), bad, dropped,
                saturated or count > MAX_VALUE or total > MAX_VALUE)
        except Exception:
            self._drop(label)

    def snapshot(self):
        # Fresh dictionaries prevent callers from modifying live aggregates.
        return dict(schema='meshticulum.host-phase-timing.v1',
            clock='time.perf_counter_ns', inclusive=True,
            phases={label.value: dict(zip(
                ('count', 'total_ns', 'min_ns', 'max_ns', 'invalid', 'dropped', 'saturated'),
                self._rows[index])) for label, index in _INDEX.items()})


def check_timing(value):
    """Validate at construction, before opening any transport or journal."""
    if value is not None and type(value) is not HostPhaseTimings:
        raise ValueError('expected HostPhaseTimings or None')
    return value


class _Measurement:
    __slots__ = ('timing', 'label', 'start')

    def __init__(self, timing, label):
        self.timing, self.label, self.start = timing, label, None

    def __enter__(self):
        # BaseException deliberately propagates here: cancellation before the
        # operation starts must not be silently ignored by instrumentation.
        try:
            self.start = self.timing._begin(self.label)
        except Exception:
            self.timing._drop(self.label)
        return None

    def __exit__(self, exception_type, exception, traceback):
        try:
            self.timing._finish(self.label, self.start)
        except Exception:
            self.timing._drop(self.label)
        except BaseException:
            # A diagnostic exit cannot replace an existing operation failure,
            # including its KeyboardInterrupt/SystemExit or exception group.
            try:
                self.timing._drop(self.label)
            except BaseException:
                pass
            if exception_type is None:
                raise
        return False


def phase(timing, label):
    """Measure a fixed phase; an absent collector is a reusable null context."""
    if type(label) is not Phase:
        raise ValueError('expected a fixed Phase')
    if timing is None:
        return _DISABLED
    check_timing(timing)
    try:
        return _Measurement(timing, label)
    except Exception:
        timing._drop(label)
        return _DISABLED


def timed(label):
    """Time a method only when its owner supplied ``host_timing``."""
    if type(label) is not Phase:
        raise ValueError('expected a fixed Phase')
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            try:
                timing = getattr(self, 'host_timing', None)
            except Exception:
                timing = None
            if timing is None:
                return function(self, *args, **kwargs)
            with phase(timing, label):
                return function(self, *args, **kwargs)
        return wrapped
    return decorate
