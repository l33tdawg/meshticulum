"""Offline controls for the third-party LoRa backend/profile contract."""
from dataclasses import replace
import hashlib

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Outcome, SerialError
from meshticulum.bench.serial_worker import SerialWorker, journal_binding
from tests.backend_conformance import (
    BackendBounds,
    BackendDescriptor,
    FakeBackendChannel,
    descriptor_document,
)

BOOT = bytes.fromhex('0000aabbccddeeff0000000000000042')
BOUNDS = BackendBounds(
    frequency_hz=921375000,
    bandwidth_hz=125000,
    spreading_factor=8,
    coding_rate=5,
    chip_power_dbm=0,
    preamble=8,
    sync_word=0x42,
    lease_ms=60000,
    max_boot_attempts=10,
    max_boot_estimated_airtime_us=10000000,
)
DESCRIPTOR = BackendDescriptor(
    backend_name='conformance-serial',
    transport_kind='serial',
    board_id=1,
    firmware='mtb1-contract-1',
    boot_id=BOOT,
    bounds=BOUNDS,
)


def setup(tmp_path, fault=None):
    tmp_path.chmod(0o700)
    channel = FakeBackendChannel(DESCRIPTOR, fault=fault)
    worker = SerialWorker(channel)
    identity = worker.discover(
        expected_board_id=DESCRIPTOR.board_id,
        expected_version=DESCRIPTOR.firmware,
    )
    context = hashlib.sha256(b'third-party-backend-conformance').digest()
    journal = AttemptJournal(
        tmp_path / 'attempts.sqlite',
        binding=journal_binding(context, identity, DESCRIPTOR.profile),
        max_attempts=DESCRIPTOR.bounds.max_boot_attempts,
        max_airtime_us=DESCRIPTOR.bounds.max_boot_estimated_airtime_us,
        create=True,
        request_clock=lambda: 100000000,
    )
    worker.arm(
        journal=journal,
        profile=DESCRIPTOR.profile,
        context_digest=context,
        authorize=lambda: True,
        authority_deadline=200.0,
    )
    return worker, channel, journal


def test_descriptor_carries_explicit_identity_and_board_bounds():
    record = DESCRIPTOR.record()
    assert record['schema'] == 'meshticulum.backend-descriptor.v1'
    assert record['backend_name'] == 'conformance-serial'
    assert record['transport_kind'] == 'serial'
    assert record['boot_id_hex'] == BOOT.hex()
    assert record['bounds'] == {
        'frequency_hz': 921375000,
        'bandwidth_hz': 125000,
        'spreading_factor': 8,
        'coding_rate': 5,
        'chip_power_dbm': 0,
        'preamble': 8,
        'sync_word': 0x42,
        'lease_ms': 60000,
        'max_boot_attempts': 10,
        'max_boot_estimated_airtime_us': 10000000,
    }
    assert descriptor_document(DESCRIPTOR) == descriptor_document(DESCRIPTOR)


@pytest.mark.parametrize('change,reason', [
    ({'frequency_hz': 901999999}, 'invalid_profile'),
    ({'bandwidth_hz': 100000}, 'invalid_profile'),
    ({'spreading_factor': 4}, 'invalid_profile'),
    ({'coding_rate': 4}, 'invalid_profile'),
    ({'chip_power_dbm': 11}, 'invalid_profile'),
    ({'preamble': 7}, 'invalid_profile'),
    ({'lease_ms': 999}, 'invalid_profile'),
    ({'max_boot_attempts': 0}, 'invalid_profile'),
    ({'max_boot_estimated_airtime_us': 0}, 'invalid_profile'),
])
def test_profile_bounds_are_explicit_and_validated(change, reason):
    bad_bounds = replace(BOUNDS, **change)
    with pytest.raises(SerialError, match=reason):
        BackendDescriptor(
            backend_name=DESCRIPTOR.backend_name,
            transport_kind=DESCRIPTOR.transport_kind,
            board_id=DESCRIPTOR.board_id,
            firmware=DESCRIPTOR.firmware,
            boot_id=DESCRIPTOR.boot_id,
            bounds=bad_bounds,
        )


def test_identity_mismatch_never_reaches_arm(tmp_path):
    channel = FakeBackendChannel(DESCRIPTOR, fault='identity')
    worker = SerialWorker(channel)
    with pytest.raises(SerialError):
        worker.discover(
            expected_board_id=DESCRIPTOR.board_id,
            expected_version=DESCRIPTOR.firmware,
        )
    assert not any(frame.type.name.endswith('ARM') for frame in channel.writes)


def test_arm_echo_transmit_evidence_receive_and_disarm(tmp_path):
    worker, channel, journal = setup(tmp_path)
    try:
        result = worker.transmit(b'opaque-ciphertext')
        assert result.outcome == 'RF_DONE'
        assert result.driver.outcome is Outcome.RF_DONE
        assert result.driver_elapsed_clock_basis == 'coprocessor_monotonic'
        assert journal.snapshot()['attempts'] == 1
        assert journal.snapshot()['reserved_estimated_airtime_us'] > 0

        channel.receive_packet(b'peer-ciphertext')
        received = worker.receive()
        assert received[1].packet == b'peer-ciphertext'
        assert 'peer-ciphertext' not in repr(received[1])

        worker.disarm()
        with pytest.raises(HardwareError, match='radio_lease_too_short'):
            worker.transmit(b'after-disarm')
    finally:
        journal.close()


def test_arm_refusal_prevents_transmit(tmp_path):
    channel = FakeBackendChannel(DESCRIPTOR, fault='arm_refused')
    worker = SerialWorker(channel)
    identity = worker.discover(
        expected_board_id=DESCRIPTOR.board_id,
        expected_version=DESCRIPTOR.firmware,
    )
    context = hashlib.sha256(b'refused-arm').digest()
    journal = AttemptJournal(
        tmp_path / 'refused.sqlite',
        binding=journal_binding(context, identity, DESCRIPTOR.profile),
        max_attempts=DESCRIPTOR.bounds.max_boot_attempts,
        max_airtime_us=DESCRIPTOR.bounds.max_boot_estimated_airtime_us,
        create=True,
    )
    try:
        with pytest.raises(HardwareError, match='arm_not_confirmed'):
            worker.arm(
                journal=journal,
                profile=DESCRIPTOR.profile,
                context_digest=context,
                authorize=lambda: True,
                authority_deadline=200.0,
            )
        assert not any(frame.type.name == 'TRANSMIT' for frame in channel.writes)
        assert journal.snapshot()['attempts'] == 0
    finally:
        journal.close()


def test_refusal_has_no_completion_evidence_but_keeps_debt(tmp_path):
    worker, channel, journal = setup(tmp_path, fault='transmit_refused')
    try:
        result = worker.transmit(b'opaque-ciphertext')
        assert result.outcome == 'REFUSED'
        assert result.driver is None
        assert journal.snapshot()['attempts'] == 1
        assert journal.snapshot()['reserved_estimated_airtime_us'] > 0
        assert not worker.fenced
    finally:
        journal.close()


def test_unknown_outcome_fences_and_never_refunds_the_attempt(tmp_path):
    worker, channel, journal = setup(tmp_path, fault='unknown')
    try:
        result = worker.transmit(b'opaque-ciphertext')
        assert result.outcome == 'UNKNOWN'
        assert worker.fenced
        snapshot = journal.snapshot()
        assert snapshot['attempts'] == 1
        assert snapshot['reserved_estimated_airtime_us'] == result.reserved_estimated_us
        before = len(channel.writes)
        with pytest.raises(HardwareError, match='radio_authority_refused'):
            worker.transmit(b'must-not-send')
        assert len(channel.writes) == before
    finally:
        journal.close()


def test_expired_authority_refuses_before_reservation(tmp_path):
    worker, channel, journal = setup(tmp_path, fault='transmit_refused')
    try:
        channel.time = 159.9
        with pytest.raises(HardwareError, match='radio_lease_too_short'):
            worker.transmit(b'too-late')
        assert journal.snapshot()['attempts'] == 0
        assert journal.snapshot()['reserved_estimated_airtime_us'] == 0
    finally:
        journal.close()
