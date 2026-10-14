"""Actual loopback TCP with source-provisioned peers; no RF/native SAGE proof."""
from contextlib import contextmanager,ExitStack
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import socket
import time

import pytest
from meshticulum.contracts.identity import PeerID,SessionKey
from meshticulum.ip.wire import IpGrant,IpLimits,IpCipher,IpError,FrameKind,PREFIX
from meshticulum.ip.state import SessionStore
from meshticulum.ip.journal import IpJournal
from meshticulum.ip.authority import IpAuthority,revoke
from meshticulum.ip.worker import TcpWorker
from meshticulum.ip.flow import IpFlow
from meshticulum.bench.services import ServiceTransfer,resource
from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.wire import WireError

A=PeerID('ip-test','a'*64);B=PeerID('ip-test','b'*64)
BOOT='21f9c178-6090-40d9-9359-ecf1c7b950b1'


class Clock:
    def __init__(self):self.offset=0
    def __call__(self):return (time.time_ns()//1000+self.offset,time.monotonic_ns()//1000+self.offset)
    def advance(self,us):self.offset+=us


def connected():
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
    client=socket.socket();client.connect(listener.getsockname());server,_=listener.accept();listener.close()
    return client,server


@contextmanager
def pair(tmp_path,payload,format='MR1.request',limits=None,*,initiator=A,responder=B,session_id=b'i'*16,generation=b'g'*16,expires=None,deadline_us=None,route_binding_digest=b'r'*32,startup_mode=None):
    tmp_path.mkdir(mode=0o700)
    left,right=connected();clock=Clock();epoch,mono=clock()
    spec=IpGrant(SessionKey(initiator,responder,session_id,generation),format,len(payload),hashlib.sha256(payload).digest(),route_binding_digest,
      epoch-1000000,(expires or epoch+60000000)+30000000,expires or epoch+60000000,left.getsockname(),right.getsockname(),limits or IpLimits(),b's'*32,startup_mode)
    with ExitStack() as stack:
        stack.callback(left.close);stack.callback(right.close)
        flows=[];workers=[];stores=[]
        for i,(sock,peer) in enumerate(((left,initiator),(right,responder))):
            directory=tmp_path/str(i);directory.mkdir(mode=0o700)
            plan=dict(schema='meshticulum.ip-service-run.v1',host_boot_id=BOOT,local_peer=asdict(peer),
              session=dict(initiator=asdict(initiator),responder=asdict(responder),session_id=spec.session.session_id.hex(),generation=spec.session.session_generation.hex()),
              format=spec.format,object={'bytes':len(payload),'sha256':spec.object_sha256.hex()},route_binding_digest=spec.route_binding_digest.hex(),
              issued_epoch_us=spec.issued_epoch_us,expires_epoch_us=spec.expires_epoch_us,application_expires_epoch_us=spec.application_expires_epoch_us,
              application_deadline_monotonic_us=deadline_us or mono+59000000,initiator_address=list(spec.initiator_address),responder_address=list(spec.responder_address),limits=asdict(spec.limits))
            if startup_mode is not None:
                plan.update(schema='meshticulum.ip-service-run.v2',startup_mode=startup_mode)
            for name,raw in [('grant.json',json.dumps(plan).encode()),('secret.bin',spec.secret),('active',b'active\n')]:
                fd=os.open(directory/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                with os.fdopen(fd,'wb')as f:f.write(raw)
            journal=IpJournal(directory/'bytes.sqlite',spec,create=True);stack.callback(journal.close)
            worker=TcpWorker(sock,spec,peer,journal);stack.callback(worker.close)
            authority=IpAuthority(directory,local_peer=peer,host_boot_id=BOOT,clock=clock,owner_probe=worker.owned);stack.callback(authority.close)
            store=SessionStore(directory/'session.sqlite',authority.grant,peer,boot_id=BOOT,clock=clock,original_deadline_us=authority.application_deadline_us,create=True);stack.callback(store.close)
            flow=IpFlow(authority=authority,worker=worker,store=store);stack.callback(flow.close)
            flows.append(flow);workers.append(worker);stores.append(store)
        yield flows,workers,stores,clock


def pump(sender,receiver):
    for _ in range(4000):
        sender.step();receiver.step()
        if sender.wire_complete and receiver.wire_complete:return
        time.sleep(.001)
    raise AssertionError('bounded TCP transfer did not complete')


def test_real_tcp_public_flow_uses_credit_replay_and_byte_accounting(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'public',payload)as(flows,workers,stores,clock):
        src,dst=[ServiceTransfer(f)for f in flows]
        sender,receiver=ServiceRunner(src,payload=payload),ServiceRunner(dst)
        assert not workers[0].journal.snapshot()['attempts']
        sender.start();receiver.start();pump(sender,receiver)
        assert receiver.public_payload==payload and receiver.application_result is None
        assert flows[0].flow.acked_frames()>0
        assert not flows[0].flow.startup_pending
        for worker in workers:
            receipt=worker.journal.snapshot()
            assert receipt['attempts']>0 and receipt['received_stream_bytes']>0
            assert receipt['outcomes']['KERNEL_ACCEPTED']>0 and 'RF_DONE' not in receipt['outcomes']
            assert receipt['measured_airtime_us'] is None and not receipt['peer_receipt_established']
        assert workers[1].journal.snapshot()['lanes']['ACK']>0


def test_large_response_uses_ip_frames_above_radio_mtu(tmp_path):
    request=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,16000)
    payload=resource.BlockServer(lambda _:b'x'*16000).handle(request)
    with pair(tmp_path/'large',payload,'MR1.response')as(flows,workers,stores,clock):
        src,dst=[ServiceTransfer(f)for f in flows]
        sender,receiver=ServiceRunner(src,payload=payload),ServiceRunner(dst)
        sender.start();receiver.start();pump(sender,receiver)
        assert receiver.public_payload==payload
        maximum=workers[0].journal.db.execute('SELECT max(bytes) FROM attempts').fetchone()[0]
        assert maximum>255
        assert flows[0].flow.acked_frames()==8


def test_every_mutated_byte_and_replay_refuse_without_replay_poisoning(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'crypto',payload)as(flows,workers,stores,clock):
        a,b=flows
        packet=a.cipher.seal(b'authenticated fixture',FrameKind.DATA,authorize=a.authority.live)
        for index in range(len(packet)):
            bad=packet[:index]+bytes([packet[index]^1])+packet[index+1:]
            with pytest.raises(IpError):b.cipher.open(bad,authorize=b.authority.live)
        assert b.cipher.open(packet,authorize=b.authority.live).payload==b'authenticated fixture'
        with pytest.raises(IpError):b.cipher.open(packet,authorize=b.authority.live)


@pytest.mark.parametrize('field',['format','route','object','expiry','limits'])
def test_grant_context_changes_cannot_impersonate_same_session(tmp_path,field):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'binding',payload)as(flows,workers,stores,clock):
        grant=flows[0].authority.grant
        changes={'format':{'format':'MR1.response'},'route':{'route_binding_digest':b'z'*32},
          'object':{'object_sha256':b'z'*32},'expiry':{'application_expires_epoch_us':grant.application_expires_epoch_us-1},
          'limits':{'limits':replace(grant.limits,window_frames=7)}}[field]
        other=replace(grant,**changes)
        store=SessionStore(tmp_path/'binding/1/other.sqlite',other,B,boot_id=BOOT,clock=clock,original_deadline_us=flows[1].authority.application_deadline_us,create=True)
        try:
            cipher=IpCipher(other,B,store)
            packet=flows[0].cipher.seal(b'bound',FrameKind.DATA,authorize=flows[0].authority.live)
            with pytest.raises(IpError):cipher.open(packet,authorize=lambda:True)
        finally:store.close()


def test_revoke_and_original_application_expiry_stop_every_lane(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'revoke',payload)as(flows,workers,stores,clock):
        before=workers[1].journal.snapshot();revoke(flows[1].authority.path)
        with pytest.raises(IpError):flows[1].step()
        assert workers[1].journal.snapshot()==before
    with pair(tmp_path/'expires',payload)as(flows,workers,stores,clock):
        before=workers[1].journal.snapshot();clock.advance(61000000)
        with pytest.raises(IpError):flows[1].step()
        assert workers[1].journal.snapshot()==before


def test_nonce_and_unresolved_byte_reservation_survive_reopen(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'reopen',payload)as(flows,workers,stores,clock):
        authority=flows[0].authority;grant=authority.grant
        p=authority.path/'independent.sqlite'
        store=SessionStore(p,grant,A,boot_id=BOOT,clock=clock,original_deadline_us=authority.application_deadline_us,create=True)
        assert store.reserve_transmit(authorize=authority.live)==1;store.close()
        store=SessionStore(p,grant,A,boot_id=BOOT,clock=clock,original_deadline_us=authority.application_deadline_us)
        assert store.reserve_transmit(authorize=authority.live)==2;store.close()
        p=authority.path/'independent-bytes.sqlite';journal=IpJournal(p,grant,create=True)
        journal.reserve(b'ciphertext-only-fixture','DATA');journal.close()
        journal=IpJournal(p,grant)
        assert journal.snapshot()['outcomes']=={'UNKNOWN':1} and journal.snapshot()['fenced']
        with pytest.raises(IpError):journal.reserve(b'new','DATA')
        journal.close()


def test_forged_stream_length_is_bounded_and_charged(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'length',payload)as(flows,workers,stores,clock):
        workers[0].sock.sendall(PREFIX.pack(2**32-1))
        with pytest.raises(IpError):flows[1].step()
        receipt=workers[1].journal.snapshot()
        assert receipt['received_stream_bytes']==4 and receipt['fenced']
        assert workers[1].closed and not workers[1].buffer


def test_private_ip_payload_refused_without_native_policy(tmp_path):
    with pair(tmp_path/'private',b'opaque encrypted object','LME.binary')as(flows,workers,stores,clock):
        transfer=ServiceTransfer(flows[0])
        with pytest.raises(WireError):ServiceRunner(transfer,payload=b'opaque encrypted object')
        assert workers[0].journal.snapshot()['attempts']==0


def test_single_use_authority_cannot_attach_second_flow(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'reuse',payload)as(flows,workers,stores,clock):
        with pytest.raises(IpError):IpFlow(authority=flows[0].authority,worker=workers[0],store=stores[0])
        with pytest.raises(FileExistsError):IpAuthority(flows[0].authority.path,local_peer=A,host_boot_id=BOOT,clock=clock,owner_probe=workers[0].owned)


def test_pending_receive_crash_is_charged_and_fenced(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'read-crash',payload)as(flows,workers,stores,clock):
        grant=flows[0].authority.grant;p=flows[0].authority.path/'read-crash.sqlite'
        journal=IpJournal(p,grant,create=True)
        assert journal.reserve_receive(80)==80
        journal.close();journal=IpJournal(p,grant)
        receipt=journal.snapshot()
        assert receipt['unknown_receive_stream_bytes']==80 and receipt['fenced']
        assert receipt['received_stream_bytes']==0 and receipt['pending_receive_stream_bytes']==0
        with pytest.raises(IpError):journal.reserve_receive(1)
        journal.close()


def test_partial_kernel_write_exception_retains_full_debt_and_closes_owner(tmp_path,monkeypatch):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'partial-write',payload)as(flows,workers,stores,clock):
        worker=workers[0];original=socket.socket.send;calls=[]
        def send(sock,data,*args,**kwargs):
            if sock is worker.sock:
                calls.append(1)
                if len(calls)==1:return original(sock,data[:3],*args,**kwargs)
                raise OSError('source fault after partial kernel write')
            return original(sock,data,*args,**kwargs)
        monkeypatch.setattr(socket.socket,'send',send)
        with pytest.raises(OSError):
            with flows[0].authority.application_lease():
                packet=flows[0].cipher.seal(b'sealed fixture',FrameKind.DATA,authorize=flows[0].authority.live)
                worker.transmit(packet,'DATA',flows[0].authority)
        receipt=worker.journal.snapshot()
        assert worker.closed and receipt['fenced']
        assert receipt['outcomes']=={'UNKNOWN':1}
        assert receipt['known_kernel_accepted_stream_bytes']==3
        assert receipt['reserved_stream_bytes']==len(packet)+4


def test_transmit_lane_and_full_endpoint_binding_precede_socket_write(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'lane',payload)as(flows,workers,stores,clock):
        authority=flows[0].authority
        with authority.application_lease():
            packet=flows[0].cipher.seal(b'exact lane',FrameKind.DATA,authorize=authority.live)
            with pytest.raises(IpError):workers[0].transmit(packet,'ACK',authority)
        assert workers[0].journal.snapshot()['attempts']==0
        grant=replace(authority.grant,responder_address=('127.0.0.1',1))
        left,right=connected()
        try:
            with pytest.raises(IpError):TcpWorker(left,grant,A,workers[0].journal)
        finally:left.close();right.close()


def test_tx_and_rx_budgets_stop_without_unbounded_read(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    limits=IpLimits(max_tx_stream_bytes=128,max_rx_stream_bytes=128)
    with pair(tmp_path/'limits',payload,limits=limits)as(flows,workers,stores,clock):
        authority=flows[0].authority;worker=workers[0]
        with authority.application_lease():
            packet=flows[0].cipher.seal(b'x'*90,FrameKind.DATA,authorize=authority.live)
            with pytest.raises(IpError):worker.transmit(packet,'DATA',authority)
        assert worker.journal.snapshot()['attempts']==0
        packet=flows[0].cipher.seal(b'x'*100,FrameKind.DATA,authorize=authority.live)
        worker.sock.sendall(PREFIX.pack(len(packet))+packet)
        with pytest.raises(IpError):flows[1].step()
        assert workers[1].journal.snapshot()['received_stream_bytes']==128
        assert workers[1].closed and not workers[1].buffer


def test_original_deadline_cannot_be_rebound_on_state_reopen(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'deadline-reopen',payload)as(flows,workers,stores,clock):
        authority=flows[0].authority;p=authority.path/'deadline.sqlite'
        kwargs=dict(boot_id=BOOT,clock=clock,original_deadline_us=authority.application_deadline_us)
        store=SessionStore(p,authority.grant,A,create=True,**kwargs);store.close()
        kwargs['original_deadline_us']+=1000000
        with pytest.raises(IpError):SessionStore(p,authority.grant,A,**kwargs)


def test_missing_state_boot_change_and_counter_limit_never_reset(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'persistence',payload)as(flows,workers,stores,clock):
        authority=flows[0].authority;p=authority.path/'limited.sqlite'
        grant=replace(authority.grant,limits=replace(authority.grant.limits,max_frames=1))
        kwargs=dict(boot_id=BOOT,clock=clock,original_deadline_us=authority.application_deadline_us)
        with pytest.raises(FileNotFoundError):SessionStore(p,grant,A,**kwargs)
        store=SessionStore(p,grant,A,create=True,**kwargs)
        assert store.reserve_transmit(authorize=lambda:True)==1;store.close()
        store=SessionStore(p,grant,A,**kwargs)
        with pytest.raises(IpError):store.reserve_transmit(authorize=lambda:True)
        store.close()
        kwargs['boot_id']='00000000-0000-4000-8000-000000000099'
        with pytest.raises(IpError):SessionStore(p,grant,A,**kwargs)


def test_cancellation_and_peer_disconnect_do_not_refund_or_resume(tmp_path):
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'cancel',payload)as(flows,workers,stores,clock):
        runner=ServiceRunner(ServiceTransfer(flows[0]),payload=payload);runner.start()
        flows[1].step();runner.step();before=workers[0].journal.snapshot()
        runner.close()
        with pytest.raises(WireError):runner.step()
        assert workers[0].journal.snapshot()==before
    with pair(tmp_path/'disconnect',payload)as(flows,workers,stores,clock):
        workers[0].close()
        with pytest.raises(IpError):flows[1].step()
        assert flows[1].closed and workers[1].closed


def test_other_thread_cannot_use_a_held_dispatch_lease(tmp_path):
    import threading
    payload=resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)
    with pair(tmp_path/'thread',payload)as(flows,workers,stores,clock):
        authority=flows[0].authority;refused=[]
        def other():
            try:authority.require_dispatch()
            except IpError:refused.append(True)
        with authority.application_lease():
            thread=threading.Thread(target=other);thread.start();thread.join(timeout=2)
            assert not thread.is_alive() and refused==[True]
        assert workers[0].journal.snapshot()['attempts']==0


def test_real_ip_request_response_preserve_original_resource_deadline(tmp_path):
    from meshticulum.bench.services import PreparedBlockExchange
    from tests.test_meshticulum_resources import pack
    from meshticulum.resources import lamplighter as ll
    # Drive the existing signed-pack fixture independently of radio models.
    generator=pack.__wrapped__(tmp_path)
    directory,trust,digest,manifest=generator
    cache=ll.VerifiedCache(directory,trust,digest)
    server=resource.BlockServer(cache.read)
    original_deadline=time.monotonic()+30
    expires=time.time_ns()//1000+29000000
    observed=[]
    def exchange(request,limit,deadline):
        reply=server.handle(request);index=len(observed)
        with pair(tmp_path/('request'+str(index)),request,expires=expires,deadline_us=int(original_deadline*1e6))as(forward,fw,fs,fc):
            with pair(tmp_path/('reply'+str(index)),reply,'MR1.response',initiator=B,responder=A,
                      session_id=b'r'*16,generation=b'h'*16,expires=expires,deadline_us=int(original_deadline*1e6))as(reverse,rw,rs,rc):
                request_src,request_dst=[ServiceTransfer(f)for f in forward]
                reply_src,reply_dst=[ServiceTransfer(f)for f in reverse]
                started=False
                def drive():
                    nonlocal started
                    forward[0].step();forward[1].step()
                    if forward[1].flow.complete and not started:
                        reply_src.send(request_dst.serve_public(server));started=True
                    reverse[0].step();reverse[1].step();time.sleep(.001)
                prepared=PreparedBlockExchange(request_src,reply_dst,pump=drive)
                result=prepared(request,limit,deadline)
                assert all(w.journal.snapshot()['attempts']>0 for w in fw+rw)
                observed.append(deadline)
                return result
    entry=manifest['chunks'][0]
    reader=resource.BlockReader(exchange,max_block=96)
    raw=reader('/packs/'+digest+'/chunks/'+entry['sha256']+'.json',65536,original_deadline)
    ll.chunk(raw,entry)
    assert raw==cache.piece(entry['sha256']) and observed and all(x==original_deadline for x in observed)
