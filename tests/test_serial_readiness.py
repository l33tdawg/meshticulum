"""Readiness tests use local pipes and modeled records; no radio is opened."""
from collections import deque
import os
import struct
from types import SimpleNamespace

import pytest

from meshticulum.bench.burst_protocol import POLICY
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_port import ExclusiveSerial
from meshticulum.bench.serial_protocol import FirmwareIdentity, Frame, Message
from meshticulum.bench.serial_worker import SerialWorker
from meshticulum.bench.tx_burst import Stage


@pytest.fixture
def channel():
    read, write = os.pipe()
    os.set_blocking(read, False)
    value = ExclusiveSerial.__new__(ExclusiveSerial)
    value.fd, value.lock_fd, value.host_timing = read, None, None
    try:
        yield value, write
    finally:
        value.close()
        os.close(write)


def test_readiness_never_consumes_bytes_and_read_retains_disconnect_semantics(channel):
    serial, write = channel
    assert serial.wait_readable(0) is False
    os.write(write, b'partial-record')
    assert serial.wait_readable(.005) is True
    assert serial.wait_readable(0) is True
    assert serial.read(0) == b'partial-record'
    assert serial.wait_readable(0) is False


def test_arrival_between_buffer_check_and_select_is_not_lost(channel, monkeypatch):
    serial, write = channel
    import meshticulum.bench.serial_port as port
    original = port.select.select
    def arrival(*args):
        os.write(write, b'new')
        return original(*args)
    monkeypatch.setattr(port.select, 'select', arrival)
    assert serial.wait_readable(.005)
    assert os.read(serial.fd, 20) == b'new'


@pytest.mark.parametrize('timeout', [True, -.001, .005001, float('nan'), float('inf'), '0', 10**1000])
def test_activity_wait_rejects_unbounded_or_nonfinite_timeout(channel, timeout):
    serial, _ = channel
    for wait in (serial.wait_readable, SerialWorker(serial).wait_for_activity):
        with pytest.raises(HardwareError, match='invalid_serial_activity_timeout'):
            wait(timeout)


@pytest.mark.parametrize('burst', [False, True])
def test_decoded_receive_wakes_without_fd_bytes_but_unmatched_replies_do_not(channel, burst):
    serial, _ = channel
    worker = BurstSerialWorker(serial, policy=POLICY) if burst else SerialWorker(serial)
    boot = b'b' * 16
    worker.identity = FirmwareIdentity(boot, 1, 'model')
    # An unrelated reply cannot drive a hot loop: only its matching waiter may consume it.
    worker._ingest(Frame(Message.HELLO_REPLY, boot, 7, b'unmatched').encode())
    assert worker.has_buffered_activity is False
    assert worker.wait_for_activity(0) is False
    frame = Frame(Message.RECEIVE, boot, 0, struct.pack('!hh', -730, 110) + b'ciphertext')
    wire = frame.encode()
    worker._ingest(wire[:4])
    assert not worker.has_buffered_activity  # an incomplete decoder record needs more input
    worker._ingest(wire[4:])
    assert worker.wait_for_activity(.005) is True
    assert worker._received.popleft()[1].packet == b'ciphertext'
    assert worker.has_buffered_activity is False


def test_terminal_is_actionable_but_never_itself_grants_queue_capacity(channel):
    serial, _ = channel
    worker = BurstSerialWorker(serial, policy=POLICY)
    worker._open = object()
    worker._burst_count = 4
    worker._physical[1] = dict(event=SimpleNamespace(stage=Stage.RF_DONE))
    worker._routed[8] = deque([object()])
    assert worker.wait_for_activity(0) is True
    assert worker.has_capacity is False
    worker._physical.clear()  # model the normal release path, not a readiness mutation
    assert worker.has_buffered_activity is False
    assert worker.has_capacity is False  # readiness did not reset the four-frame train


def test_wait_exception_including_cancellation_propagates_without_consuming(channel, monkeypatch):
    serial, write = channel
    os.write(write, b'kept')
    import meshticulum.bench.serial_port as port
    error = KeyboardInterrupt('cancelled')
    def cancel(*args):
        raise error
    monkeypatch.setattr(port.select, 'select', cancel)
    with pytest.raises(KeyboardInterrupt) as caught:
        serial.wait_readable(.005)
    assert caught.value is error
    assert os.read(serial.fd, 10) == b'kept'


def test_readiness_is_optional_for_existing_channels():
    channel = SimpleNamespace(read=lambda _: pytest.fail('must not consume'),
        write=lambda *a: pytest.fail('must not write'), monotonic=lambda: 1.)
    worker = SerialWorker(channel)
    assert worker.wait_for_activity(.005) is None
    worker._received.append(object())
    assert worker.wait_for_activity(.005) is True
