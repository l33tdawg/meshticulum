"""Real admitted route, crypto, frozen flow and native-model marker composition."""
import pytest
from tests.test_burst_runtime import composition
from meshticulum.bench.tx_burst import Stage
from meshticulum.bench.wire import HEADER,FrameKind,WireError
from meshticulum.bench.linux_hat import HardwareError
from meshticulum.flow.flow import build_data_frame,build_ack_frame

def finish_one(channel):
    for request,packet in tuple(channel.packets.items()):
        if request in channel.delivered:continue
        channel.event(request,Stage.STARTED)
        channel.clock.advance(channel.events[request][4]+5000)
        channel.deliver(packet);channel.delivered.add(request)
        channel.event(request,Stage.RF_DONE)
        return

def run(case):
    case.sender.send(case.data)
    for _ in range(1000):
        case.receiver.step();case.sender.step()
        finish_one(case.channels[0])
        case.clock.advance(5000)
        if case.sender.flow.complete and case.receiver.flow.complete:
            for _ in range(3):case.sender.step();case.receiver.step()
            return
    raise AssertionError('composition did not complete')

@pytest.mark.parametrize('size',[100,386,1000,6500,8192])
def test_encrypted_complete_object_and_marker_coverage(composition,size):
    case=composition(b'x'*size,train_depth=8,train32=True,turn_end=True)
    case.receiver.set_receiver_ack_quiet_us(150000)
    run(case)
    assert bytes(case.receiver.received)==case.data
    assert case.sender.flow.complete and case.receiver.flow.complete
    assert not case.workers[0].fenced
    if size==100:
        assert case.sender.counts['tx_DATA_turn_end']==0
    else:
        assert case.sender.counts['tx_DATA_turn_end']>0
        assert case.receiver.counts['rx_DATA_turn_end']>0
    assert case.journals[0].snapshot()['attempts']>=case.sender.counts['tx_DATA_turn_end']

@pytest.mark.parametrize('loss',['first','middle','marked_last','progress_ack'])
def test_real_pair_loss_uses_original_paid_retry_and_quiet_fallback(composition,loss):
    case=composition(b'x'*8192,train_depth=8,train32=True,turn_end=True)
    case.receiver.set_receiver_ack_quiet_us(150000)
    channel=case.channels[1 if loss=='progress_ack' else 0]
    original=channel.deliver;seen=[];dropped=[]
    def deliver(packet):
        kind=HEADER.unpack(packet[:HEADER.size])[2];seen.append(kind)
        chosen=(loss=='first' and len(seen)==1 or loss=='middle' and len(seen)==3
                or loss=='marked_last' and kind==int(FrameKind.DATA_TURN_END)
                or loss=='progress_ack' and len(seen)==2)
        if chosen and not dropped:dropped.append(packet);return
        original(packet)
    channel.deliver=deliver
    original_deadlines=[a.deadlines for a in case.authorities]
    run(case)
    assert dropped and bytes(case.receiver.received)==case.data
    assert all(a.deadlines==d for a,d in zip(case.authorities,original_deadlines))
    assert not any(w.fenced for w in case.workers)
    assert case.journals[0].snapshot()['attempts']>=43
    assert case.sender.flow.complete

def inner(case,seq,*,flow_id=None,generation=None):
    f=case.sender.flow
    size=f.limits.frame_payload_bytes
    return build_data_frame(flow_id=f.flow_id if flow_id is None else flow_id,
        generation=f.generation if generation is None else generation,seq=seq,
        total=f._total,last=False,payload=case.data[seq*size:(seq+1)*size],
        modulus=f.seq_modulus,limits=f.limits)

@pytest.mark.parametrize('malformed',['invalid_bytes','wrong_flow','wrong_generation'])
def test_authenticated_invalid_inner_marker_cannot_release_ack(malformed,composition):
    case=composition(b'x'*1000,train_depth=8,train32=True,turn_end=True)
    case.sender.send(case.data);case.receiver.step()
    before=case.receiver.flow._ack_work_gen
    payload=(b'not an inner frame' if malformed=='invalid_bytes' else
        inner(case,0,flow_id=case.sender.flow.flow_id+1) if malformed=='wrong_flow'
        else inner(case,0,generation=case.sender.flow.generation+1))
    packet=case.sender.cipher.seal(payload,FrameKind.DATA_TURN_END,authorize=case.authorities[0].live)
    case.channels[0].deliver(packet);case.receiver.step()
    assert case.receiver.flow._ack_work_gen==before
    assert case.receiver.counts['rx_DATA_turn_end']==0
    assert case.receiver._turn_end_highest_received==0

def test_old_authenticated_marker_after_newer_data_keeps_quiet_barrier(composition):
    case=composition(b'x'*1000,train_depth=8,train32=True,turn_end=True)
    case.receiver.set_receiver_ack_quiet_us(150000)
    case.sender.send(case.data);case.receiver.step()
    old=case.sender.cipher.seal(inner(case,0),FrameKind.DATA_TURN_END,authorize=case.authorities[0].live)
    newer=case.sender.cipher.seal(inner(case,1),FrameKind.DATA,authorize=case.authorities[0].live)
    case.channels[0].deliver(newer);case.receiver.step()
    assert case.receiver._turn_end_highest_received==2
    case.clock.advance(5000);case.channels[0].deliver(old);case.receiver.step()
    assert case.receiver.counts['rx_DATA_turn_end']==0
    assert case.receiver.counts['rx_DATA_turn_end_old']==1
    assert case.receiver.ack_due_us>case.clock()[1]
    work=case.receiver.flow._ack_work_gen;due=case.receiver.ack_due_us
    case.channels[0].deliver(old);case.receiver.step()
    assert case.receiver.flow._ack_work_gen==work and case.receiver.ack_due_us==due

@pytest.mark.parametrize('entry',['adapter','worker'])
def test_cipher_authorize_reentry_caught_by_callback_still_latches_failure(composition,entry):
    case=composition(b'x'*386,train_depth=8,train32=True,turn_end=True)
    original=case.authorities[0].live;seen=[]
    def live():
        if case.sender._turn_end_dispatch is not None and not seen:
            seen.append(True)
            with pytest.raises((WireError,HardwareError)):
                if entry=='adapter':case.sender._send(None)
                else:case.workers[0].queue_data(b'bad',logical_id=b'Z'*32)
        return original()
    case.authorities[0].live=live
    with pytest.raises((WireError,HardwareError)):run(case)
    assert seen and case.workers[0]._turn_end_failed
    assert case.journals[0].snapshot()['attempts']==1

@pytest.mark.parametrize('target',['cipher','adapter'])
def test_authorize_cannot_disable_selected_option_mid_seal(composition,target):
    case=composition(b'x'*386,train_depth=8,train32=True,turn_end=True)
    original=case.authorities[0].live;seen=[]
    def live():
        if case.sender._turn_end_dispatch is not None and not seen:
            seen.append(True)
            setattr(case.sender.cipher if target=='cipher' else case.sender,'_turn_end_enabled',False)
        return original()
    case.authorities[0].live=live
    with pytest.raises((WireError,HardwareError)):run(case)
    assert seen and case.workers[0]._turn_end_failed
    assert case.journals[0].snapshot()['attempts']==1

def inject_ack(case,ack_seq,credit,revision):
    f=case.sender.flow
    body=build_ack_frame(flow_id=f.flow_id,generation=f.generation,ack_seq=ack_seq,
        credit=credit,sack=0,grant_revision=revision,limits=f.limits)
    packet=case.receiver.cipher.seal(body,FrameKind.ACK,authorize=case.authorities[1].live)
    case.channels[1].deliver(packet)

def test_authenticated_progress_ack_during_sealed_pending_cannot_reopen(composition):
    case=composition(b'x'*1000,train_depth=8,train32=True,turn_end=True)
    case.sender.send(case.data);inject_ack(case,0,2,1)
    case.sender.step()
    w=case.workers[0];seal=w._turn_end_seal
    assert seal is not None and w._turn_end_queued and len(w.pending_physical)==2
    before=case.journals[0].snapshot()['attempts'];identity=w.train_identity
    # Canonical authenticated credit/progress is logical receipt, never local RF_DONE.
    inject_ack(case,2,32,2);case.sender.step()
    assert case.sender.counts['rx_ACK_progress']>0
    assert w._turn_end_seal is seal and not w.has_capacity and w.train_identity==identity
    assert case.journals[0].snapshot()['attempts']==before

def test_exact_preamble32_partial_tail_ack_eligibility_pair(composition):
    observations=[]
    for enabled in (False,True):
        case=composition(b'x'*1000,train_depth=8,train32=True,turn_end=enabled,preamble=32)
        case.receiver.set_receiver_ack_quiet_us(150000)
        case.sender.send(case.data);inject_ack(case,0,2,1);case.sender.step()
        finish_one(case.channels[0]);case.receiver.step()
        finish_one(case.channels[0]);before=case.journals[1].snapshot()['attempts']
        case.receiver.step()
        observations.append(dict(markers=case.sender.counts['tx_DATA_turn_end'],
            received_markers=case.receiver.counts['rx_DATA_turn_end'],
            ack_attempted=case.journals[1].snapshot()['attempts']>before,
            pending_wait_us=max(0,case.receiver.ack_due_us-case.clock()[1])))
    assert observations[0]['markers']==0 and not observations[0]['ack_attempted']
    assert observations[0]['pending_wait_us']>0
    assert observations[1]['markers']==observations[1]['received_markers']==1
    assert observations[1]['ack_attempted']

def test_real_grant_route_binds_capability_and_mutation_refuses(composition):
    case=composition(b'x'*1000,train_depth=8,train32=True,turn_end=True)
    a,b=case.authorities
    assert a.grant.digest==b.grant.digest
    assert a.route_record['turn_end_capability']==a.plan['turn_end_capability']
    assert a.live()
    a.plan['turn_end_capability']['host_pending_limit']=4
    assert not a.live()
    with pytest.raises((WireError,HardwareError)):case.sender.send(case.data);case.sender.step()
    assert case.journals[0].snapshot()['attempts']==0
