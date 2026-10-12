# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Host/firmware codec parity and fault injection in the actual firmware loop.

Radio, time, NVS and SHA hardware calls are mocked in the native harness. These
tests cannot establish RF delivery, hardware initialization or crypto strength.
"""
from dataclasses import replace
from pathlib import Path
import hashlib
import random
import struct
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from meshticulum.bench.serial_protocol import (
    Decoder, FirmwareIdentity, Frame, MAX_RAW, Message, Outcome, RadioProfile,
    RawReceive, SerialError, TxEvidence)

BOOT = bytes.fromhex('0000545662f61b440000000000000012')


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    directory = ROOT / 'tests/meshticulum_bench_native'
    executable = tmp_path_factory.mktemp('mtbench-native') / 'fault-tests'
    subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-g', '-I', str(directory),
                    str(directory / 'harness.cpp'), '-o', str(executable)], check=True,
                   capture_output=True, text=True)
    return executable


@pytest.mark.parametrize('scenario', [
    'boot_failure', 'idle', 'complete_replay', 'timeout', 'conflicting_flags',
    'lost_interrupt', 'start_failure', 'finish_failure', 'clear_failure',
    'wrong_boot', 'lease_replay', 'budget', 'receive', 'backpressure', 'config_failure', 'irq_read_failure',
    'frequency_bounds', 'sf5_profile', 'sf5_short_preamble', 'warm_lease_expiry', 'warm_tx_fence', 'warm_config_failure'])
def test_actual_firmware_faults(native, scenario):
    subprocess.run([str(native), scenario], check=True, capture_output=True, timeout=10)


def test_cross_language_codec_and_every_fragment_boundary(native):
    rng = random.Random(78613)
    frames = [Frame(Message.TRANSMIT, BOOT, 0xDBC0, bytes([0xC0, 0xDB]) * 160)]
    frames += [Frame(Message.RECEIVE, rng.randbytes(16), rng.randrange(2**64), rng.randbytes(size))
               for size in (0, 1, 31, 255, 259, 319, 320)]
    for frame in frames:
        encoded = frame.encode()
        assert subprocess.run([str(native), 'codec'], input=encoded, check=True,
                              capture_output=True, timeout=10).stdout == encoded
        for boundary in range(len(encoded) + 1):
            decoder = Decoder()
            assert decoder.feed(encoded[:boundary]) + decoder.feed(encoded[boundary:]) == [frame]


def test_corrupt_truncated_oversized_and_bad_escape_recover(native):
    frame = Frame(Message.TRANSMIT, BOOT, 1, b'packet\xc0\xdb')
    good = frame.encode()
    malformed = [good[:-3] + bytes([0xC0]), bytes([0xC0, 0xDB, 1, 0xC0]),
                 bytes([0xC0]) + b'X' * (MAX_RAW + 100) + bytes([0xC0])]
    malformed += [good[:i] + bytes([good[i] ^ 0x40]) + good[i+1:] for i in range(1, len(good)-1)]
    for bad in malformed:
        data = bad + good
        assert Decoder().feed(data) == [frame]
        assert subprocess.run([str(native), 'codec'], input=data, check=True,
                              capture_output=True, timeout=10).stdout == good


def test_read_size_and_parser_memory_bound():
    decoder = Decoder()
    with pytest.raises(SerialError, match='invalid_read_size'):
        decoder.feed(b'x' * 4097)
    decoder.feed(b'\xc0')
    for _ in range(50):
        decoder.feed(b'x' * 4096)
        assert len(decoder._buffer) <= MAX_RAW
    assert decoder.feed(b'\xc0') == [] and decoder.rejected == 1


def hello(**changes):
    frame = Frame(Message.HELLO_REPLY, BOOT, 11,
                  struct.pack('!BBH', 1, 1, 255) + b'mtb1-0123456789\0')
    return replace(frame, **changes)


def test_hello_binds_board_and_firmware():
    options = dict(request=11, expected_board_id=1, expected_version='mtb1-0123456789')
    assert FirmwareIdentity.from_reply(hello(), **options).boot == BOOT
    invalid = [hello(boot=bytes(16)), hello(request=12),
               hello(body=bytes([2]) + hello().body[1:]),
               hello(body=bytes([1, 0]) + hello().body[2:]),
               hello(body=hello().body[:-2] + b'x\0')]
    for frame in invalid:
        with pytest.raises(SerialError):
            FirmwareIdentity.from_reply(frame, **options)


def evidence(outcome=Outcome.RF_DONE, **changes):
    fields = dict(digest=hashlib.sha256(b'packet').digest(), estimate=100000,
                  elapsed=110000, flags=1, error=0)
    fields.update(changes)
    return Frame(Message.TX_RESULT, BOOT, 15, struct.pack('!B32sQQIh', outcome,
                 fields['digest'], fields['estimate'], fields['elapsed'], fields['flags'], fields['error']))


def test_evidence_requires_exact_boot_attempt_and_ciphertext():
    options = dict(boot=BOOT, request=15, packet=b'packet')
    assert TxEvidence.from_reply(evidence(), **options).outcome is Outcome.RF_DONE
    invalid = [replace(evidence(), boot=bytes(16)), replace(evidence(), request=16),
               evidence(digest=bytes(32)), evidence(estimate=0), evidence(flags=0x201),
               evidence(flags=2), evidence(error=-1)]
    for frame in invalid:
        with pytest.raises(SerialError):
            TxEvidence.from_reply(frame, **options)
    assert TxEvidence.from_reply(evidence(Outcome.UNKNOWN, flags=0x201, error=-1),
                                 **options).outcome is Outcome.UNKNOWN
    assert TxEvidence.from_reply(evidence(Outcome.STARTED, elapsed=0, flags=0),
                                 **options).outcome is Outcome.STARTED
    with pytest.raises(SerialError):
        TxEvidence.from_reply(evidence(Outcome.STARTED), **options)


def test_receive_never_becomes_authenticated_or_application_delivery():
    frame = Frame(Message.RECEIVE, BOOT, 0, struct.pack('!hh', -925, -23) + b'ciphertext')
    received = RawReceive.from_frame(frame, boot=BOOT)
    assert (received.rssi_dbm, received.snr_db, received.packet) == (-92.5, -2.3, b'ciphertext')
    with pytest.raises(SerialError):
        RawReceive.from_frame(frame, boot=bytes(16))
    assert 'ciphertext' not in repr(received)


def test_board_specific_profile_limits_and_wire_layout():
    profile = RadioProfile(917375000, 62500, 8, 5, 10, 8, 0x42, 10000, 20, 5000000)
    encoded = profile.encode(board_id=1)
    assert len(encoded) == 30 and encoded[10] == 10 and encoded[13] == 0x42
    assert int.from_bytes(encoded[22:], 'big') == 5000000
    with pytest.raises(SerialError):
        replace(profile, chip_power_dbm=22).encode(board_id=1)
    assert replace(profile, chip_power_dbm=22).encode(board_id=2)[10] == 22
    with pytest.raises(SerialError):
        replace(profile, lease_ms=300001).encode(board_id=2)
    assert replace(profile, frequency_hz=921375000).encode(board_id=1)[:4] == struct.pack('!I', 921375000)
    for hz in (901999999, 902000000, 928000000, 928000001):
        with pytest.raises(SerialError):
            replace(profile, frequency_hz=hz).encode(board_id=1)
