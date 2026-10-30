"""MNR1 uses real firmware branches; all driver/serial I/O is simulated."""
from dataclasses import replace
from pathlib import Path
import struct
import subprocess

import pytest

from meshticulum.bench.native_profile import (
    NativeRxReason, NativeRxReasons, rx_reasons_query)
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, SerialError

ROOT = Path(__file__).resolve().parents[1]
BOOT = bytes.fromhex('0000545662f61b440000000000000012')
CASES = (
    'read_crc', 'read_header', 'read_both', 'read_stale_both',
    'read_unattributed', 'read_timeout', 'read_other', 'length_zero',
    'length_large', 'reserve_low', 'invalid_length_and_low', 'offer_failed',
    'export', 'gfsk_export', 'stale_header', 'gfsk_stale_header',
    'sync_preempt', 'gfsk_preempt', 'sync_done', 'irq_crc', 'irq_header',
    'irq_both', 'irq_timeout', 'irq_stale', 'irq_timeout_stale',
    'header_stall', 'backpressure')


@pytest.fixture(scope='module')
def binaries(tmp_path_factory):
    directory = ROOT / 'tests/meshticulum_bench_native'
    output = tmp_path_factory.mktemp('rx-reasons')
    result = []
    for enabled in (0, 1):
        executable = output / f'controls-{enabled}'
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', '-g',
                        f'-DMTBENCH_NATIVE_PROFILE={enabled}', '-I', str(directory),
                        str(directory / 'rx_reasons.cpp'), '-o', str(executable)],
                       check=True, capture_output=True, text=True)
        result.append(executable)
    return result


def run(executable, case):
    return subprocess.run([str(executable), case], check=True,
                          capture_output=True, timeout=10).stdout


@pytest.mark.parametrize('case', CASES)
def test_reason_sites_preserve_driver_debt_and_serial_bytes(binaries, case):
    assert run(binaries[0], case) == run(binaries[1], case)


def test_disabled_arguments_are_unevaluated_and_query_unavailable(binaries):
    run(binaries[0], 'disabled')


def decode(frame):
    return NativeRxReasons.from_frame(frame, boot=BOOT, request=100)


def frame(counts=None, mask=0):
    return Frame(Message.NATIVE_PROFILE_REPLY, BOOT, 100,
                 b'MNR1\x01\x11\x04\x01' + struct.pack('!18I', mask, *(counts or [0] * 17)))


def test_native_query_is_readonly_boot_bound_idle_drained_and_repeatable(binaries):
    run(binaries[1], 'query')
    reply, = Decoder().feed(run(binaries[1], 'wire'))
    result = decode(reply)
    assert result.counts == tuple(range(17))
    assert result.exact and result.saturated_mask == 0
    assert result.dropped_total == sum(range(14))
    assert result.counts[NativeRxReason.RX_EXPORTED] == 14
    assert rx_reasons_query(boot=BOOT, request=100).body == b'\xfd'


def test_each_counter_saturates_independently_and_last_exact_max_is_valid(binaries):
    reply, = Decoder().feed(run(binaries[1], 'saturation'))
    result = decode(reply)
    assert result.counts == (2**32 - 1,) * 17
    assert result.saturated_mask == (1 << 17) - 1 and not result.exact
    assert result.dropped_total == 14 * (2**32 - 1)
    assert decode(frame([2**32 - 1] * 17)).exact
    for index in range(17):
        values = [0] * 17
        values[index] = 2**32 - 1
        assert not decode(frame(values, 1 << index)).exact
        with pytest.raises(SerialError):
            decode(frame(mask=1 << index))


@pytest.mark.parametrize('body', [
    b'', bytes(79), bytes(81), b'MNR2\x01\x11\x04\x01' + bytes(72),
    *[b'MNR1' + bytes(header) + bytes(72) for header in (
        (0, 17, 4, 1), (1, 16, 4, 1), (1, 18, 4, 1),
        (1, 17, 8, 1), (1, 17, 4, 0), (1, 17, 4, 3))],
])
def test_reject_unknown_or_malformed_schema(body):
    with pytest.raises(SerialError):
        decode(replace(frame(), body=body))


def test_reject_wrong_identity_types_and_unknown_saturation_bits():
    valid = frame()
    for bad in (replace(valid, boot=bytes(16)), replace(valid, request=99),
                replace(valid, type=Message.STATUS_REPLY), None, b'not a frame',
                frame(mask=1 << 17), frame(mask=1 << 31)):
        with pytest.raises(SerialError):
            decode(bad)
    for args in (dict(boot=bytearray(BOOT), request=100),
                 dict(boot=BOOT, request=True), dict(boot=BOOT, request=0)):
        with pytest.raises(SerialError):
            NativeRxReasons.from_frame(valid, **args)
    for request in (True, False, 0, -1, 1.5, '1'):
        with pytest.raises(SerialError):
            rx_reasons_query(boot=BOOT, request=request)
