"""Real serial codec/worker with a modeled channel; no device or RF calls."""
import struct
from types import SimpleNamespace

import pytest

from meshticulum.bench.gfsk_profile import GfskProfile, GfskProfileError
from meshticulum.bench.gfsk250_profile import CAPABILITY, Gfsk250Profile
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, SerialError
from meshticulum.bench.serial_worker import Gfsk250SerialWorker
from meshticulum.bench.wire import WireError
from tests.test_radio_file_example import example, source_metadata

BOOT = bytes.fromhex('0000545662f61b440000000000000012')
PROFILE = Gfsk250Profile(921375000, 0, 180000, 256, 60000000)


def status_body(*, flags=b'\0\0\0', attempts=34, airtime=1114496, dropped=2):
    return PROFILE.encode(board_id=1) + struct.pack('!IQI', attempts, airtime, dropped) + flags


@pytest.fixture
def inspect_case(tmp_path, monkeypatch):
    path, pin, source = source_metadata(tmp_path)
    controls = dict(mode='configured', body=status_body(), kind=Message.GFSK250_STATUS_REPLY,
                    boot=BOOT, request_delta=0, refusal=b'\x01', direct=False, expire_after_refusal=False)
    events, closed, sends = [], [], []

    class Channel:
        def __init__(self, *args, **kwargs):
            self.pending, self.now = b'', 10.

        def monotonic(self):
            return self.now

        def write(self, data, deadline):
            frame, = Decoder().feed(data)
            events.append(frame.type)
            reply_boot, request = BOOT, frame.request
            if frame.type is Message.HELLO:
                kind = Message.HELLO_REPLY
                body = struct.pack('!BBH', 1, 1, 255) + source['identity'].encode() + b'\0'
            elif frame.type is Message.GFSK250_CAPABILITIES:
                kind, body = Message.GFSK250_CAPABILITIES_REPLY, CAPABILITY
            elif frame.type is Message.STATUS:
                if controls['mode'] == 'unconfigured':
                    kind, body = Message.STATUS_REPLY, controls['body']
                elif controls['direct']:
                    kind, body = controls['kind'], controls['body']
                else:
                    kind, body = Message.REFUSED, controls['refusal']
                    if controls['expire_after_refusal']:
                        self.now += 3
            else:
                assert frame.type is Message.GFSK250_STATUS
                kind, body = controls['kind'], controls['body']
            if frame.type in (Message.STATUS, Message.GFSK250_STATUS):
                reply_boot = controls['boot']
                request += controls['request_delta']
            self.pending = Frame(kind, reply_boot, request, body).encode()

        def read(self, timeout):
            self.now += min(timeout, .05)
            result, self.pending = self.pending, b''
            return result

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

    original_send = Gfsk250SerialWorker._send
    def observed_send(worker, frame, deadline):
        sends.append((frame.type, frame.request, deadline))
        return original_send(worker, frame, deadline)
    monkeypatch.setattr(Gfsk250SerialWorker, '_send', observed_send)
    monkeypatch.setattr(example, 'ExclusiveSerial', Channel)

    def run():
        try:
            return example.inspect_usb(SimpleNamespace(firmware_source=path,
                firmware_source_sha256=pin, device='/explicit-source-model', usb_serial='source-model'))
        finally:
            assert closed == [True]
            assert set(events) <= {Message.HELLO, Message.GFSK250_CAPABILITIES,
                                   Message.STATUS, Message.GFSK250_STATUS}
            assert sorted(p.name for p in tmp_path.iterdir()) == ['SOURCE.json']
    return controls, events, sends, run


def test_gfsk250_status_preserves_nonzero_boot_cost_and_old_receive_drops(inspect_case):
    control, events, sends, run = inspect_case
    value = run()
    assert value['boot_attempts'] == 34 and value['boot_reserved_airtime_us'] == 1114496
    assert value['dropped_receive'] == 2
    assert value['armed'] is value['transmitting'] is value['fenced'] is False
    assert events == [Message.HELLO, Message.GFSK250_CAPABILITIES, Message.STATUS, Message.GFSK250_STATUS]
    assert sends[-2][2] == sends[-1][2] and sends[-2][1] != sends[-1][1]


def test_existing_49_byte_status_remains_readonly_and_keeps_counters(inspect_case):
    control, events, sends, run = inspect_case
    control.update(mode='unconfigured', body=bytes(30) + struct.pack('!IQI', 200, 40000000, 7) + bytes(3))
    value = run()
    assert (value['boot_attempts'], value['boot_reserved_airtime_us'], value['dropped_receive']) == (200, 40000000, 7)
    assert events == [Message.HELLO, Message.GFSK250_CAPABILITIES, Message.STATUS]


def test_typed_66_byte_reply_is_decoded_without_an_extra_query(inspect_case):
    control, events, sends, run = inspect_case
    control['direct'] = True
    assert run()['boot_attempts'] == 34
    assert events[-1] is Message.STATUS


@pytest.mark.parametrize('flags', [b'\1\0\0', b'\0\1\0', b'\0\0\1', b'\2\0\0', b'\0\2\0', b'\0\0\2'])
def test_gfsk250_unsafe_or_nonboolean_state_refuses_and_closes(inspect_case, flags):
    control, events, sends, run = inspect_case
    control['body'] = status_body(flags=flags)
    with pytest.raises((WireError, SerialError)):
        run()


@pytest.mark.parametrize('body', [b'', status_body()[:-1], status_body() + b'\0',
    GfskProfile(921375000, 0, 180000, 256, 60000000).encode(board_id=1) + struct.pack('!IQI', 34, 1114496, 2) + bytes(3),
    status_body(attempts=257), status_body(airtime=60000001)])
def test_malformed_or_wrong_modem_status_refuses(inspect_case, body):
    control, events, sends, run = inspect_case
    control['body'] = body
    with pytest.raises((WireError, SerialError, GfskProfileError)):
        run()


@pytest.mark.parametrize('kind', [Message.STATUS_REPLY, Message.GFSK_STATUS_REPLY, Message.REFUSED])
def test_fallback_requires_the_exact_gfsk250_reply_type(inspect_case, kind):
    control, events, sends, run = inspect_case
    control['kind'] = kind
    with pytest.raises((WireError, SerialError)):
        run()


@pytest.mark.parametrize('fault', ['boot', 'request'])
def test_status_still_binds_exact_boot_and_request(inspect_case, fault):
    control, events, sends, run = inspect_case
    if fault == 'boot':
        control['boot'] = b'x' * 16
    else:
        control['request_delta'] = 1
    with pytest.raises((WireError, SerialError, HardwareError)):
        run()
    assert Message.GFSK250_STATUS not in events


@pytest.mark.parametrize('refusal', [b'', b'\x02', b'\x04', b'\x09', b'\x01\x00'])
def test_only_exact_bad_command_can_select_the_readonly_fallback(inspect_case, refusal):
    control, events, sends, run = inspect_case
    control['refusal'] = refusal
    with pytest.raises((WireError, SerialError)):
        run()
    assert Message.GFSK250_STATUS not in events


def test_fallback_never_renews_an_expired_inspection_deadline(inspect_case):
    control, events, sends, run = inspect_case
    control['expire_after_refusal'] = True
    with pytest.raises((WireError, SerialError, HardwareError)):
        run()
    assert Message.GFSK250_STATUS not in events


@pytest.mark.parametrize('flags', [b'\1\0\0', b'\0\1\0', b'\0\0\1'])
def test_49_byte_status_still_refuses_unsafe_state(inspect_case, flags):
    control, events, sends, run = inspect_case
    control.update(mode='unconfigured', body=bytes(30) + struct.pack('!IQI', 4, 12, 2) + flags)
    with pytest.raises(WireError):
        run()
