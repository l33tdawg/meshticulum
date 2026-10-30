"""Explicit v7 admission and real owner/cipher/journal source composition."""
from dataclasses import replace

import pytest

from meshticulum.bench.burst_protocol import (
    POLICY, POLICY8, POLICY32, POLICY_REPAIR_FILL, POLICY_TERMINAL_RETRY,
    POLICY_SCALAR_FILL_PAIR, POLICY_REPEAT_RETRY, policy_for_repeat_retry,
    policy_repeat_retry, validate_policy,
)
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.tx_burst import BurstContractError
from meshticulum.bench.wire import FrameKind, WireError
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model


def test_policy_is_exact_v7_and_previous_policies_never_enable_it():
    previous = (POLICY, POLICY8, POLICY32, POLICY_REPAIR_FILL,
                POLICY_TERMINAL_RETRY, POLICY_SCALAR_FILL_PAIR)
    assert policy_for_repeat_retry() == POLICY_REPEAT_RETRY
    assert all(not policy_repeat_retry(policy) for policy in previous)
    assert policy_repeat_retry(POLICY_REPEAT_RETRY)
    assert dict(POLICY_REPEAT_RETRY, schema=POLICY_SCALAR_FILL_PAIR['schema'],
                selective_repeat_retry=False) != POLICY_SCALAR_FILL_PAIR
    copied = policy_for_repeat_retry()
    copied['selective_repeat_retry'] = False
    assert policy_for_repeat_retry()['selective_repeat_retry'] is True
    with pytest.raises(BurstContractError):
        validate_policy(copied)


@pytest.mark.parametrize('change', [
    {'selective_repeat_retry': 1}, {'selective_repeat_retry': 'true'},
    {'selective_repeat_retry': None}, {'selective_repeat_retry': False},
    {'schema': 'meshticulum.data-burst-policy.v6'}, {'scalar_fill_pair': False},
    {'selective_terminal_retry': False}, {'selective_repair_fill': False},
    {'max_depth': 4}, {'train_frames': 4}, {'flow_window_frames': 8},
    {'max_hold_us': 2_000_001}, {'reverse_listen_us': 0}, {'extra': True},
])
def test_policy_rejects_partial_opt_in_and_unreviewed_bounds(change):
    with pytest.raises(BurstContractError):
        validate_policy(dict(POLICY_REPEAT_RETRY, **change))


@pytest.mark.parametrize('direction', [False, True])
def test_real_cipher_binding_refuses_other_policy_without_charging(composition, direction):
    old = composition(train_depth=8, scalar_fill_pair=True)
    new = composition(train_depth=8, repeat_retry=True)
    assert old.authorities[0].grant.digest != new.authorities[0].grant.digest
    for attr in ('deadlines', 'operation_limits'):
        assert getattr(old.authorities[0], attr) == getattr(new.authorities[0], attr)
    assert old.authorities[0].contract.limits == new.authorities[0].contract.limits
    assert replace(new.sender.flow.limits, selective_repeat_retry=False) == old.sender.flow.limits
    assert new.sender.burst_frames == old.sender.burst_frames == 8
    assert new.sender.flow.limits.window_frames == 32
    assert new.sender.sender_control_hold_us == old.sender.sender_control_hold_us
    assert new.workers[0]._depth == old.workers[0]._depth == 8
    assert not new.receiver.flow.limits.selective_repeat_retry
    origin, target = (new, old) if direction else (old, new)
    packet = origin.sender.cipher.seal(b'same DATA', FrameKind.DATA, authorize=lambda: True)
    with pytest.raises(WireError, match='authentication_failed'):
        target.receiver.cipher.open(packet, authorize=lambda: True)
    assert all(j.snapshot()['attempts'] == 0 for c in (old, new) for j in c.journals)
    assert 'selective_repeat_retry' not in old.sender.snapshot()['timing_policy']
    assert 'repeat_retry_epochs' not in old.sender.snapshot()
    assert new.sender.snapshot()['timing_policy']['selective_repeat_retry'] is True


def test_policy_requires_original_sf5_native_admission(composition, monkeypatch):
    case = composition(train_depth=8, repeat_retry=True)
    authority = case.authorities[0]
    monkeypatch.setattr(authority, 'profile', replace(authority.profile, spreading_factor=7, preamble=8))
    with pytest.raises(WireError, match='burst_eight_requires_sf5'):
        BenchFlow(authority=authority, worker=case.workers[0], cipher=case.sender.cipher,
            journal=case.journals[0], flow_key=authority.flow_key, contract=authority.contract,
            profile=authority.carrier_profile, ledger=case.sender.ledger,
            scheduler_epoch=authority.scheduler_epoch, broker_epoch=authority.broker_epoch,
            sends=True, clock=case.clock)
    assert case.journals[0].snapshot()['attempts'] == 0


@pytest.mark.parametrize('faults', [
    {}, {'drop_data': {0: 2}}, {'drop_data': {0: 3, 2: 1}},
    {'drop_data': {10: 2, 18: 2}}, {'drop_data': {37: 2, 38: 1}},
    {'drop_data': {39: 1}}, {'drop_final_ack': True},
    {'drop_data': {0: 2}, 'drop_ack_seqs': {0: 2}},
    {'drop_data': {0: 2}, 'host_step_work_us': (18_000, 3_000)},
])
def test_source_fault_model_keeps_delivery_nonce_debt_and_native_bounds(tmp_path, faults):
    previous = {}
    def observe(index, endpoint, now):
        assert endpoint.burst_sent < 8 and endpoint.flow.in_flight() <= 32
        assert len(endpoint.flow._buffer) < 32
        count = endpoint.counts['tx_QUEUED']
        assert count - previous.get(index, 0) <= 2
        previous[index] = count
        for pending in endpoint.flow._in_flight.values():
            assert len(pending.selective_evidence) <= 3
    results, air = model.exercise(tmp_path, train_depth=8, repeat_retry=True,
        host_pump=True, receipt_confirmation=True, sender_cap=64, receiver_cap=64,
        payload=b'x'*(40*193), observe=observe, **faults)
    model.assert_complete(results, air)
    assert air.channels[0].peak <= 8
    assert len({frame.packet for frame in air.frames}) == len(air.frames)
    assert not any(frame.collision for frame in air.frames)
    for result, channel in zip(results, air.channels):
        journal = result['snapshot']['attempt_journal']
        assert journal['attempts'] == channel.attempts
        assert journal['recovered_unknown'] == 0
        assert result['settled_states'] == [('RF_DONE', journal['attempts'])]
    assert results[0]['snapshot']['repeat_retry_epochs'] == results[0]['snapshot']['retransmits']


def test_lost_repair_model_requires_new_receipts_and_preserves_attempt_count(tmp_path):
    rows = []
    for enabled in (False, True):
        results, air = model.exercise(tmp_path / str(enabled), train_depth=8,
            scalar_fill_pair=not enabled, repeat_retry=enabled, host_pump=True,
            receipt_confirmation=True, sender_cap=64, payload=b'x'*(40*193),
            drop_data={0: 2})
        model.assert_complete(results, air)
        rows.append((results[0]['snapshot'], air.timeline.now))
    assert rows[0][0]['retransmits'] == rows[1][0]['retransmits'] == 2
    assert rows[0][0]['fast_retransmits'] == 1
    assert rows[1][0]['fast_retransmits'] == 2
    assert rows[1][1] < rows[0][1]
