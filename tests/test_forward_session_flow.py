# SPDX-License-Identifier: Apache-2.0
"""MPF2 over real local flow/AEAD/journals with RF completion explicitly modeled.

These are generic opaque carriers, not an admitted private service format or
native policy/capture. No device, radio throughput or physical cost is proved.
"""
import hashlib
import uuid

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import pytest

from meshticulum.privacy.forward_session import ForwardContext, ForwardSession, ForwardSessionError, public
from meshticulum.privacy.mesh_binary_envelope import to_binary
from meshticulum.bench.wire import FrameKind
from tests.test_forward_session import Counters
from tests.test_meshticulum_bench_runtime import Clock
from tests.test_meshticulum_private_carrier import (
    SENDER, RECIPIENT, SENDER_KEY, RECIPIENT_KEY, CONTENT,
    carrier_pair, transfer, sealed_message, open_private)


def session_pair(path, clock, inner):
    # Public deterministic fixture keys; native generation/provenance is absent.
    keys=tuple(X25519PrivateKey.from_private_bytes(hashlib.sha256(label).digest())
               for label in (b'public flow fixture Noise initiator',b'public flow fixture Noise responder'))
    context=ForwardContext(SENDER,RECIPIENT,uuid.uuid4().bytes,hashlib.sha256(inner).digest(),
        clock()[0],1100000000,64,public(keys[0]),public(keys[1]))
    deadline=clock()[1]+context.expires_epoch_us-clock()[0]
    stores=(Counters(path/'i.sqlite'),Counters(path/'r.sqlite'))
    sessions=tuple(ForwardSession(context=context,initiator=index==0,
        signing_key=(SENDER_KEY,RECIPIENT_KEY)[index],noise_static_key=keys[index],counters=stores[index],
        authorize=lambda:True,clock=clock,application_deadline_monotonic_us=deadline) for index in (0,1))
    return sessions,context,stores


def model_channel(path, clock, context, *, fault='none'):
    path.mkdir(mode=0o700)
    observations=[]
    def carry(packet, *, reverse=False, mutate=False, retain_original=True):
        units=('unit-c','unit-b','unit-a') if reverse else ('unit-a','unit-b','unit-c')
        current=packet
        for hop in (0,1):
            index=len(observations)
            expiry=context.expires_epoch_us if retain_original else None
            with carrier_pair(path/str(index),current,clock,units[hop],units[hop+1],index+1,
                              original_expires_epoch_us=expiry) as pair:
                if fault=='loss':pair.workers[0].drop_kind=FrameKind.DATA
                elif fault=='corrupt':pair.workers[0].corrupt_once=True
                elif fault=='replay':pair.workers[0].replay_once=True
                current=transfer(pair,current,clock)
                assert CONTENT.encode() not in current
                if retain_original:
                    assert all(flow.authority.plan['expires_epoch_us']==context.expires_epoch_us
                               for flow in (pair.sender,pair.receiver))
                snapshots=tuple(flow.journal.snapshot() for flow in (pair.sender,pair.receiver))
                assert all(s['attempts']>0 and s['reserved_estimated_airtime_us']>0 for s in snapshots)
                if fault in ('loss','corrupt'):assert pair.sender.flow.retransmits>0
                if fault in ('corrupt','replay'):assert pair.receiver.counts['rx_refused']>0
                observations.append(snapshots)
            if hop==0 and mutate:current=current[:-1]+bytes([current[-1]^1])
        return current
    return carry,observations


def exchange(sessions, carry):
    i,r=sessions
    second=r.respond(carry(i.start()))
    i.finish(carry(second,reverse=True))
    fourth=r.accept_confirmation(carry(i.confirm()))
    i.verify_confirmation(carry(fourth,reverse=True))
    assert i.state==r.state=='ready'


@pytest.mark.parametrize('fault',['none','loss','corrupt','replay'])
def test_full_exchange_and_sealed_record_across_two_modeled_authenticated_hops(tmp_path,fault):
    clock=Clock();inner=to_binary(sealed_message(1).wire)
    sessions,context,stores=session_pair(tmp_path,clock,inner)
    carry,observations=model_channel(tmp_path/'hops',clock,context,fault=fault)
    exchange(sessions,carry)
    opened=sessions[1].open(carry(sessions[0].seal(inner)))
    assert opened==inner and open_private(opened,clock).content==CONTENT
    assert len(observations)==10 and clock()[0]<context.expires_epoch_us
    assert stores[0].reserved==2 and stores[1].reserved==1
    assert stores[1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==1
    for session in sessions:session.close()


def test_relay_with_valid_second_hop_cannot_replace_end_to_end_record_or_refund_cost(tmp_path):
    clock=Clock();inner=to_binary(sealed_message(1).wire)
    sessions,context,stores=session_pair(tmp_path,clock,inner)
    carry,observations=model_channel(tmp_path/'hops',clock,context)
    exchange(sessions,carry)
    before=sum(s['attempts'] for pair in observations for s in pair)
    received=carry(sessions[0].seal(inner),mutate=True)
    # Both adjacent links accepted/ACKed the relay's changed object. Endpoint
    # authentication, not the second-hop ACK, must reject its private content.
    with pytest.raises(ForwardSessionError):sessions[1].open(received)
    assert sessions[1].state=='closed' and stores[0].reserved==2
    assert stores[1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==0
    assert sum(s['attempts'] for pair in observations for s in pair)>before


def test_new_adjacent_grants_cannot_renew_original_end_to_end_record(tmp_path):
    clock=Clock();inner=to_binary(sealed_message(1).wire)
    sessions,context,stores=session_pair(tmp_path,clock,inner)
    carry,observations=model_channel(tmp_path/'hops',clock,context)
    exchange(sessions,carry);packet=sessions[0].seal(inner)
    clock.advance(context.expires_epoch_us-clock()[0]+1)
    # Deliberately supply new valid adjacent-link authority after the original
    # lifetime. It grants no renewal of the end-to-end session or inner message.
    received=carry(packet,retain_original=False)
    with pytest.raises(ForwardSessionError):sessions[1].open(received)
    assert stores[0].reserved==2 and sessions[1].state=='closed'
    assert stores[1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==0
