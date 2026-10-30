"""Native diagnostics: real firmware control flow with fake radio/time, no RF."""
from dataclasses import replace
from pathlib import Path
import struct
import subprocess

import pytest

from meshticulum.bench.native_profile import (
    NativePhase, NativeProfileCapability, NativeProfileRecord, NativeMemorySnapshot,
    query, memory_query)
from meshticulum.bench.serial_protocol import Decoder, Frame, Message, SerialError

ROOT = Path(__file__).resolve().parents[1]
BOOT = bytes.fromhex('0000545662f61b440000000000000012')


@pytest.fixture(scope='module', params=[0, 1])
def native_profile(request, tmp_path_factory):
    directory = ROOT / 'tests/meshticulum_bench_native'
    executable = tmp_path_factory.mktemp('native-profile') / 'controls'
    subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g',
                    f'-DMTBENCH_NATIVE_PROFILE={request.param}', '-I', str(directory),
                    str(directory / 'profile.cpp'), '-o', str(executable)],
                   check=True, capture_output=True, text=True)
    return executable, request.param


def run(native_profile, case):
    return subprocess.run([str(native_profile[0]), case], check=True,
                          capture_output=True, timeout=10).stdout


@pytest.mark.parametrize('case', [
    'profile_aggregate', 'burst_capability', 'burst_four_train', 'burst_dwell_rearm',
    'burst_reverse_ack_boundary', 'burst_header_priority', 'burst_header_rearm_age',
    'burst_standby_header', 'burst_standby_rx', 'burst_start_failure',
    'burst_clear_failure', 'burst_irq_failure', 'burst_finish_failure',
    'burst_expiry_started', 'burst_cancel_started', 'burst_containment_retry',
    'burst_clock_regression', 'burst_debt_regression', 'burst_ack_replay',
    'burst_serial_failure', 'burst_output_priority', 'rx_priority_lease_expiry',
    'rx_boundary_lease_expiry', 'radio_notify_wakeup', 'radio_notify_decode',
    'radio_notify_serial_failure', 'warm_lease_expiry', 'rx_error_rearm_failure',
])
def test_diagnostics_preserve_actual_driver_fault_controls(native_profile, case):
    run(native_profile, case)


@pytest.mark.parametrize('case', ['profile_query', 'profile_busy', 'profile_timings',
    'profile_failed_start', 'profile_failed_rx', 'profile_failed_finish', 'profile_irq_race',
    'profile_memory'])
def test_diagnostic_timing_and_readonly_controls(native_profile, case):
    if not native_profile[1]:
        run(native_profile, 'profile_disabled')
    else:
        run(native_profile, case)


def test_native_records_cross_decode_with_exact_identity(native_profile):
    if not native_profile[1]:
        run(native_profile, 'profile_disabled')
        return
    frames = Decoder().feed(run(native_profile, 'profile_wire'))
    assert len(frames) == len(NativePhase) + 1
    cap = NativeProfileCapability.from_frame(frames[0], boot=BOOT, request=100)
    assert cap.phases == 12 and cap.record_bytes == 40
    records = [NativeProfileRecord.from_frame(frame, boot=BOOT, request=101 + index,
                                            phase=NativePhase(index))
               for index, frame in enumerate(frames[1:])]
    assert records[NativePhase.ISSUED_TO_STARTED].total_us == 83
    assert records[NativePhase.TX_DONE_TO_RX_RETURN].total_us == 97
    assert records[NativePhase.RX_START_CALL].calls == 2
    assert records[NativePhase.RX_START_CALL].min_us == 0
    assert all(row.exact and row.failures == 0 for row in records)
    assert {row.phase for row in records if row.inclusive} == {
        NativePhase.ISSUED_TO_STARTED, NativePhase.TX_DONE_TO_RX_RETURN}


def test_native_memory_cross_codec(native_profile):
    if not native_profile[1]:
        run(native_profile, 'profile_disabled')
        return
    frame, = Decoder().feed(run(native_profile, 'profile_memory_wire'))
    value = NativeMemorySnapshot.from_frame(frame, boot=BOOT, request=100)
    assert value == NativeMemorySnapshot(123456, 100000, 65536, 4096)
    assert memory_query(boot=BOOT, request=100).body == b'\xfe'
    for invalid in (replace(frame, boot=bytes(16)), replace(frame, request=99),
                    replace(frame, type=Message.BURST_STATUS_REPLY),
                    replace(frame, body=frame.body[:-1]),
                    replace(frame, body=b'MNP1' + frame.body[4:]),
                    replace(frame, body=frame.body[:5] + b'\x04' + frame.body[6:]),
                    replace(frame, body=frame.body[:7] + b'\x01' + frame.body[8:]),
                    replace(frame, body=frame.body[:8] + struct.pack('!IIII', 1, 2, 1, 1)),
                    replace(frame, body=frame.body[:8] + struct.pack('!IIII', 1, 1, 2, 1))):
        with pytest.raises(SerialError):
            NativeMemorySnapshot.from_frame(invalid, boot=BOOT, request=100)
    zero = replace(frame, body=frame.body[:8] + bytes(16))
    assert NativeMemorySnapshot.from_frame(zero, boot=BOOT, request=100).loop_stack_minimum_free_bytes == 0


def record(**changes):
    values = dict(calls=2, failures=1, invalid=0, flags=0, total=30, minimum=10, maximum=20)
    values.update(changes)
    return Frame(Message.NATIVE_PROFILE_REPLY, BOOT, 10,
                 b'MNP1' + bytes((12, 40, NativePhase.ISSUE_STANDBY, 1))
                 + struct.pack('!IIIIQQQ', *values.values()))


def decode(frame):
    return NativeProfileRecord.from_frame(frame, boot=BOOT, request=10,
                                         phase=NativePhase.ISSUE_STANDBY)


def test_strict_native_profile_codec_and_queries():
    assert decode(record()).failures == 1
    assert not decode(record(calls=3, invalid=1, flags=2)).exact
    assert not decode(record(flags=1, total=2**64-1)).exact
    assert decode(record(calls=0, failures=0, total=0, minimum=0, maximum=0)).calls == 0
    for phase in NativePhase:
        f = query(boot=BOOT, request=1, phase=phase)
        assert f.type is Message.NATIVE_PROFILE and f.body == bytes((phase,))
    assert query(boot=BOOT, request=1).body == b''
    for changes in (dict(calls=0), dict(failures=3), dict(invalid=3, flags=2),
                    dict(flags=4), dict(invalid=1), dict(flags=2),
                    dict(minimum=21), dict(total=9), dict(total=41),
                    dict(calls=0, failures=0, flags=1, total=100, minimum=1, maximum=100),
                    dict(flags=1),
                    dict(calls=1, invalid=1, flags=2, failures=0)):
        with pytest.raises(SerialError):
            decode(record(**changes))
    for f in (replace(record(), boot=bytes(16)), replace(record(), request=11),
              replace(record(), type=Message.BURST_STATUS_REPLY),
              replace(record(), body=record().body[:-1]),
              replace(record(), body=b'MNP2' + record().body[4:]),
              replace(record(), body=record().body[:6] + b'\x00' + record().body[7:])):
        with pytest.raises(SerialError):
            decode(f)
    for args in (dict(request=0), dict(request=True), dict(request=1, phase=0)):
        with pytest.raises(SerialError):
            query(boot=BOOT, **args)
