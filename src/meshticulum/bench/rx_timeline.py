"""Bounded opt-in receive service observations, never authority or RF evidence."""
from collections import deque
import time

CAPACITY = 2048
DETAIL_LIMIT = 4


class ReceiveTimeline:
    def __init__(self):
        self._rows = deque(maxlen=CAPACITY)
        self._active = self._last_packet = None
        self._last_us = None
        self._calls = self._evicted = self._detail_dropped = self._clock_errors = 0

    def _now(self):
        try:
            ns = time.monotonic_ns()
            if type(ns) is not int or not 0 <= ns <= 2**63-1:
                raise ValueError('invalid_diagnostic_clock')
            value = ns // 1000
            if self._last_us is not None and value < self._last_us:
                raise ValueError('diagnostic_clock_regressed')
            self._last_us = value
            return value
        except Exception:
            self._clock_errors += 1
            return None

    def begin(self):
        self._calls += 1
        self._last_packet = None
        self._active = dict(call=self._calls, started_us=self._now(), finished_us=None,
                            outcome='failure', irq=[], rearm=[], packet_sha256=None)

    def irq(self, flags):
        row = self._active
        if row is None or not flags:
            return
        if len(row['irq']) == DETAIL_LIMIT:
            self._detail_dropped += 1
        else:
            row['irq'].append(dict(at_us=self._now(), flags=flags))

    def rearm_start(self):
        row = self._active
        if row is None:
            return None
        if len(row['rearm']) == DETAIL_LIMIT:
            self._detail_dropped += 1
            return None
        item = dict(started_us=self._now(), finished_us=None, complete=False)
        row['rearm'].append(item)
        return item

    def rearm_finish(self, item, success):
        if item is not None:
            item.update(finished_us=self._now(), complete=success)

    def finish(self, outcome):
        row = self._active
        self._active = None
        if row is None:
            return
        row.update(finished_us=self._now(), outcome=outcome)
        if len(self._rows) == CAPACITY:
            self._evicted += 1
        self._rows.append(row)
        if outcome == 'packet':
            self._last_packet = row

    def anchor(self, digest):
        # Called only with the hash already computed for the accepted flow event.
        if self._last_packet is not None:
            self._last_packet['packet_sha256'] = digest
            self._last_packet = None

    def summary(self):
        value = self.snapshot()
        value.pop('rows')
        value['rows_in_flow_snapshot'] = True
        return value

    def snapshot(self):
        # Detach nested mutable records from the bounded writer state.
        rows = [dict(r, irq=[dict(x) for x in r['irq']], rearm=[dict(x) for x in r['rearm']]) for r in self._rows]
        return dict(schema='meshticulum.hat-receive-timeline.v1', capacity=CAPACITY,
                    calls=self._calls, retained=len(rows), evicted=self._evicted,
                    detail_dropped=self._detail_dropped, clock_errors=self._clock_errors,
                    active_call=self._active is not None,
                    complete=not (self._evicted or self._detail_dropped or self._clock_errors or self._active),
                    scope='HATWorker.receive only; excludes initialization and transmit restoration',
                    clock_basis='diagnostic-host-monotonic; never authority',
                    packet_anchor='existing authenticated flow-event hash; absent for unaccepted physical packets',
                    timing_overhead=True, extra_spi_calls=0, rows=rows)
