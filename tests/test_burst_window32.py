"""Logical-32/physical-eight admission and bounded source composition.

These controls use modeled carriers, never devices or measured RF goodput.
The accepted frozen flow and port are exercised without changing their source.
"""
from dataclasses import replace
import hashlib
import json

import pytest

from meshticulum.bench.authority import RunAuthority, revoke
from meshticulum.bench.burst_protocol import (
    CAPABILITY8, POLICY, POLICY8, POLICY32, capability_for_depth,
    capability_request_body, policy_depth, policy_flow_window,
    policy_for_depth, policy_for_flow_window, validate_policy,
)
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.serial_protocol import Message
from meshticulum.bench.tx_burst import BurstContractError
from meshticulum.bench.wire import FrameKind, WireError
from meshticulum.flow.flow import (
    FlowLimits, ReliableFlow, StartupGrant, build_data_frame, parse_frame,
)
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model
from tests.test_flow_selective_fast_retry import Link, ack
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision


def test_new_policy_is_explicit_and_keeps_old_defaults_and_native_bytes():
    assert policy_for_depth() == POLICY and policy_for_depth(8) == POLICY8
    assert [policy_flow_window(p) for p in (POLICY, POLICY8, POLICY32)] == [4, 8, 32]
    assert policy_for_flow_window(32) == POLICY32
    assert policy_depth(POLICY32) == 8
    assert capability_for_depth(policy_depth(POLICY32)) == CAPABILITY8
    assert capability_request_body(policy_depth(POLICY32)) == b'\x08'
    copied = policy_for_flow_window(32)
    copied['flow_window_frames'] = 8
    assert policy_for_flow_window(32)['flow_window_frames'] == 32
    with pytest.raises(BurstContractError):
        validate_policy(copied)


@pytest.mark.parametrize('value', [True, 32.0, None, 0, 4, 8, 16, 31, 33, 64])
def test_window_selector_does_not_infer_other_policies(value):
    with pytest.raises(BurstContractError):
        policy_for_flow_window(value)


@pytest.mark.parametrize('mutation', [
    {'flow_window_frames': True}, {'flow_window_frames': 32.0},
    {'flow_window_frames': 31}, {'flow_window_frames': 33},
    {'max_depth': 32}, {'train_frames': 32}, {'train_frames': 4},
    {'schema': 'meshticulum.data-burst-policy.v2'},
    {'max_hold_us': 2_000_001}, {'inter_packet_rx_us': 0},
    {'reverse_listen_us': 0}, {'extra': 'authority'},
])
def test_malformed_window_policy_is_refused_before_single_use_claim(tmp_path, mutation):
    clock = Clock()
    path = tmp_path / 'unit'
    plan = provision(path, 'unit-a', b'bytes', clock, model.PROFILE)
    plan['data_burst'] = dict(POLICY32, **mutation)
    (path / 'grant.json').write_text(json.dumps(plan))
    with pytest.raises(BurstContractError):
        RunAuthority(path, expected_unit='unit-a', host_boot_id=BOOT,
            clock=clock, owner_probe=lambda: True, radio_admission=model.ADMISSION)
    assert not (path / 'started').exists()


def test_original_window_admission_changes_shared_binding_but_not_operation_caps(tmp_path):
    clock = Clock()
    opened = []
    limits = {'sender': {'attempts': 160, 'estimated_airtime_us': 8_000_000},
              'receiver': {'attempts': 64, 'estimated_airtime_us': 3_000_000}}
    try:
        for name, policy in [('eight', POLICY8), ('logical32', POLICY32)]:
            path = tmp_path / name
            plan = provision(path, 'unit-a', b'bytes', clock, model.PROFILE)
            plan.update(data_burst=dict(policy), operation_limits=limits)
            (path / 'grant.json').write_text(json.dumps(plan))
            opened.append(RunAuthority(path, expected_unit='unit-a', host_boot_id=BOOT,
                clock=clock, owner_probe=lambda: True, radio_admission=model.ADMISSION))
        eight, wider = opened
        assert eight.grant.digest != wider.grant.digest
        assert eight.grant.route_binding_digest != wider.grant.route_binding_digest
        assert eight.operation_limits == wider.operation_limits
        assert eight.deadlines == wider.deadlines
        assert eight.grant.max_frames == wider.grant.max_frames
        assert wider.contract.limits.window_bytes == 6656
        assert wider.contract.limits.max_reassembly_frames == 32
        assert wider.contract.limits.max_payload_bytes == 208
        assert wider.contract.limits.max_retry_attempts == eight.contract.limits.max_retry_attempts
        assert wider.contract.limits.max_total_frames == eight.contract.limits.max_total_frames
        assert wider.contract.limits.initial_credit_bytes == 0
        copied = wider.data_burst_policy
        copied['flow_window_frames'] = 8
        assert wider.data_burst_policy == POLICY32 and wider.live()
        wider.plan['data_burst'] = dict(POLICY8)
        assert not wider.live()
    finally:
        for authority in opened:
            authority.close()


def compose_again(case, contract):
    original = case.sender
    return BenchFlow(authority=original.authority, worker=original.worker,
        cipher=original.cipher, journal=original.journal, flow_key=original.authority.flow_key,
        contract=contract, profile=original.authority.carrier_profile, ledger=original.ledger,
        scheduler_epoch=original.authority.scheduler_epoch,
        broker_epoch=original.authority.broker_epoch, sends=True, clock=case.clock)


@pytest.mark.parametrize('change', ['copied_contract', 'smaller_window', 'smaller_reassembly', 'worker_policy'])
def test_v3_requires_original_contract_and_exact_worker_policy(composition, change):
    case = composition(train_depth=8, flow_window=32)
    original = case.authorities[0].contract
    contract = replace(original)
    if change == 'smaller_window':
        contract = replace(original, limits=replace(original.limits, window_bytes=1664))
    elif change == 'smaller_reassembly':
        contract = replace(original, limits=replace(original.limits,
                           window_bytes=1664, max_reassembly_frames=8))
    elif change == 'worker_policy':
        contract = original
        case.workers[0]._burst_policy = tuple(sorted(POLICY8.items()))
    with pytest.raises(WireError, match='contract_mismatch|missing_admitted_burst_worker'):
        compose_again(case, contract)
    assert not case.journals[0].snapshot()['attempts']
    assert not any(f.type is Message.BURST_DATA for f in case.channels[0].writes)


def test_logical32_still_yields_after_eight_accepted_physical_frames(composition):
    case = composition(b'x' * 9000, train_depth=8, flow_window=32)
    assert case.sender.flow.limits.window_frames == 32
    assert case.receiver.flow.limits.max_reassembly_frames == 32
    assert case.sender.owner.grant_ceiling_bytes() == 6656
    assert case.sender.burst_frames == case.receiver.burst_frames == 8
    assert case.receiver.burst_max_hold_us == 2_000_000
    assert case.sender.sender_control_hold_us == 1_000_000
    case.sender.send(case.data)
    for _ in range(100):
        case.receiver.step()
        case.sender.step()
        if len(case.workers[0].pending_physical) == 8:
            break
        case.clock.advance(1000)
    assert len(case.workers[0].pending_physical) == 8
    assert case.sender.flow._next_seq == 8 and not case.workers[0].has_capacity
    before = case.journals[0].snapshot()
    case.sender.step()
    assert case.journals[0].snapshot() == before
    case.channels[0].finish_packets()
    case.sender.step()  # No peer ACK: logical32 must not erase the reverse turn.
    assert not case.workers[0].pending_physical
    assert case.sender.flow._next_seq == 8 and case.sender.next_send_us > case.clock()[1]
    for _ in range(8):
        case.receiver.step()
    assert case.receiver.burst_received == 0
    acks = [e for e in case.receiver.events if e.get('ack_wait_reason') == 'full_train']
    assert len(acks) == 1
    assert case.receiver.counts['tx_RF_DONE'] == 2  # startup plus the eight-frame receipt


def test_uncertain_eighth_attempt_fences_the_same_eight_slots_and_retains_debt(composition):
    case = composition(train_depth=8, flow_window=32)
    worker, channel, journal = case.workers[0], case.channels[0], case.journals[0]
    for index in range(7):
        worker.queue_data(bytes([index]) * 255, logical_id=bytes([index+1]) * 32)
    before = journal.snapshot()['reserved_estimated_airtime_us']
    channel.fault = 'partial_write'
    assert worker.queue_data(b'eighth', logical_id=b'e' * 32).outcome == 'UNKNOWN'
    assert worker.fenced and len(worker.pending_physical) == 8 and not worker.has_capacity
    assert journal.snapshot()['attempts'] == 8
    assert journal.snapshot()['reserved_estimated_airtime_us'] > before
    assert all(row['state'] == 'UNKNOWN' and not row['released'] for row in journal.queued_records())


def test_v2_peer_cannot_authenticate_v3_and_retries_keep_fresh_outer_nonces(composition):
    eight = composition(train_depth=8)
    wider = composition(train_depth=8, flow_window=32)
    packet = eight.sender.cipher.seal(b'same logical DATA', FrameKind.DATA, authorize=lambda: True)
    with pytest.raises(WireError, match='authentication_failed'):
        wider.receiver.cipher.open(packet, authorize=lambda: True)
    assert wider.stores[1]._db.execute('SELECT high FROM state').fetchone() == (0,)
    packets = [wider.sender.cipher.seal(b'same logical DATA', FrameKind.DATA,
               authorize=lambda: True) for _ in range(2)]
    assert len(set([packet, *packets])) == 3
    assert [wider.receiver.cipher.open(p, authorize=lambda: True).counter for p in packets] == [1, 2]
    with pytest.raises(WireError, match='replayed_or_too_old'):
        wider.receiver.cipher.open(packets[0], authorize=lambda: True)
    assert not wider.journals[0].snapshot()['attempts']  # crypto alone grants no radio work


@pytest.mark.parametrize('fault', ['expiry', 'revoked'])
def test_wider_window_does_not_reopen_expired_or_revoked_authority(composition, fault):
    case = composition(train_depth=8, flow_window=32)
    original_deadlines = case.authorities[0].deadlines
    case.sender.send(case.data)
    if fault == 'expiry':
        case.clock.advance(181_000_000)
    else:
        revoke(case.authorities[0].path)
    with pytest.raises(WireError, match='bench_authority_not_live'):
        case.sender.step()
    assert not case.journals[0].snapshot()['attempts']
    assert case.authorities[0].deadlines is original_deadlines


def logical_limits():
    return FlowLimits(frame_payload_bytes=193, window_frames=32,
        initial_credit_frames=32, max_reassembly_frames=32, max_total_frames=128,
        max_queued_bytes=32768, max_delivered_bytes=32768,
        selective_fast_retry=True)


def test_edge31_is_buffered_edge32_is_refused_and_hole_fill_delivers_in_order():
    limits = logical_limits()
    receiver = ReliableFlow('b', Link(), limits, sends=False, receive=True, startup=None)
    for seq in range(1, 33):
        raw = build_data_frame(flow_id=1, generation=1, seq=seq, total=40,
            last=False, payload=bytes([seq])*193, limits=limits)
        receiver.absorb(parse_frame(raw), seq / 1000)
    assert len(receiver._buffer) == 31 and receiver._credit() == 1
    assert receiver._sack_bitmap() == 0xfffffffe and receiver.out_of_window == 1
    assert not receiver.delivered
    raw = build_data_frame(flow_id=1, generation=1, seq=0, total=40,
        last=False, payload=b'\x00'*193, limits=limits)
    receiver.absorb(parse_frame(raw), .04)
    assert not receiver._buffer
    assert receiver.delivered == b''.join(bytes([seq])*193 for seq in range(32))


def test_seven_sacks_allow_24_new_frames_but_31_sacks_leave_only_the_hole():
    limits, link = logical_limits(), Link()
    sender = ReliableFlow('a', link, limits, startup=StartupGrant(32))
    sender.send(b'x' * (40*193))
    sender.pump(0, max_frames=8, drain=False)
    ack(sender, positions=tuple(range(1, 8)), credit=25, at=.01)
    sender.pump(.02, max_frames=24, drain=False)
    assert sender._next_seq == 32 and len(link.frames) == 32
    ack(sender, positions=tuple(range(1, 32)), credit=1, revision=3, at=.03)
    sender.pump(.04, max_frames=8, drain=False)
    assert sender._next_seq == 32 and set(sender._in_flight) == {0}
    assert sender.queued_bytes() == 8*193


@pytest.mark.parametrize('faults', [
    {}, {'drop_data': {0: 2}}, {'drop_data': {0: 2, 2: 1}},
    {'drop_data': {31: 1}}, {'drop_data': {32: 1}},
    {'drop_ack_seqs': {0: 2}}, {'drop_ack_seqs': {8: 1}},
    {'drop_final_ack': True},
], ids=['clean', 'lost-hole-and-fast-repair', 'two-holes', 'edge31', 'edge32',
        'startup-and-first-sack', 'train-ack', 'final-ack'])
def test_independent_hosts_keep_eight_frame_air_turns_with_32_credit(tmp_path, faults):
    payload = bytes(index % 251 for index in range(40*193+1))
    peaks = [0, 0]
    def observe(index, endpoint, now):
        peaks[index] = max(peaks[index], endpoint.flow.in_flight() if index == 0
                           else len(endpoint.flow._buffer))
        assert endpoint.burst_frames == 8
        assert endpoint.flow.in_flight() <= 32 and len(endpoint.flow._buffer) <= 31
    results, air = model.exercise(tmp_path, train_depth=8, flow_window=32,
        host_pump=True, receipt_confirmation=True, sender_cap=100, receiver_cap=64,
        payload=payload, observe=observe, **faults)
    model.assert_complete(results, air)
    assert not any(frame.collision for frame in air.frames)
    assert air.channels[0].peak <= 8
    assert all(result['snapshot']['timing_policy']['flow_window_frames'] == 32 for result in results)
    for result, channel in zip(results, air.channels):
        journal = result['snapshot']['attempt_journal']
        assert journal['recovered_unknown'] == 0
        assert channel.attempts == journal['attempts']
        assert result['settled_states'] == [('RF_DONE', journal['attempts'])]
    packets = [frame.packet for frame in air.frames]
    assert len(set(packets)) == len(packets)  # fresh outer counter on every DATA/ACK retry
    assert hashlib.sha256(results[1]['received']).digest() == hashlib.sha256(payload).digest()
    if faults.get('drop_data', {}).get(0) == 2:
        assert peaks[1] > 8  # actual bounded lookahead, not only a larger advertised number


@pytest.mark.parametrize('costs', [(40_000, 10_000), (120_000, 40_000)])
def test_wider_credit_does_not_skip_real_empty_tail_listen_floor(tmp_path, costs):
    results, air = model.exercise(tmp_path, train_depth=8, flow_window=32,
        host_work_us=costs, host_pump=True, receipt_confirmation=True)
    model.assert_complete(results, air)
    assert not any(frame.collision for frame in air.frames)
    data = [frame for frame in air.frames if 'seq' in frame.inner]
    assert len(data) == 9
    assert any(second.start - first.end >= model.REVERSE_LISTEN_US
               for first, second in zip(data[:7], data[1:8]))


def test_small_original_sender_attempt_cap_still_stops_wider_window(tmp_path):
    payload = b'x' * (40*193)
    results, air = model.exercise(tmp_path, train_depth=8, flow_window=32,
        host_pump=True, receipt_confirmation=True, sender_cap=10, payload=payload)
    assert not results[0]['complete'] and 'error' in results[0]
    assert results[0]['snapshot']['attempt_journal']['attempts'] == 10
    assert air.channels[0].attempts <= 10
    assert len([f for f in air.frames if 'seq' in f.inner]) <= 10
    assert all(not channel.armed for channel in air.channels)
    assert not air.channels[0].events

