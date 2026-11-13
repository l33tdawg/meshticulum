# SPDX-License-Identifier: Apache-2.0
"""Optional fixed authority diagnostics; never an authority or deadline clock."""
from contextlib import nullcontext
from enum import Enum
from functools import wraps
import time

_WALL = time.perf_counter_ns
_CPU = getattr(time, 'thread_time_ns', None)
MAX_VALUE = 2**63 - 1
_DISABLED = nullcontext()

class AuthorityPhase(Enum):
    HAT_LIVE = 'hat_live'
    APPLICATION_LIVE = 'application_live'
    AUTHORITY_LIVE = 'authority_live'
    OWNER_CURRENT = 'owner_current'
    FILE_WINDOW_CHECK = 'file_window_check'

_INDEX = {label: i for i, label in enumerate(AuthorityPhase)}
_EMPTY = (0, 0, None, None, 0, 0, False)

def check_authority_timing(value):
    if value is not None and type(value) is not AuthorityPhaseTimings:
        raise ValueError('expected AuthorityPhaseTimings or None')
    return value

class AuthorityPhaseTimings:
    """Five inclusive wall/CPU aggregates, fixed storage and no event identities."""
    __slots__ = ('_wall', '_cpu', '_active', '_binding_errors')
    def __init__(self):
        self._wall = [_EMPTY for _ in AuthorityPhase]
        self._cpu = [_EMPTY for _ in AuthorityPhase] if callable(_CPU) else None
        self._active = 0
        self._binding_errors = 0

    def _drop(self, index, channel, invalid=False):
        try:
            rows = self._wall if channel == 'wall' else self._cpu
            if rows is None:
                return
            n, total, low, high, bad, dropped, saturated = rows[index]
            bad += int(invalid); dropped += 1
            rows[index] = (n, total, low, high, min(bad, MAX_VALUE),
                           min(dropped, MAX_VALUE), saturated or bad > MAX_VALUE or dropped > MAX_VALUE)
        except Exception:
            pass

    def _sample(self, index, channel):
        try:
            value = (_WALL if channel == 'wall' else _CPU)()
            if type(value) is not int or not 0 <= value <= MAX_VALUE:
                self._drop(index, channel, True)
                return None
            return value
        except Exception:
            self._drop(index, channel, True)
            return None

    def _begin(self, label):
        index = _INDEX[label]; bit = 1 << index
        if self._active & bit:
            self._drop(index, 'wall'); self._drop(index, 'cpu')
            return None
        self._active |= bit
        try:
            wall = self._sample(index, 'wall')
            cpu = self._sample(index, 'cpu') if self._cpu is not None else None
            return wall, cpu
        except BaseException:
            self._active &= ~bit
            raise

    def _record(self, index, channel, start, end):
        if start is None or end is None:
            return
        if end < start:
            self._drop(index, channel, True)
            return
        rows = self._wall if channel == 'wall' else self._cpu
        n, total, low, high, bad, dropped, saturated = rows[index]
        elapsed = end - start; n += 1; total += elapsed
        rows[index] = (min(n, MAX_VALUE), min(total, MAX_VALUE),
                       elapsed if low is None else min(low, elapsed),
                       elapsed if high is None else max(high, elapsed), bad, dropped,
                       saturated or n > MAX_VALUE or total > MAX_VALUE)

    def _finish(self, label, start):
        if start is None:
            return
        index = _INDEX[label]
        try:
            wall, cpu = start
            cpu_end = self._sample(index, 'cpu') if cpu is not None else None
            wall_end = self._sample(index, 'wall') if wall is not None else None
            self._record(index, 'wall', wall, wall_end)
            if self._cpu is not None:
                self._record(index, 'cpu', cpu, cpu_end)
        finally:
            self._active &= ~(1 << index)

    def snapshot(self):
        def row(value):
            return dict(zip(('count','total_ns','min_ns','max_ns','invalid','dropped','saturated'), value))
        return dict(schema='meshticulum.authority-phase-timing.v1',
                    wall_clock='time.perf_counter_ns', cpu_clock='time.thread_time_ns' if self._cpu is not None else None,
                    inclusive=True, overlapping=True, active_phase_mask=self._active,
                    binding_errors=self._binding_errors,
                    scope='after worker setup through cleanup; initial hardware/authority/worker setup excluded',
                    phases={label.value:dict(wall=row(self._wall[i]),
                        thread_cpu=row(self._cpu[i]) if self._cpu is not None else None)
                        for label,i in _INDEX.items()})

class _Measurement:
    __slots__ = ('timing','label','start')
    def __init__(self, timing, label):
        self.timing,self.label,self.start = timing,label,None
    def __enter__(self):
        try:
            self.start = self.timing._begin(self.label)
        except Exception:
            self.timing._drop(_INDEX[self.label], 'wall')
            self.timing._drop(_INDEX[self.label], 'cpu')
        return None
    def __exit__(self, kind, error, traceback):
        try:
            self.timing._finish(self.label, self.start)
        except Exception:
            self.timing._drop(_INDEX[self.label], 'wall')
            self.timing._drop(_INDEX[self.label], 'cpu')
        except BaseException:
            try:
                self.timing._drop(_INDEX[self.label], 'wall')
                self.timing._drop(_INDEX[self.label], 'cpu')
            except BaseException:
                pass
            if kind is None:
                raise
        finally:
            if self.start is not None:
                try:
                    self.timing._active &= ~(1 << _INDEX[self.label])
                except Exception:
                    pass
        return False

def authority_phase(timing, label):
    if type(label) is not AuthorityPhase:
        raise ValueError('expected a fixed AuthorityPhase')
    if timing is None:
        return _DISABLED
    check_authority_timing(timing)
    try:
        return _Measurement(timing, label)
    except Exception:
        timing._drop(_INDEX[label], 'wall')
        timing._drop(_INDEX[label], 'cpu')
        return _DISABLED

def authority_timed(label):
    if type(label) is not AuthorityPhase:
        raise ValueError('expected a fixed AuthorityPhase')
    def decorate(function):
        @wraps(function)
        def wrapped(self, *args, **kwargs):
            try:
                timing = getattr(self, 'authority_timing', None)
                measurement = _Measurement(timing, label) if type(timing) is AuthorityPhaseTimings else None
            except Exception:
                measurement = None
            if measurement is None:
                return function(self, *args, **kwargs)
            with measurement:
                return function(self, *args, **kwargs)
        return wrapped
    return decorate


class AuthorityTimingBindings:
    """Run-scoped diagnostic attributes; best-effort restoration, never authority."""
    def __init__(self, timing):
        self.timing = check_authority_timing(timing)
        self.saved = []
        self.missing = object()
        self.failed = False
    def _failed(self):
        self.failed = True
        try:
            self.timing._binding_errors = min(MAX_VALUE, self.timing._binding_errors + 1)
        except Exception:
            pass
    def bind(self, obj):
        if self.timing is None:
            return
        try:
            state = vars(obj)
            previous = state.get('authority_timing', self.missing)
            self.saved.append((state, previous))
            state['authority_timing'] = self.timing
        except Exception:
            self._failed()
    def restore(self, *, suppress_cancellation=False):
        cancellation = None
        for state, previous in reversed(self.saved):
            try:
                if previous is self.missing:
                    state.pop('authority_timing', None)
                else:
                    state['authority_timing'] = previous
            except Exception:
                self._failed()
            except BaseException as error:
                self._failed()
                if cancellation is None:
                    cancellation = error
        self.saved.clear()
        if cancellation is not None and not suppress_cancellation:
            raise cancellation
