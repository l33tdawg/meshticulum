"""Admitted terminal policy over real owner/cipher/journal and modeled radio."""
from dataclasses import replace

import pytest

from meshticulum.bench.burst_protocol import (POLICY,POLICY8,POLICY32,POLICY_REPAIR_FILL,
    POLICY_TERMINAL_RETRY,policy_for_terminal_retry,policy_terminal_retry,validate_policy)
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.tx_burst import BurstContractError, Stage
from meshticulum.bench.wire import FrameKind,WireError
from meshticulum.bench.linux_hat import HardwareError
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model


def test_policy_is_exact_v5_and_old_policies_never_enable_it():
    assert policy_for_terminal_retry()==POLICY_TERMINAL_RETRY
    assert all(not policy_terminal_retry(p) for p in (POLICY,POLICY8,POLICY32,POLICY_REPAIR_FILL))
    assert policy_terminal_retry(POLICY_TERMINAL_RETRY)
    copy=policy_for_terminal_retry();copy['selective_terminal_retry']=False
    assert policy_for_terminal_retry()['selective_terminal_retry'] is True
    with pytest.raises(BurstContractError):validate_policy(copy)


@pytest.mark.parametrize('change',[{'selective_terminal_retry':1},{'selective_terminal_retry':'true'},
    {'selective_terminal_retry':None},{'schema':'meshticulum.data-burst-policy.v4'},
    {'selective_repair_fill':False},{'max_depth':4},{'flow_window_frames':8},
    {'reverse_listen_us':0},{'max_hold_us':2_000_001},{'extra':True}])
def test_terminal_policy_does_not_widen_other_bounds(change):
    with pytest.raises(BurstContractError):validate_policy(dict(POLICY_TERMINAL_RETRY,**change))


def test_policy_changes_peer_binding_but_not_caps_deadlines_or_native_depth(composition):
    old=composition(train_depth=8,repair_fill=True)
    new=composition(train_depth=8,terminal_retry=True)
    assert old.authorities[0].grant.digest!=new.authorities[0].grant.digest
    assert old.authorities[0].contract.limits==new.authorities[0].contract.limits
    assert old.authorities[0].deadlines==new.authorities[0].deadlines
    assert old.authorities[0].operation_limits==new.authorities[0].operation_limits
    assert old.sender.burst_frames==new.sender.burst_frames==8
    assert old.sender.flow.limits.window_frames==new.sender.flow.limits.window_frames==32
    assert old.sender.sender_control_hold_us==new.sender.sender_control_hold_us
    assert not old.sender.flow.limits.selective_terminal_retry and new.sender.flow.limits.selective_terminal_retry
    sealed=old.sender.cipher.seal(b'same DATA',FrameKind.DATA,authorize=lambda:True)
    with pytest.raises(WireError,match='authentication_failed'):
        new.receiver.cipher.open(sealed,authorize=lambda:True)
    assert new.journals[0].snapshot()['attempts']==0


def test_terminal_policy_still_requires_sf5_before_new_flow_dispatch(composition,monkeypatch):
    case=composition(train_depth=8,terminal_retry=True)
    authority=case.authorities[0]
    monkeypatch.setattr(authority,'profile',replace(authority.profile,spreading_factor=7,preamble=8))
    with pytest.raises(WireError,match='burst_eight_requires_sf5'):
        BenchFlow(authority=authority,worker=case.workers[0],cipher=case.sender.cipher,
            journal=case.journals[0],flow_key=authority.flow_key,contract=authority.contract,
            profile=authority.carrier_profile,ledger=case.sender.ledger,
            scheduler_epoch=authority.scheduler_epoch,broker_epoch=authority.broker_epoch,
            sends=True,clock=case.clock)
    assert case.journals[0].snapshot()['attempts']==0


def tail_case(composition, *, terminal=True, withhold=False):
    case=composition(b'x'*(8*193),train_depth=8,repair_fill=not terminal,terminal_retry=terminal)
    for endpoint in (case.sender,case.receiver):
        endpoint.enable_receipt_confirmation(b'c'*32,
            receiver_ready=None if endpoint.sends else lambda:False)
    case.sender.send(case.data)
    for _ in range(100):
        case.receiver.step();case.sender.step()
        if len(case.workers[0].pending_physical)==8:break
        case.clock.advance(1000)
    assert len(case.workers[0].pending_physical)==8
    original=case.channels[0].deliver;count=0
    def drop_tail(packet):
        nonlocal count
        if count not in (3,4,6):original(packet)
        count+=1
    case.channels[0].deliver=drop_tail
    case.channels[0].finish_packets(withhold=withhold)
    case.channels[0].deliver=original
    for _ in range(400):
        case.receiver.step();case.sender.step()
        if case.sender.flow._peer_original_final_received:break
        case.clock.advance(1000)
    assert case.sender.flow._peer_original_final_received
    assert {seq:len(p.selective_evidence) for seq,p in case.sender.flow._in_flight.items()}=={3:2,4:2,6:1}
    return case


@pytest.mark.parametrize('terminal',[False,True])
def test_real_composition_uses_terminal_permission_only_when_admitted(composition,terminal):
    case=tail_case(composition,terminal=terminal)
    for _ in range(5):case.sender.step();case.clock.advance(1000)
    assert case.sender.flow.fast_retransmits==int(terminal)
    assert case.journals[0].snapshot()['attempts']==8+int(terminal)
    assert case.sender.flow._peer_credit==30


def test_final_peer_receipt_does_not_replace_exact_physical_release(composition):
    case=tail_case(composition,withhold=True)
    assert case.sender.flow.selective_retry_ready(terminal=True)
    for _ in range(5):case.sender.step();case.clock.advance(1000)
    assert case.sender.flow.fast_retry_offers==0 and case.journals[0].snapshot()['attempts']==8
    case.channels[0].release_held();case.sender.step()
    assert case.sender.flow.fast_retransmits==1 and case.journals[0].snapshot()['attempts']==9


def test_unknown_physical_result_cannot_become_terminal_retry(composition):
    case=tail_case(composition,withhold=True)
    case.channels[0].release_held(Stage.UNKNOWN)
    with pytest.raises((WireError,HardwareError)):case.sender.step()
    assert case.sender.flow.fast_retry_offers==0
    assert case.journals[0].snapshot()['fenced']
    assert case.journals[0].snapshot()['attempts']==8


@pytest.mark.parametrize('faults',[
    {'drop_data':{3:1,4:1,6:1}},
    {'drop_data':{3:2,4:1,6:1}},
    {'drop_data':{7:1}},
    {'drop_data':{3:1,4:1,6:1},'drop_ack_seqs':{3:2}},
    {'drop_final_ack':True},
])
def test_terminal_fault_models_keep_delivery_nonce_debt_and_native_bounds(tmp_path,faults):
    results,air=model.exercise(tmp_path,train_depth=8,terminal_retry=True,
        host_pump=True,receipt_confirmation=True,sender_cap=32,payload=b'x'*(8*193),**faults)
    model.assert_complete(results,air)
    assert not any(frame.collision for frame in air.frames)
    assert air.channels[0].peak<=8
    assert len({frame.packet for frame in air.frames})==len(air.frames)
    for result,channel in zip(results,air.channels):
        journal=result['snapshot']['attempt_journal']
        assert journal['attempts']==channel.attempts and journal['recovered_unknown']==0
        assert result['settled_states']==[('RF_DONE',journal['attempts'])]


def test_wrong_boot_on_required_drained_status_prevents_terminal_offer(composition,monkeypatch):
    from meshticulum.bench.serial_protocol import Message
    case=tail_case(composition,withhold=True)
    channel=case.channels[0]
    original_offer=channel.offer
    def wrong_status(frame):
        if frame.type is Message.BURST_STATUS_REPLY:
            frame=replace(frame,boot=b'3'*16)
        original_offer(frame)
    monkeypatch.setattr(channel,'offer',wrong_status)
    case.channels[0].release_held()
    with pytest.raises(HardwareError,match='boot_changed'):case.sender.step()
    assert case.sender.flow.fast_retry_offers==0 and case.journals[0].snapshot()['attempts']==8
