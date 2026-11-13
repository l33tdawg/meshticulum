"""Explicit train24 dynamic-fit experiment; all clocks and radios are models."""
from dataclasses import replace
import pytest
from meshticulum.bench.burst_protocol import (
    CAPABILITY12, CAPABILITY24, POLICY_TRAIN12, POLICY_TRAIN24, OPEN,
    capability_for_depth, capability_request_body, policy_for_train24,
    validate_policy, open_frame, OpenBinding, check_capability)
from meshticulum.bench.burst_worker import BurstSerialWorker
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.bench.serial_protocol import Frame, Message
from meshticulum.bench.tx_burst import BurstContractError, Stage
from meshticulum.bench.wire import FrameKind, WireError
from tests.test_burst_worker import setup, Channel, BOOT, VERSION, PROFILE
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model

SF5 = replace(PROFILE, spreading_factor=5, preamble=32)


def test_exact_v10_preserves_old_policy_and_capability():
    assert policy_for_train24() == dict(POLICY_TRAIN12,
        schema='meshticulum.data-burst-policy.v10', train_frames=24,
        dynamic_train_admission=True)
    assert capability_for_depth(8, train_frames=12) == CAPABILITY12
    assert capability_request_body(8, train_frames=24) == b'\x08\x18'
    assert CAPABILITY24[:7] == b'MBT2\x08\x18\x07'
    frame = open_frame(BOOT, 2, b'x'*32, 1, 3_000_000, depth=8, train_frames=24)
    assert frame.body == b'\x08\x18' + OPEN.pack(b'x'*32, 1, 3_000_000)
    assert OpenBinding.from_frame(replace(frame, type=Message.BURST_OPEN_REPLY),
        boot=BOOT, request=2, depth=8, train_frames=24).train_frames == 24
    reply = Frame(Message.BURST_CAPABILITIES_REPLY, BOOT, 2, CAPABILITY24)
    check_capability(reply, boot=BOOT, request=2, depth=8, train_frames=24)
    with pytest.raises(BurstContractError):
        check_capability(reply, boot=BOOT, request=2, depth=8, train_frames=12)


@pytest.mark.parametrize('change', [dict(train_frames=True), dict(train_frames=24.0),
    dict(dynamic_train_admission=False), dict(max_depth=24), dict(max_hold_us=2_000_001),
    dict(reverse_listen_us=99_999), dict(inter_packet_rx_us=4_999),
    dict(flow_window_frames=64), dict(selective_repair_train=False), dict(extra=True)])
def test_v10_shape_and_guard_mutations_refused(change):
    with pytest.raises(BurstContractError):
        validate_policy(dict(POLICY_TRAIN24, **change))


def test_train12_firmware_cannot_satisfy_v10_before_arm():
    channel = Channel(train_depth=8, train_frames=12, profile=SF5)
    worker = BurstSerialWorker(channel, policy=POLICY_TRAIN24)
    with pytest.raises((HardwareError, BurstContractError)):
        worker.discover(expected_board_id=1, expected_version=VERSION)
    assert worker.journal is None and channel.attempts == channel.arm_request == 0


def test_budget_refusal_latches_until_all_retained_evidence_released(setup):
    worker, channel, journal, _ = setup(profile=SF5, train_depth=8, train24=True)
    accepted = [worker.queue_data(bytes([i])*255, logical_id=bytes([i+1])*32)
                for i in range(7)]
    deadline = worker._burst_deadline
    clock_count = sum(f.type is Message.BURST_CLOCK for f in channel.writes)
    channel.fault = 'budget_refused'
    refused = worker.queue_data(b'late', logical_id=b'z'*32)
    assert refused.outcome == 'REFUSED' and worker.train_admission_refused
    assert not worker.has_capacity and journal.snapshot()['attempts'] == 8
    assert channel.attempts == 7 and worker._burst_deadline == deadline
    for _ in range(3):
        with pytest.raises(HardwareError, match='burst_queue_capacity'):
            worker.queue_data(b'no repeat', logical_id=b'y'*32)
    with pytest.raises(HardwareError, match='refused_train_not_drained'):
        worker.retire_refused_train()
    assert journal.snapshot()['attempts'] == 8
    assert sum(f.type is Message.BURST_CLOCK for f in channel.writes) == clock_count
    for row in accepted:
        channel.event(row.request, Stage.RF_DONE)
    results = worker.drain_physical()
    assert len(results) == 7 and not worker.has_capacity
    assert all(r['released'] for r in journal.queued_records())
    worker.retire_refused_train()
    assert worker.has_capacity and not worker.train_admission_refused
    assert journal.snapshot()['attempts'] == 8
    assert sum(f.type is Message.BURST_CLOCK for f in channel.writes) == clock_count
    channel.fault = None
    worker.queue_data(b'fresh later turn', logical_id=b'w'*32)
    assert sum(f.type is Message.BURST_CLOCK for f in channel.writes) == clock_count + 1
    assert worker._burst_deadline <= worker._open.deadline_us


def test_partial_write_stays_unknown_and_cannot_retire_refusal(setup):
    worker, channel, journal, _ = setup(profile=SF5, train_depth=8, train24=True)
    channel.fault = 'partial_write'
    result = worker.queue_data(b'partial', logical_id=b'x'*32)
    assert result.outcome == 'UNKNOWN' and worker.fenced
    assert journal.snapshot()['attempts'] == 1 and not worker.has_capacity
    with pytest.raises(HardwareError):
        worker.retire_refused_train()


def test_new_policy_binds_peer_and_retains_original_flow_and_limits(composition):
    old = composition(train_depth=8, train12=True)
    new = composition(train_depth=8, train24=True)
    assert new.sender.burst_frames == 24 and old.sender.burst_frames == 12
    assert new.sender.flow.limits == old.sender.flow.limits
    assert new.receiver.burst_quiet_us == old.receiver.burst_quiet_us == 250_000
    assert new.receiver.burst_max_hold_us == old.receiver.burst_max_hold_us == 2_000_000
    assert new.authorities[0].operation_limits == old.authorities[0].operation_limits
    assert new.authorities[0].contract.limits == old.authorities[0].contract.limits
    packet = old.sender.cipher.seal(b'policy mismatch', FrameKind.DATA, authorize=lambda: True)
    with pytest.raises(WireError, match='authentication_failed'):
        new.receiver.cipher.open(packet, authorize=lambda: True)


@pytest.mark.parametrize('faults', [{}, {'drop_data':{3:1, 8:1, 15:1}},
    {'drop_data':{3:2, 8:1}, 'drop_ack_seqs':{3:1}}, {'drop_final_ack':True},
    {'host_work_us':(40_000, 10_000)}, {'host_step_work_us':(45_000, 10_000)}])
def test_modeled_train24_complete_paid_bounded(tmp_path, faults):
    def observe(index, endpoint, now):
        assert endpoint.burst_frames == 24 and endpoint.burst_sent < 24
        if index == 0:
            assert len(endpoint.worker.pending_physical) <= 8
            assert endpoint.worker._open.deadline_us <= endpoint.worker._last_clock.arm_end_us
    results, air = model.exercise(tmp_path, train_depth=8, train24=True, host_pump=True,
        receipt_confirmation=True, sender_cap=160, payload=b'x'*(48*193),
        observe=observe, **faults)
    model.assert_complete(results, air)
    assert air.channels[0].peak <= 8
    for result, channel in zip(results, air.channels):
        paid = result['snapshot']['attempt_journal']['attempts']
        # A native BUDGET refusal remains a charged host attempt, even though
        # it is correctly absent from the native accepted-attempt counter.
        assert paid >= channel.attempts
        assert dict(result['settled_states']).get('RF_DONE') == channel.attempts


def test_refused_partial_train_drains_keeps_logical_flights_and_full_reverse_hold(tmp_path):
    held = {}
    def observe(index, endpoint, now):
        if index or not endpoint.counts['dynamic_train_refused_turn']:
            return
        if not held:
            held.update(until=endpoint.next_send_us,
                attempts=endpoint.journal.snapshot()['attempts'],
                observed_at=now, original_deadline=endpoint.authority.application_deadline_us)
            assert endpoint.counts['dynamic_train_refused_turn'] == 1
            assert not endpoint.worker.pending_physical and not endpoint._physical_data
            assert not endpoint.worker.train_admission_refused
            assert endpoint.flow.in_flight() > 0
            assert endpoint.next_send_us - now == endpoint.sender_control_hold_us
        if now < held['until']:
            # Authenticated progress can update the logical flow but cannot
            # shorten the explicit refusal hold or spend a second attempt.
            assert endpoint.next_send_us >= held['until']
            assert endpoint.journal.snapshot()['attempts'] == held['attempts']
        assert endpoint.authority.application_deadline_us == held['original_deadline']
    results, air = model.exercise(tmp_path, train_depth=8, train24=True, host_pump=True,
        receipt_confirmation=True, sender_cap=160, payload=b'x'*(48*193),
        refuse_budget_at=9, observe=observe)
    model.assert_complete(results, air)
    assert held and air.channels[0].budget_refusal['pending'] > 0
    assert results[0]['snapshot']['counts']['dynamic_train_refused_turn'] == 1
    assert results[0]['snapshot']['attempt_journal']['attempts'] == air.channels[0].attempts + 1
    assert dict(results[0]['settled_states'])['REFUSED'] == 1
    events = results[0]['snapshot']['events']
    later = [e for e in events if e['event']=='tx_queued' and e['mono_us'] > held['observed_at']]
    assert later and min(e['mono_us'] for e in later) >= held['until']
