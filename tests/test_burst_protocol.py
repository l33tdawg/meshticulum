"""Local serial identity, proof and clock bounds; no serial port or RF."""
from dataclasses import replace
import hashlib

import pytest

from meshticulum.bench.burst_protocol import (
    ACK, CAPABILITY, EVENT, POLICY, STATUS, BurstEvent, BurstStatus,
    ClockSnapshot, OpenBinding, ack_frame, check_capability, data_frame,
    open_frame, validate_policy,
)
from meshticulum.bench.serial_protocol import Decoder, Frame, Message
from meshticulum.bench.tx_burst import Binding, BurstContractError, PacketDescriptor, Stage

BOOT = b'B' * 16
PACKET = bytes(range(255))
DIGEST = hashlib.sha256(PACKET).digest()


def event(stage=Stage.RF_DONE, **changes):
    values = dict(request=9, token=5, stage=stage, at_us=1_500_000,
        digest=DIGEST, estimated_airtime_us=99_904, size=255, deadline_us=2_000_000,
        driver_elapsed_us=100_000 if stage is Stage.RF_DONE else 0,
        irq_flags=1 if stage is Stage.RF_DONE else 0, driver_error=0)
    values.update(changes)
    return BurstEvent(**values)


def wire(value):
    return Frame(Message.BURST_EVENT, BOOT, value.request, EVENT.pack(
        value.token, value.stage, value.at_us, value.digest, value.estimated_airtime_us,
        value.size, value.deadline_us, value.driver_elapsed_us, value.irq_flags, value.driver_error))


def test_full_ciphertext_fits_wire_bound_and_is_bound_to_exact_descriptor():
    packet = PacketDescriptor.from_ciphertext(9, PACKET, 99_904, 2_000_000)
    frame = data_frame(BOOT, 5, packet, PACKET)
    assert len(frame.body) == 314 and Decoder().feed(frame.encode()) == [frame]
    with pytest.raises(BurstContractError, match='ciphertext_changed'):
        data_frame(BOOT, 5, packet, b'?' * 255)
    opened = open_frame(BOOT, 5, b'C' * 32, 3, 50_000_000)
    reply = replace(opened, type=Message.BURST_OPEN_REPLY)
    assert OpenBinding.from_frame(reply, boot=BOOT, request=5, expected_body=opened.body).token == 5
    with pytest.raises(BurstContractError, match='open_changed'):
        OpenBinding.from_frame(reply, boot=BOOT, request=5, expected_body=b'wrong')


@pytest.mark.parametrize('change', [dict(boot=b'X' * 16), dict(request=10),
    dict(type=Message.TX_RESULT), dict(body=b'truncated')])
def test_local_physical_evidence_cannot_change_boot_request_kind_or_shape(change):
    with pytest.raises(BurstContractError):
        BurstEvent.from_frame(replace(wire(event()), **change), boot=BOOT, request=9)


@pytest.mark.parametrize('change', [dict(irq_flags=0), dict(irq_flags=0x201),
    dict(driver_error=-1), dict(driver_elapsed_us=0), dict(size=0), dict(digest=bytes(32))])
def test_rf_done_requires_consistent_exact_driver_proof(change):
    with pytest.raises(BurstContractError):
        BurstEvent.from_frame(wire(event(**change)), boot=BOOT)


def test_unknown_diagnostics_do_not_become_completion_and_release_identity_is_exact():
    unknown = BurstEvent.from_frame(wire(event(Stage.UNKNOWN,
        irq_flags=0x200, driver_error=-1, driver_elapsed_us=1234)), boot=BOOT)
    proof = unknown.evidence(Binding(BOOT, b'C' * 32))
    assert proof.stage is Stage.UNKNOWN and proof.driver_elapsed_us is None
    release = ack_frame(BOOT, 10, unknown)
    assert ACK.unpack(release.body) == (5, 9, Stage.UNKNOWN, unknown.at_us, DIGEST)
    with pytest.raises(BurstContractError, match='not_terminal'):
        ack_frame(BOOT, 10, event(Stage.QUEUED))
    with pytest.raises(BurstContractError, match='packet_changed'):
        unknown.check_packet(replace(unknown.packet, deadline_us=2_000_001), token=5)


def test_mapping_is_conservative_for_full_open_horizon_at_minimum_clock_rate():
    snapshot = ClockSnapshot(10_000_000, 3, 200_000_000)
    sent, received, host_end = 100_000_000, 100_040_000, 220_040_000
    end = snapshot.map_deadline(host_deadline_us=host_end, sent_us=sent,
                               received_us=received, arm_request=3)
    assert end == snapshot.now_us + 118_800_000 - 20_000
    # Slowest allowed MCU: even a snapshot captured at host reply cannot
    # run past the host authority end. Earlier capture only narrows further.
    assert (end - snapshot.now_us) / .99 <= host_end - received
    burst = snapshot.map_deadline(host_deadline_us=host_end, sent_us=sent,
        received_us=received, arm_request=3, original_cap_us=end, burst=True)
    assert burst == snapshot.now_us + 1_980_000
    narrowed = snapshot.map_deadline(host_deadline_us=host_end, sent_us=sent,
        received_us=received, arm_request=3, original_cap_us=snapshot.now_us + 500_000, burst=True)
    assert narrowed == snapshot.now_us + 500_000


@pytest.mark.parametrize('change', [dict(sent_us=100_050_001),
    dict(received_us=100_050_001), dict(arm_request=4),
    dict(host_deadline_us=100_000_000)])
def test_clock_regression_sampling_delay_foreign_arm_and_expiry_refuse_mapping(change):
    values = dict(host_deadline_us=220_000_000, sent_us=100_000_000,
                  received_us=100_040_000, arm_request=3)
    values.update(change)
    with pytest.raises(BurstContractError):
        ClockSnapshot(10_000_000, 3, 200_000_000).map_deadline(**values)


def test_capability_and_policy_require_exact_types_and_all_bounds():
    check_capability(Frame(Message.BURST_CAPABILITIES_REPLY, BOOT, 1, CAPABILITY),
                     boot=BOOT, request=1)
    with pytest.raises(BurstContractError):
        check_capability(Frame(Message.BURST_CAPABILITIES_REPLY, BOOT, 1, CAPABILITY[:-1] + b'x'),
                         boot=BOOT, request=1)
    assert validate_policy(dict(POLICY)) == POLICY
    for changed in (dict(POLICY, max_depth=True), dict(POLICY, max_hold_us=2_000_001),
                    dict(POLICY, reverse_listen_us=0), dict(POLICY, extra='permission')):
        with pytest.raises(BurstContractError):
            validate_policy(changed)


def test_status_reports_retained_physical_work_and_rejects_invalid_flags():
    body = STATUS.pack(5, b'C' * 32, 3, 50_000_000, 1, 2, 1, 0, 0,
                       10_000_000, 10_200_000, 30, 1_000_000)
    frame = Frame(Message.BURST_STATUS_REPLY, BOOT, 11, body)
    status = BurstStatus.from_frame(frame, boot=BOOT, request=11)
    assert status.pending == 1 and status.retained == 2 and status.transmitting
    bad = STATUS.pack(5, b'C' * 32, 3, 50_000_000, 3, 2, 2, 0, 0,
                      10_000_000, 10_200_000, 30, 1_000_000)
    with pytest.raises(BurstContractError):
        BurstStatus.from_frame(replace(frame, body=bad), boot=BOOT, request=11)
