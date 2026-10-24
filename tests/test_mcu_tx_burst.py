"""Dormant MCU queue state-machine controls; synthetic driver/time, never RF."""
from pathlib import Path
import subprocess

import pytest

from meshticulum.bench.tx_burst import (
    Binding, BurstContractError, EvidenceHistory, PacketDescriptor,
    PacketEvidence, QueueDescriptor, Stage,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def burst_native(tmp_path_factory):
    executable = tmp_path_factory.mktemp('mcu-tx-burst') / 'fault-tests'
    subprocess.run([
        'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
        '-fsanitize=address,undefined', '-g',
        '-I', str(ROOT / 'firmware/meshticulum-bench'),
        str(ROOT / 'tests/mcu_burst_native/tx_burst.cpp'), '-o', str(executable),
    ], check=True, capture_output=True, text=True)
    return executable


@pytest.mark.parametrize('scenario', [
    'prepaid', 'replay', 'invalid', 'budget', 'backpressure',
    'cancel_queued', 'cancel_issued', 'cancel_started',
    'expiry_queued', 'expiry_issued', 'late_result',
    'bad_digest', 'bad_flags', 'bad_error', 'bad_estimate', 'bad_size', 'unknown',
    'clock_regression', 'debt_regression', 'reconstruction_clean',
    'reconstruction_uncertain', 'reconstruction_clock', 'duplicate_owner',
])
def test_native_ciphertext_burst_faults(burst_native, scenario):
    subprocess.run([str(burst_native), scenario], check=True, capture_output=True, timeout=10)


def test_native_evidence_conforms_to_host_accounting(burst_native):
    """Trace actual native transitions through the host validator, without a wire codec."""
    now, deadline = 1_000_000, 2_500_000
    binding = Binding(b'\x01' + bytes(15), b'\x02' + bytes(31))
    packets = tuple(PacketDescriptor.from_ciphertext(request, bytes(range(255)),
                    100_000, deadline) for request in range(1, 4))
    descriptor = QueueDescriptor(binding, now, now + 10_000_000, deadline, packets)
    history = EvidenceHistory(descriptor)
    result = subprocess.run([str(burst_native), 'conformance'], check=True,
                            capture_output=True, text=True, timeout=10)
    observed = []
    for row in result.stdout.splitlines():
        (at, request, numeric_stage, size, estimate, packet_deadline, digest,
         boot, context, elapsed, flags, error) = row.split(',')
        packet = PacketDescriptor(int(request), int(size), bytes.fromhex(digest),
                                  int(estimate), int(packet_deadline))
        stage = Stage(int(numeric_stage))
        assert packet == descriptor.packet(int(request))
        assert Binding(bytes.fromhex(boot), bytes.fromhex(context)) == binding
        driver = tuple(map(int, (elapsed, flags, error)))
        if stage not in (Stage.STARTED, Stage.RF_DONE):
            assert driver == (0, 0, 0)
            driver = (None, None, None)
        evidence = PacketEvidence(binding, packet, stage, int(at), *driver)
        assert history.observe(evidence)
        observed.append((packet.request, stage, int(at)))
    assert observed == [
        (1, Stage.QUEUED, now), (2, Stage.QUEUED, now), (3, Stage.QUEUED, now),
        (1, Stage.ISSUED, now + 1000), (1, Stage.STARTED, now + 2000),
        (1, Stage.RF_DONE, now + 103000), (2, Stage.ISSUED, now + 104000),
        (2, Stage.UNKNOWN, now + 105000), (3, Stage.NOT_SENT, now + 105000),
    ]
    assert history.rf_done_requests == (1,)
    assert history.pending_requests == ()
    assert history.fenced
    # Lost delivery of an already emitted outcome can only replay, never settle twice.
    assert not history.observe(evidence)
    late_done = PacketEvidence(binding, packets[1], Stage.RF_DONE, now + 106000,
                               100_500, 1, 0)
    with pytest.raises(BurstContractError, match='terminal_evidence_changed'):
        history.observe(late_done)
    assert history.fenced and history.rf_done_requests == (1,)
