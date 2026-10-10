# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Regression for observed ESP32-S3 USB RX loss; model is not RF evidence."""
import pytest

from meshticulum.bench import serial_port
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder, Frame, Message


class RXQueue:
    """USB accepts packets but an overflowing MCU byte queue drops their tail."""
    def __init__(self, partial):
        self.now, self.partial = 0.0, partial
        self.queued, self.delivered, self.writes = bytearray(), bytearray(), []
        self.dropped = 0

    def write(self, fd, data):
        data = bytes(data[:self.partial])
        room = 256 - len(self.queued)
        self.queued.extend(data[:room])
        self.dropped += max(0, len(data) - room)
        self.writes.append((self.now, data))
        return len(data)

    def sleep(self, delay):
        self.now += delay
        self.delivered.extend(self.queued)
        self.queued.clear()


def channel(monkeypatch, partial=64):
    hardware = RXQueue(partial)
    port = object.__new__(serial_port.ExclusiveSerial)
    port.fd, port.lock_fd = 999, None
    port.monotonic = lambda: hardware.now
    monkeypatch.setattr(serial_port.os, 'write', hardware.write)
    monkeypatch.setattr(serial_port.select, 'select', lambda r, w, x, t: ([], w, []))
    monkeypatch.setattr(serial_port.time, 'sleep', hardware.sleep)
    return port, hardware


@pytest.mark.parametrize('partial', [64, 23])
def test_full_size_escaped_radio_packet_survives_bounded_mcu_queue(monkeypatch, partial):
    port, hardware = channel(monkeypatch, partial)
    frame = Frame(Message.TRANSMIT, b'b' * 16, 1, b'\xc0\xdb' * 127 + b'\xc0')
    encoded = frame.encode()
    assert len(encoded) > 512
    port.write(encoded, 1.0)
    hardware.sleep(.010)
    assert hardware.dropped == 0 and bytes(hardware.delivered) == encoded
    assert Decoder().feed(bytes(hardware.delivered)) == [frame]
    assert max(len(data) for _, data in hardware.writes) <= 64
    # The original unpaced write loses bytes in this same observed queue model.
    burst = RXQueue(len(encoded)); burst.write(999, encoded); burst.sleep(.010)
    assert burst.dropped > 0 and Decoder().feed(bytes(burst.delivered)) == []


def test_deadline_exhaustion_stops_mid_frame_without_sending_more(monkeypatch):
    port, hardware = channel(monkeypatch)
    with pytest.raises(HardwareError, match='serial_write_deadline'):
        port.write(b'x' * 300, .001)
    assert len(hardware.writes) == 1 and len(hardware.writes[0][1]) == 64
    assert hardware.now == 0
