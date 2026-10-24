"""Offline contract tests; these values do not drive firmware or qualify RF."""
from dataclasses import replace
import hashlib

import pytest

from meshticulum.bench.tx_burst import (
    Binding, BurstContractError, EvidenceHistory, MAX_AUTONOMOUS_HOLD_US,
    PacketDescriptor, PacketEvidence, PacketKind, QueueDescriptor, Stage,
    context_sha256,
)

BINDING = Binding(b'A' * 16, b'C' * 32)
NOW = 10_000_000
DEADLINE = NOW + 1_200_000


def descriptor(count=4):
    packets = tuple(PacketDescriptor.from_ciphertext(100 + index,
        bytes([index]) * 255, 99_904, DEADLINE) for index in range(count))
    return QueueDescriptor(BINDING, NOW, NOW + 90_000_000, DEADLINE, packets)


def evidence(packet, stage, at_us=NOW + 1000, binding=BINDING):
    driver = ((0, 0, 0) if stage is Stage.STARTED else
              (110_000, 1, 0) if stage is Stage.RF_DONE else (None, None, None))
    return PacketEvidence(binding, packet, stage, at_us, *driver)


def test_descriptor_is_bounded_canonical_and_excludes_ciphertext():
    value = descriptor()
    raw = value.encode()
    assert len(raw) == 297
    assert QueueDescriptor.decode(raw) == value
    assert QueueDescriptor.decode(raw).encode() == raw
    assert bytes([3]) * 255 not in raw
    value.check_admission(now_us=NOW, independent_airtime_us=(99_904,) * 4)
    for packet in value.packets:
        value.check_dispatch(packet.request, now_us=NOW, independent_airtime_us=99_904)


@pytest.mark.parametrize('transform', [
    lambda raw: raw[:-1],
    lambda raw: raw + b'\0',
    lambda raw: b'BTD2' + raw[4:],
    lambda raw: raw[:76] + b'\x05' + raw[77:],
    lambda raw: raw[:85] + b'\x02' + raw[86:],
])
def test_malformed_descriptors_cannot_change_shape_or_add_control_roles(transform):
    with pytest.raises(BurstContractError):
        QueueDescriptor.decode(transform(descriptor().encode()))


def test_exact_ciphertext_and_binding_are_independent_of_same_length():
    packet = descriptor(1).packets[0]
    packet.check_ciphertext(bytes(255))
    for changed in (b'x' * 255, bytes(254), bytearray(255)):
        with pytest.raises(BurstContractError, match='ciphertext_changed'):
            packet.check_ciphertext(changed)
    profile = b'encoded-profile'
    original = context_sha256(b'G' * 32, profile)
    assert original != context_sha256(b'H' * 32, profile)
    assert original != context_sha256(b'G' * 32, profile + b'x')


@pytest.mark.parametrize('change', [
    dict(deadline_us=NOW + MAX_AUTONOMOUS_HOLD_US + 1),
    dict(original_deadline_us=DEADLINE - 1),
    dict(enqueued_at_us=DEADLINE),
    dict(packets=[]),
    dict(packets=descriptor().packets + descriptor().packets[:1]),
    dict(packets=(descriptor().packets[1], descriptor().packets[0])),
    dict(packets=(replace(descriptor().packets[0], deadline_us=DEADLINE - 1),)),
])
def test_no_deadline_renewal_unbounded_hold_or_duplicate_request(change):
    with pytest.raises(BurstContractError):
        replace(descriptor(), **change)


def test_admission_checks_independent_estimates_and_remaining_room():
    value = descriptor()
    for estimates in ((1, 1, 1, 1), (99_904,) * 3, [99_904] * 4,
                      (True, 99_904, 99_904, 99_904)):
        with pytest.raises(BurstContractError, match='independent_airtime'):
            value.check_admission(now_us=NOW, independent_airtime_us=estimates)
    with pytest.raises(BurstContractError, match='cannot_fit'):
        value.check_admission(now_us=DEADLINE - 799_615,
                              independent_airtime_us=(99_904,) * 4)
    with pytest.raises(BurstContractError, match='clock_regressed'):
        value.check_admission(now_us=NOW - 1, independent_airtime_us=(99_904,) * 4)
    with pytest.raises(BurstContractError, match='cannot_fit'):
        value.check_dispatch(100, now_us=DEADLINE - 199_903,
                             independent_airtime_us=99_904)
    with pytest.raises(BurstContractError, match='foreign_burst_request'):
        value.check_dispatch(999, now_us=NOW, independent_airtime_us=99_904)


def test_queue_acceptance_remains_physically_pending_and_exact_replay_is_idempotent():
    value = descriptor(1)
    history = EvidenceHistory(value)
    queued = evidence(value.packets[0], Stage.QUEUED)
    assert history.observe(queued)
    assert history.pending_requests == (100,)
    assert history.rf_done_requests == ()
    assert not history.observe(queued)
    assert history.pending_requests == (100,)
    assert not history.fenced


def test_lost_intermediate_reports_and_late_physical_completion_still_account_once():
    value = descriptor(1)
    history = EvidenceHistory(value)
    done = evidence(value.packets[0], Stage.RF_DONE, at_us=DEADLINE + 500_000)
    assert history.observe(done)
    assert history.pending_requests == () and history.rf_done_requests == (100,)
    assert not history.observe(done)
    assert not history.fenced
    with pytest.raises(BurstContractError, match='terminal_evidence_changed'):
        history.observe(replace(done, at_us=done.at_us + 1))
    assert history.fenced and history.rf_done_requests == (100,)


@pytest.mark.parametrize('stage', [Stage.ISSUED, Stage.STARTED])
def test_driver_handoff_cannot_be_reclassified_as_definitely_unsent(stage):
    value = descriptor(1)
    history = EvidenceHistory(value)
    history.observe(evidence(value.packets[0], stage))
    with pytest.raises(BurstContractError, match='cannot_be_unsent'):
        history.observe(evidence(value.packets[0], Stage.NOT_SENT, NOW + 2000))
    assert history.fenced and history.pending_requests == (100,)


def test_unknown_is_sticky_and_pending_physical_results_survive_a_fence():
    value = descriptor(2)
    history = EvidenceHistory(value)
    history.observe(evidence(value.packets[0], Stage.UNKNOWN))
    assert history.fenced and history.pending_requests == (101,)
    history.observe(evidence(value.packets[1], Stage.NOT_SENT))
    assert history.fenced and history.pending_requests == ()
    assert history.rf_done_requests == ()
    with pytest.raises(BurstContractError, match='terminal_evidence_changed'):
        history.observe(evidence(value.packets[0], Stage.RF_DONE, NOW + 2000))
    assert history.fenced and history.rf_done_requests == ()


@pytest.mark.parametrize('changed', [
    dict(binding=Binding(b'B' * 16, BINDING.context_sha256)),
    dict(binding=Binding(BINDING.boot, b'P' * 32)),
    dict(packet=replace(descriptor(1).packets[0], digest=hashlib.sha256(b'other').digest())),
    dict(packet=replace(descriptor(1).packets[0], size=254)),
    dict(packet=replace(descriptor(1).packets[0], estimated_airtime_us=99_905)),
    dict(packet=replace(descriptor(1).packets[0], deadline_us=DEADLINE + 1)),
])
def test_foreign_or_changed_terminal_evidence_cannot_settle_a_request(changed):
    value = descriptor(1)
    history = EvidenceHistory(value)
    bad = replace(evidence(value.packets[0], Stage.RF_DONE), **changed)
    with pytest.raises(BurstContractError):
        history.observe(bad)
    assert history.fenced and history.pending_requests == (100,)
    assert history.rf_done_requests == ()


@pytest.mark.parametrize('change', [
    dict(irq_flags=0), dict(irq_flags=0x201), dict(driver_error=-1),
    dict(driver_elapsed_us=0), dict(driver_elapsed_us=True),
])
def test_rf_done_requires_consistent_local_driver_evidence(change):
    done = evidence(descriptor(1).packets[0], Stage.RF_DONE)
    with pytest.raises(BurstContractError):
        replace(done, **change)


def test_activity_cannot_be_reported_after_expiry_or_clock_rollback():
    value = descriptor(1)
    history = EvidenceHistory(value)
    history.observe(evidence(value.packets[0], Stage.QUEUED, NOW + 1000))
    with pytest.raises(BurstContractError, match='clock_regressed'):
        history.observe(evidence(value.packets[0], Stage.ISSUED, NOW + 999))
    assert history.fenced
    fresh = EvidenceHistory(value)
    with pytest.raises(BurstContractError, match='after_deadline'):
        fresh.observe(evidence(value.packets[0], Stage.ISSUED, DEADLINE))
    assert fresh.fenced and fresh.pending_requests == (100,)


def test_local_data_role_is_explicit_and_cannot_be_a_remote_priority_label():
    packet = descriptor(1).packets[0]
    assert packet.kind is PacketKind.DATA
    for kind in (1, True, 'DATA', 'ACK', 2):
        with pytest.raises(BurstContractError, match='local_data_role'):
            replace(packet, kind=kind)


def test_native_evidence_values_and_nonempty_packet_digest_agree():
    assert [int(stage) for stage in Stage] == [1, 2, 3, 4, 5, 6]
    assert int(Stage.NOT_SENT) == 5 and int(Stage.UNKNOWN) == 6
    with pytest.raises(BurstContractError, match='empty_burst_packet_digest'):
        replace(descriptor(1).packets[0], digest=bytes(32))
