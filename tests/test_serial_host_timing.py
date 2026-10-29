"""Source fake-I/O timings preserve pacing and original serial failure boundaries."""
import pytest

from meshticulum.bench import host_timing, serial_port
from meshticulum.bench.host_timing import HostPhaseTimings, Phase
from meshticulum.bench.linux_hat import HardwareError


class FakeIO:
    def __init__(self):
        self.perf_ns = 0
        self.profiling_clock_reads = 0
        self.monotonic_s = 0.0
        self.select_ns = 100_000
        self.write_ns, self.read_ns = 50_000, 70_000
        self.partial = 64
        self.write_actions = []
        self.write_calls, self.read_calls = 0, 0
        self.written = bytearray()
        self.write_sizes, self.sleeps = [], []
        self.sleep_extra_ns = 0
        self.readable = self.writable = True
        self.read_result = b'local bytes'

    def advance(self, ns):
        self.perf_ns += ns
        self.monotonic_s += ns / 1_000_000_000

    def select(self, read, write, errors, timeout):
        self.advance(self.select_ns)
        return (read if self.readable else [], write if self.writable else [], [])

    def write(self, fd, data):
        self.write_calls += 1
        self.advance(self.write_ns)
        action = self.write_actions.pop(0) if self.write_actions else self.partial
        if isinstance(action, BaseException):
            raise action
        size = min(action, len(data))
        self.write_sizes.append(size)
        self.written.extend(data[:size])
        return size

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(round(seconds * 1_000_000_000) + self.sleep_extra_ns)

    def read(self, fd, size):
        assert size == 4096
        self.read_calls += 1
        self.advance(self.read_ns)
        if isinstance(self.read_result, BaseException):
            raise self.read_result
        return self.read_result


def channel(monkeypatch, enabled):
    hardware = FakeIO()
    if enabled:
        def profiling_clock():
            hardware.profiling_clock_reads += 1
            return hardware.perf_ns
        monkeypatch.setattr(host_timing, 'perf_counter_ns', profiling_clock)
        collector = HostPhaseTimings()
    else:
        def forbidden_clock():
            hardware.profiling_clock_reads += 1
            raise AssertionError('disabled timing read the profiling clock')
        monkeypatch.setattr(host_timing, 'perf_counter_ns', forbidden_clock)
        collector = None
    port = object.__new__(serial_port.ExclusiveSerial)
    port.fd, port.lock_fd, port.host_timing = 999, None, collector
    port.monotonic = lambda: hardware.monotonic_s
    monkeypatch.setattr(serial_port.select, 'select', hardware.select)
    monkeypatch.setattr(serial_port.os, 'write', hardware.write)
    monkeypatch.setattr(serial_port.os, 'read', hardware.read)
    monkeypatch.setattr(serial_port.time, 'sleep', hardware.sleep)
    return port, hardware, collector


@pytest.mark.parametrize('enabled', [False, True])
def test_full_frame_preserves_six_writes_and_five_pacing_sleeps(monkeypatch, enabled):
    port, io, collector = channel(monkeypatch, enabled)
    data = b'x' * 352
    port.write(data, 1.0)
    assert bytes(io.written) == data and io.write_sizes == [64] * 5 + [32]
    assert io.sleeps == [.002] * 5
    assert port.read(.1) == b'local bytes'
    if collector is not None:
        assert io.profiling_clock_reads > 0
        snapshot = collector.snapshot()
        phases = snapshot['phases']
        assert phases[Phase.SERIAL_WRITE.value]['count'] == 1
        assert phases[Phase.SERIAL_WRITE.value]['total_ns'] == 10_900_000
        assert phases[Phase.SERIAL_WAIT_WRITABLE.value]['total_ns'] == 600_000
        assert phases[Phase.SERIAL_OS_WRITE.value]['total_ns'] == 300_000
        assert phases[Phase.SERIAL_PACING_SLEEP.value]['count'] == 5
        assert phases[Phase.SERIAL_PACING_SLEEP.value]['total_ns'] == 10_000_000
        assert phases[Phase.SERIAL_READ.value]['total_ns'] == 170_000
        assert phases[Phase.SERIAL_READ_WAIT.value]['total_ns'] == 100_000
        assert phases[Phase.SERIAL_OS_READ.value]['total_ns'] == 70_000
    else:
        assert io.profiling_clock_reads == 0


@pytest.mark.parametrize('enabled', [False, True])
def test_partial_and_blocked_writes_preserve_bytes_and_pacing(monkeypatch, enabled):
    port, io, _ = channel(monkeypatch, enabled)
    io.partial = 23
    io.write_actions = [BlockingIOError(), 7]
    port.write(b'x' * 100, 1.0)
    assert bytes(io.written) == b'x' * 100
    assert io.write_sizes == [7, 23, 23, 23, 23, 1]
    assert io.write_calls == 7 and io.sleeps == [.002] * 5


@pytest.mark.parametrize('enabled', [False, True])
def test_delayed_readiness_cannot_write_after_original_deadline(monkeypatch, enabled):
    port, io, _ = channel(monkeypatch, enabled)
    io.select_ns = 20_000_000
    with pytest.raises(HardwareError, match='serial_write_deadline'):
        port.write(b'late', .010)
    assert io.write_calls == 0 and io.written == b''


@pytest.mark.parametrize('enabled', [False, True])
def test_sleep_overshoot_stops_before_next_write(monkeypatch, enabled):
    port, io, _ = channel(monkeypatch, enabled)
    io.sleep_extra_ns = 20_000_000
    with pytest.raises(HardwareError, match='serial_write_deadline'):
        port.write(b'x' * 100, .010)
    assert io.write_calls == 1 and len(io.written) == 64 and io.sleeps == [.002]


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('action,exception,message', [
    (0, HardwareError, 'serial_write_disconnected'),
    (OSError('write failed'), OSError, 'write failed'),
])
def test_write_failure_semantics_survive_instrumentation(monkeypatch, enabled, action, exception, message):
    port, io, _ = channel(monkeypatch, enabled)
    io.write_actions = [23, action]
    with pytest.raises(exception, match=message):
        port.write(b'x' * 100, 1.0)
    assert len(io.written) == 23 and io.sleeps == [.002]


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('result', [BlockingIOError(), b'', OSError('read failed')])
def test_read_failure_semantics_survive_instrumentation(monkeypatch, enabled, result):
    port, io, _ = channel(monkeypatch, enabled)
    io.read_result = result
    if isinstance(result, BlockingIOError):
        assert port.read(.1) == b''
    elif result == b'':
        with pytest.raises(HardwareError, match='serial_read_disconnected'):
            port.read(.1)
    else:
        with pytest.raises(OSError, match='read failed'):
            port.read(.1)
    assert io.read_calls == 1


@pytest.mark.parametrize('enabled', [False, True])
def test_read_timeout_and_write_timeout_do_not_call_os_io(monkeypatch, enabled):
    port, io, _ = channel(monkeypatch, enabled)
    io.readable = io.writable = False
    assert port.read(.1) == b'' and io.read_calls == 0
    with pytest.raises(HardwareError, match='serial_write_deadline'):
        port.write(b'x', 1.0)
    assert io.write_calls == 0


def test_invalid_collector_rejected_before_platform_or_open(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('collector validation reached platform or device opening')
    monkeypatch.setattr(serial_port.platform, 'system', forbidden)
    monkeypatch.setattr(serial_port.os, 'open', forbidden)
    with pytest.raises((TypeError, ValueError)):
        serial_port.ExclusiveSerial('/private/unused', usb_serial='unused', host_timing=object())
