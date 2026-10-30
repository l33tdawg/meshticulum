"""Explicit scalar-eight admission and durable lifecycle; no radio/device."""
from dataclasses import replace

import pytest

from meshticulum.bench.burst_protocol import (
    CAPABILITY, CAPABILITY8, EVENT, OPEN, POLICY, POLICY8, STATUS,
    BurstStatus, OpenBinding, capability_request_body, check_capability,
    open_frame, policy_depth, policy_for_depth, validate_policy,
)
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Frame, Message
from meshticulum.bench.tx_burst import BurstContractError, Stage
from tests.test_burst_worker import BOOT, PROFILE, VERSION, Channel, setup

SF5 = replace(PROFILE, spreading_factor=5, preamble=12)


def test_policy_is_explicit_and_legacy_bytes_are_unchanged():
    assert policy_for_depth() == POLICY and policy_depth(POLICY) == 4
    assert policy_for_depth(8) == POLICY8 and policy_depth(POLICY8) == 8
    assert capability_request_body() == b'' and capability_request_body(8) == b'\x08'
    assert CAPABILITY[:5] == b'MBQ1\x04' and CAPABILITY8[:5] == b'MBQ8\x08'
    copied = policy_for_depth(8)
    copied['max_depth'] = 4
    with pytest.raises(BurstContractError):
        validate_policy(copied)
    assert policy_for_depth(8)['max_depth'] == 8
    for depth in (True, 4.0, 0, 5, 16):
        with pytest.raises(BurstContractError):
            policy_for_depth(depth)
    for mutation in ({'train_frames': 4}, {'max_hold_us': 2_000_001},
                     {'inter_packet_rx_us': 0}, {'reverse_listen_us': 0}):
        with pytest.raises(BurstContractError):
            validate_policy(dict(POLICY8, **mutation))


@pytest.mark.parametrize('depth,body', [(4, CAPABILITY8), (8, CAPABILITY)])
def test_other_capability_is_not_a_silent_fallback(depth, body):
    with pytest.raises(BurstContractError, match='unsupported_burst_capability'):
        check_capability(Frame(Message.BURST_CAPABILITIES_REPLY, BOOT, 9, body),
                         boot=BOOT, request=9, depth=depth)


def test_eight_open_and_status_cannot_be_decoded_as_legacy():
    opened = open_frame(BOOT, 5, b'C' * 32, 3, 50_000_000, depth=8)
    assert len(opened.body) == OPEN.size + 1 and opened.body[0] == 8
    reply = replace(opened, type=Message.BURST_OPEN_REPLY)
    assert OpenBinding.from_frame(reply, boot=BOOT, request=5, depth=8,
                                  expected_body=opened.body).depth == 8
    with pytest.raises(BurstContractError):
        OpenBinding.from_frame(reply, boot=BOOT, request=5)
    with pytest.raises(BurstContractError):
        OpenBinding.from_frame(replace(reply, body=b'\x04'+reply.body[1:]),
                               boot=BOOT, request=5, depth=8)
    body = STATUS.pack(5, b'C'*32, 3, 50_000_000, 8, 8, 0, 0, 0,
                       10_000_000, 10_100_000, 8, 800_000)
    frame = Frame(Message.BURST_STATUS_REPLY, BOOT, 9, body)
    assert BurstStatus.from_frame(frame, boot=BOOT, request=9, depth=8).pending == 8
    with pytest.raises(BurstContractError):
        BurstStatus.from_frame(frame, boot=BOOT, request=9)


def test_old_firmware_rejects_eight_before_arm_or_journal():
    channel = Channel()
    worker = BurstSerialWorker(channel, policy=policy_for_depth(8))
    with pytest.raises((BurstContractError, HardwareError)):
        worker.discover(expected_board_id=1, expected_version=VERSION)
    assert not channel.arm_request and not channel.attempts and worker.journal is None
    assert all(frame.type not in (Message.ARM, Message.BURST_OPEN, Message.BURST_DATA)
               for frame in channel.writes)


def test_eight_requires_sf5_before_arm(setup):
    with pytest.raises(HardwareError, match='burst_eight_requires_sf5'):
        setup(train_depth=8)


def test_all_eight_retain_debt_and_capacity_until_last_confirmed_release(setup):
    worker, channel, journal, _ = setup(profile=SF5, train_depth=8)
    results = [worker.queue_data(bytes([n])*255, logical_id=bytes([n+1])*32)
               for n in range(8)]
    assert len(worker.pending_physical) == 8 and not worker.has_capacity
    with pytest.raises(HardwareError, match='burst_queue_capacity'):
        worker.queue_data(b'ninth', logical_id=b'z'*32)
    assert journal.snapshot()['attempts'] == channel.attempts == 8
    debt = journal.snapshot()['reserved_estimated_airtime_us']
    for result in results[:-1]:
        channel.event(result.request, Stage.RF_DONE)
    assert len(worker.poll_physical()) == 7
    assert len(worker.pending_physical) == 1 and not worker.has_capacity
    channel.event(results[-1].request, Stage.RF_DONE)
    assert len(worker.poll_physical()) == 1
    worker.assert_drained()
    assert worker.has_capacity and all(row['released'] for row in journal.queued_records())
    assert journal.snapshot()['reserved_estimated_airtime_us'] == debt
    assert len(worker._released_events) == len(worker._release_replies) == 8
    # A complete train's delayed exact events/replies remain idempotent.
    for request, body in tuple(worker._released_events):
        channel.offer(Frame(Message.BURST_EVENT, BOOT, request, body))
    for reply in tuple(worker._release_replies):
        channel.offer(reply)
    assert worker.poll_physical() == [] and not worker._routed and not worker.fenced
    ninth = worker.queue_data(b'ninth', logical_id=b'z'*32)
    channel.event(ninth.request, Stage.RF_DONE)
    worker.poll_physical()
    assert len(worker._released_events) == len(worker._release_replies) == 8
    assert journal.snapshot()['attempts'] == 9


def test_eight_partial_write_fences_and_preserves_all_prior_debt(setup):
    worker, channel, journal, _ = setup(profile=SF5, train_depth=8)
    for n in range(7):
        worker.queue_data(bytes([n])*255, logical_id=bytes([n+1])*32)
    channel.fault = 'partial_write'
    assert worker.queue_data(b'eighth', logical_id=b'e'*32).outcome == 'UNKNOWN'
    assert worker.fenced and journal.snapshot()['attempts'] == 8
    assert len(worker.pending_physical) == 8
    assert all(row['state'] == 'UNKNOWN' and not row['released']
               for row in journal.queued_records())
