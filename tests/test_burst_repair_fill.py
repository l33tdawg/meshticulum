"""Explicit selective-repair plus fresh-DATA train; source-only radio model."""
import pytest

from meshticulum.bench.burst_protocol import (POLICY, POLICY8, POLICY32,
    POLICY_REPAIR_FILL, policy_for_depth, policy_for_flow_window,
    policy_for_repair_fill, policy_repair_fill, validate_policy)
from meshticulum.bench.tx_burst import BurstContractError
from meshticulum.bench.wire import FrameKind, WireError
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model


def test_repair_fill_is_exact_v4_and_all_previous_selectors_stay_unchanged():
    assert policy_for_depth() == POLICY
    assert policy_for_depth(8) == POLICY8
    assert policy_for_flow_window(32) == POLICY32
    assert policy_for_repair_fill() == POLICY_REPAIR_FILL
    assert all(not policy_repair_fill(p) for p in (POLICY, POLICY8, POLICY32))
    assert policy_repair_fill(POLICY_REPAIR_FILL) is True
    copied = policy_for_repair_fill()
    copied['selective_repair_fill'] = False
    assert policy_for_repair_fill()['selective_repair_fill'] is True
    with pytest.raises(BurstContractError):
        validate_policy(copied)


@pytest.mark.parametrize('mutation', [
    {'schema': 'meshticulum.data-burst-policy.v3'}, {'selective_repair_fill': 1},
    {'selective_repair_fill': 'true'}, {'selective_repair_fill': False},
    {'flow_window_frames': 8}, {'max_depth': 32}, {'train_frames': 32},
    {'reverse_listen_us': 0}, {'max_hold_us': 2_000_001}, {'extra': True},
])
def test_unreviewed_repair_fill_shapes_are_rejected(mutation):
    with pytest.raises(BurstContractError):
        validate_policy(dict(POLICY_REPAIR_FILL, **mutation))


def test_repair_fill_changes_original_peer_binding_not_caps_or_radio_bounds(composition):
    old = composition(train_depth=8, flow_window=32)
    new = composition(train_depth=8, repair_fill=True)
    assert old.authorities[0].grant.digest != new.authorities[0].grant.digest
    assert old.authorities[0].contract.limits == new.authorities[0].contract.limits
    assert old.authorities[0].deadlines == new.authorities[0].deadlines
    assert old.authorities[0].operation_limits == new.authorities[0].operation_limits
    assert old.sender.burst_frames == new.sender.burst_frames == 8
    assert old.sender.sender_control_hold_us == new.sender.sender_control_hold_us
    assert old.sender.flow.limits == new.sender.flow.limits
    assert old.workers[0]._depth == new.workers[0]._depth == 8
    assert not old.sender._selective_repair_fill and new.sender._selective_repair_fill
    packet = old.sender.cipher.seal(b'unchanged logical DATA', FrameKind.DATA, authorize=lambda: True)
    with pytest.raises(WireError, match='authentication_failed'):
        new.receiver.cipher.open(packet, authorize=lambda: True)
    assert not new.journals[0].snapshot()['attempts']


@pytest.mark.parametrize('fill', [False, True])
def test_repair_counts_as_first_of_eight_and_new_data_is_individually_charged(tmp_path, fill):
    payload = bytes(index % 251 for index in range(24*193))
    results, air = model.exercise(tmp_path, train_depth=8, flow_window=32,
        repair_fill=fill, host_pump=True, receipt_confirmation=True,
        sender_cap=40, payload=payload, drop_data={0: 1})
    model.assert_complete(results, air)
    assert not any(frame.collision for frame in air.frames)
    assert air.channels[0].peak <= 8
    data = [frame for frame in air.frames if 'seq' in frame.inner]
    assert len(data) == 25
    retry = [frame for frame in data if frame.inner['seq'] == 0][1]
    subsequent = [frame for frame in air.frames if frame.start >= retry.end]
    if fill:
        first_ack = next(frame for frame in subsequent if 'ack_seq' in frame.inner)
        following = [frame for frame in subsequent if 'seq' in frame.inner
                     and frame.end <= first_ack.start]
        assert [frame.inner['seq'] for frame in following] == list(range(8, 15))
        assert results[0]['snapshot']['counts']['selective_repair_fill_turn'] == 1
    else:
        assert 'ack_seq' in subsequent[0].inner
        assert not results[0]['snapshot']['counts'].get('selective_repair_fill_turn', 0)
    assert results[0]['snapshot']['attempt_journal']['attempts'] == 26  # DATA plus one CONTROL
    assert results[0]['snapshot']['retransmits'] == results[0]['snapshot']['fast_retransmits'] == 1


@pytest.mark.parametrize('faults,size', [
    ({'drop_data': {0: 1}}, 9*193),   # repair plus one full FINAL DATA
    ({'drop_data': {0: 1}}, 8*193),   # no outbound DATA: original single-repair hold
    ({'drop_data': {0: 2}}, 40*193),
    ({'drop_data': {0: 2, 8: 1}}, 40*193),
    ({'drop_data': {0: 2, 8: 1, 9: 1}}, 40*193),
    ({'drop_data': {0: 1, 2: 1, 5: 1}}, 40*193),
    ({'drop_data': {31: 1}}, 40*193),
    ({'drop_data': {39: 1}}, 40*193),
    ({'drop_data': {0: 1}, 'drop_ack_seqs': {0: 2}}, 40*193),
    ({'drop_data': {0: 1}, 'drop_ack_seqs': {15: 2}}, 40*193),
    ({'drop_final_ack': True}, 40*193),
])
def test_repair_fill_faults_keep_delivery_credit_nonce_and_physical_bounds(tmp_path, faults, size):
    payload = bytes(index % 251 for index in range(size))
    def observe(index, endpoint, now):
        assert endpoint.burst_frames == 8 and endpoint.burst_sent < 8
        assert endpoint.flow.in_flight() <= 32 and len(endpoint.flow._buffer) <= 31
    results, air = model.exercise(tmp_path, train_depth=8, flow_window=32,
        repair_fill=True, host_pump=True, receipt_confirmation=True,
        sender_cap=100, payload=payload, observe=observe, **faults)
    model.assert_complete(results, air)
    assert not any(frame.collision for frame in air.frames)
    assert air.channels[0].peak <= 8
    assert len({frame.packet for frame in air.frames}) == len(air.frames)
    for result, channel in zip(results, air.channels):
        journal = result['snapshot']['attempt_journal']
        assert journal['recovered_unknown'] == 0
        assert journal['attempts'] == channel.attempts
        assert result['settled_states'] == [('RF_DONE', journal['attempts'])]
    if size == 8*193:
        assert not results[0]['snapshot']['counts'].get('selective_repair_fill_turn', 0)


@pytest.mark.parametrize('costs', [(40_000, 10_000), (120_000, 40_000)])
def test_repair_fill_underfill_still_observes_empty_tail_floor(tmp_path, costs):
    results, air = model.exercise(tmp_path, train_depth=8, flow_window=32,
        repair_fill=True, host_pump=True, receipt_confirmation=True,
        sender_cap=40, payload=b'x'*(16*193), drop_data={0: 1}, host_work_us=costs)
    model.assert_complete(results, air)
    assert not any(frame.collision for frame in air.frames)
    data = [frame for frame in air.frames if 'seq' in frame.inner]
    assert any(second.start-first.end >= model.REVERSE_LISTEN_US
               for first, second in zip(data, data[1:]))


@pytest.mark.parametrize('held_new_data', [False, True])
def test_due_ordinary_retry_ends_fill_before_new_budget_nonce_or_attempt(composition, held_new_data):
    case = composition(b'x' * (40*193), train_depth=8, repair_fill=True)
    for endpoint in (case.sender, case.receiver):
        endpoint.enable_receipt_confirmation(b'c'*32,
            receiver_ready=None if endpoint.sends else lambda: False)
    case.sender.send(case.data)
    for _ in range(100):
        case.receiver.step(); case.sender.step()
        if len(case.workers[0].pending_physical) == 8:
            break
        case.clock.advance(1000)
    assert len(case.workers[0].pending_physical) == 8
    deliver = case.channels[0].deliver
    skipped = []
    def lose_first(packet):
        if not skipped:
            skipped.append(packet)
        else:
            deliver(packet)
    case.channels[0].deliver = lose_first
    case.channels[0].finish_packets()
    case.channels[0].deliver = deliver
    for _ in range(500):
        case.receiver.step(); case.sender.step()
        if case.sender._repair_fill_active:
            break
        case.clock.advance(1000)
    assert case.sender._repair_fill_active and case.sender.flow.fast_retransmits == 1
    assert case.sender.burst_sent == 1
    if held_new_data:
        case.sender.link.defer_to_host = True
        case.sender.step()
        case.sender.link.defer_to_host = False
        assert case.sender.link._held is not None
    held = case.sender.link._held
    # Finish the exact queued repair, but leave its peer ACK undelivered. Its
    # ordinary retry becomes due through the real clock, without editing RTO.
    case.channels[0].finish_packets()
    case.sender._poll_physical()
    case.sender.update_counter()
    pending = case.sender.flow._in_flight[0]
    due_us = round((pending.sent_at + pending.rto) * 1_000_000) + 1
    case.clock.advance(due_us - case.clock()[1])
    before = (case.journals[0].snapshot(), case.sender.owner.rf_attempts,
        len(case.sender.owner.retained), case.stores[0]._db.execute('SELECT tx FROM state').fetchone(),
        pending.attempts, case.sender.ledger.snapshot(case.clock()[1]))
    case.sender.step()
    after = (case.journals[0].snapshot(), case.sender.owner.rf_attempts,
        len(case.sender.owner.retained), case.stores[0]._db.execute('SELECT tx FROM state').fetchone(),
        pending.attempts, case.sender.ledger.snapshot(case.clock()[1]))
    assert before[:5] == after[:5]
    for field in ('debt_own_us', 'debt_shared_us', 'pending_reservations'):
        assert before[5][field] == after[5][field]
    assert case.sender.link._held is held
    assert not case.sender._repair_fill_active and case.sender.owner.no_send
    assert case.sender.next_send_us > case.clock()[1]
    assert case.sender.counts['selective_repair_fill_timeout_hold'] == 1
    assert case.sender.flow.fast_retry_offers == 1
