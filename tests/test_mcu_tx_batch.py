"""Dormant finite-vector prototype: actual C++ with synthetic driver/time."""
from pathlib import Path
import subprocess

import pytest

from meshticulum.bench.burst_batch_protocol import BatchPlan
from meshticulum.bench.tx_burst import Binding, PacketDescriptor, QueueDescriptor

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def batch_native(tmp_path_factory):
    executable = tmp_path_factory.mktemp('mcu-tx-batch') / 'fault-tests'
    subprocess.run([
        'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
        '-fsanitize=address,undefined', '-g',
        '-I', str(ROOT / 'firmware/meshticulum-bench'),
        '-I', str(ROOT / 'tests/meshticulum_bench_native'),
        str(ROOT / 'tests/mcu_batch_native/tx_batch.cpp'), '-o', str(executable),
    ], check=True, capture_output=True, text=True)
    return executable


@pytest.mark.parametrize('scenario', [
    'prepaid', 'partial', 'bad_stage', 'wrong_binding', 'manifest_mismatch',
    'order', 'budget', 'airtime_budget', 'overflow', 'replay', 'stale',
    'deadline', 'hold', 'revoke_staged', 'revoke_committed', 'unknown', 'expiry',
    'pacing', 'release_atomic', 'release_replay', 'release_pending', 'clock', 'debt',
    'stage_expiry', 'active_mutation', 'count', 'one_tail', 'poll_unknown',
    'new_owner', 'poll_new_owner',
    'native_cap', 'boot_changed', 'pacing_overflow',
    'receive_delayed', 'receive_stale', 'receive_failed', 'receive_clock',
    'receive_expiry', 'receive_tail_new_owner', 'receive_failed_new_owner',
    'receive_expiry_after_rearm',
])
def test_native_batch_faults(batch_native, scenario):
    subprocess.run([str(batch_native), scenario], check=True, capture_output=True, timeout=10)


def test_real_native_sha256_manifest_matches_python(batch_native):
    payloads = tuple(bytes((i + n) % 256 for i in range(255)) for n in range(4))
    packets = tuple(PacketDescriptor.from_ciphertext(11 + n, data, 100_000, 2_500_000)
                    for n, data in enumerate(payloads))
    queue = QueueDescriptor(Binding(b'\x01' + bytes(15), b'\x02' + bytes(31)),
                            1_000_000, 2_500_000, 2_500_000, packets)
    plan = BatchPlan(queue, 1, 10, payloads)
    result = subprocess.run([str(batch_native), 'manifest'], check=True,
                            capture_output=True, text=True, timeout=10)
    assert result.stdout.strip() == plan.manifest.hex()
