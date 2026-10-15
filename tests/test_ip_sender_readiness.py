"""Versioned sender readiness on real localhost TCP; no native/backend proof."""
from dataclasses import replace
import time

import pytest

from meshticulum.bench.services import ServiceTransfer, resource
from meshticulum.contracts.identity import SessionKey
from meshticulum.flow.flow import build_data_frame, build_ack_frame
from meshticulum.ip.authority import IpAuthority, grant_from_plan, revoke
from meshticulum.ip.flow import IpFlow
from meshticulum.ip.journal import IpJournal
from meshticulum.ip.state import SessionStore
from meshticulum.ip.wire import IpCipher, IpError, IpGrant, IpLimits, FrameKind, HEADER, PREFIX, MAGIC, READY_MODE
from tests.test_ip_transport import A, B, BOOT, pair


def payload():
    return resource.encode_request('/packs/'+'a'*64+'/manifest.json',0,32)


def inject(sender, kind=FrameKind.READY, body=None, *, counter=1, version=None, direction=0, session=None):
    """An adversarial admitted peer writes bytes; receiver must charge/refuse them."""
    grant=sender.authority.grant
    body=grant.digest if body is None else body
    header=HEADER.pack(MAGIC,grant.wire_version if version is None else version,int(kind),direction,
        grant.session.session_id if session is None else session,counter,len(body))
    packet=header+sender.cipher._tx.encrypt(bytes(4)+counter.to_bytes(8,'big'),body,grant.domain+grant.digest+header)
    framed=PREFIX.pack(len(packet))+packet
    sender.worker.sock.sendall(framed)
    return framed


def finish(flows):
    for _ in range(500):
        flows[0].step();flows[1].step()
        if all(f.flow.complete for f in flows):return
        time.sleep(.001)
    pytest.fail('bounded ready-mode localhost flow incomplete')


def test_receiver_waits_beyond_old_startup_window_then_uses_genuine_ack(tmp_path):
    with pair(tmp_path/'ready',payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        a,b=flows;src,dst=ServiceTransfer(a),ServiceTransfer(b)
        deadlines=[f.authority.application_deadline_us for f in flows]
        assert b.step() is False
        clock.advance(6800000)
        assert b.step() is False
        assert b.flow._startup_first_at is None and b.flow.startup_pending
        assert workers[1].journal.snapshot()['attempts']==0
        src.send(payload())
        assert workers[0].journal.snapshot()['lanes']=={'READY':1}
        assert workers[0].journal.snapshot()['reserved_stream_bytes']==83
        assert stores[0]._db.execute('SELECT tx FROM state').fetchone()==(1,)
        assert a.step() is False and a.flow._peer_credit==0
        assert workers[0].journal.snapshot()['lanes']['READY']==1
        assert 'DATA' not in workers[0].journal.snapshot()['lanes']
        assert b.step() is False
        assert b._ready_received and b.flow.startup_pending
        assert workers[1].journal.snapshot()['lanes']=={'ACK':1}
        assert b.events[0]=={'event':'rx','kind':'READY','counter':1}
        assert stores[1]._db.execute('SELECT high FROM state').fetchone()==stores[0]._db.execute('SELECT tx FROM state').fetchone()
        assert [f.authority.application_deadline_us for f in flows]==deadlines
        finish(flows)
        assert dst.received()==payload()
        assert a.counts['tx_READY']==b.counts['rx_READY']==1
        assert all(w.journal.snapshot()['peer_receipt_established'] is False for w in workers)


def test_v1_startup_failure_and_original_charges_are_retained(tmp_path):
    with pair(tmp_path/'legacy',payload(),limits=IpLimits(max_retries=3)) as (flows,workers,stores,clock):
        b=flows[1];deadline=b.authority.application_deadline_us
        b.step();first=b.flow._startup_first_at
        before=workers[1].journal.snapshot()
        clock.advance(6800000)
        with pytest.raises(IpError,match='ip_reliable_flow_failed'):b.step()
        assert b.closed and b.flow.failure=='startup_grant_unanswered'
        assert b.flow._startup_first_at==first and b.authority.application_deadline_us==deadline
        after=workers[1].journal.snapshot()
        assert after['reserved_stream_bytes']>=before['reserved_stream_bytes']>0
        assert 'READY' not in after['lanes']


def test_ready_sender_idle_step_keeps_frozen_flow_and_service_fresh(tmp_path):
    with pair(tmp_path/'idle-sender',payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        a=flows[0]
        assert a.service_fresh() and a.step() is False
        clock.advance(6800000)
        assert a.step() is False and a.service_fresh()
        assert a.flow._peer_credit==0 and a.flow._startup_wait_at is None
        assert a.flow._startup_first_at is None and not a.events
        assert workers[0].journal.snapshot()['attempts']==0
        assert stores[0]._db.execute('SELECT tx FROM state').fetchone()==(0,)
        ServiceTransfer(a).send(payload())
        finish(flows)


def test_authenticated_early_ack_cannot_seed_sender_credit_before_ready(tmp_path):
    with pair(tmp_path/'early-ack',payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        a,b=flows;transfer=ServiceTransfer(a)
        inner=build_ack_frame(flow_id=1,generation=1,ack_seq=0,credit=4,sack=0)
        with b.authority.application_lease():
            packet=b.cipher.seal(inner,FrameKind.ACK,authorize=b.authority.live)
            workers[1].transmit(packet,'ACK',b.authority)
        assert a.step() is False and a.flow._peer_credit==0
        assert a.flow._startup_wait_at is None and a.flow._startup_first_at is None
        assert a.counts['rx_refused']==1 and not a.events
        assert stores[0]._db.execute('SELECT high FROM state').fetchone()==(1,)
        assert workers[0].journal.snapshot()['received_stream_bytes']==len(packet)+4
        assert workers[0].journal.snapshot()['attempts']==0
        transfer.send(payload());assert a.flow._peer_credit==0
        # The independent receiver's genuine ACK has a later durable nonce.
        finish(flows)
        assert a.flow.complete and b.flow.complete


@pytest.mark.parametrize('boundary',['cipher','worker','journal','open'])
def test_v1_refuses_ready_before_nonce_or_byte_reservation(tmp_path,boundary):
    with pair(tmp_path/boundary,payload()) as (flows,workers,stores,clock):
        a,b=flows;grant=a.authority.grant
        header=HEADER.pack(MAGIC,1,int(FrameKind.READY),0,grant.session.session_id,1,32)
        packet=header+b'x'*48
        with pytest.raises(IpError):
            if boundary=='cipher':a.cipher.seal(grant.digest,FrameKind.READY,authorize=a.authority.live)
            elif boundary=='journal':workers[0].journal.reserve(PREFIX.pack(len(packet))+packet,'READY')
            elif boundary=='open':b.cipher.open(packet,authorize=b.authority.live)
            else:
                with a.authority.application_lease():workers[0].transmit(packet,'READY',a.authority)
        assert stores[0]._db.execute('SELECT tx FROM state').fetchone()==(0,)
        assert stores[1]._db.execute('SELECT high FROM state').fetchone()==(0,)
        assert all(w.journal.snapshot()['attempts']==0 for w in workers)


@pytest.mark.parametrize('damage',['counter','direction','version','session','length','digest','tag'])
def test_invalid_ready_cannot_start_ack_clock_and_rx_bytes_are_charged(tmp_path,damage):
    with pair(tmp_path/damage,payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        kwargs={'counter':2} if damage=='counter' else {'direction':1} if damage=='direction' else \
            {'version':1} if damage=='version' else {'session':b'z'*16} if damage=='session' else \
            {'body':b'z'*31} if damage=='length' else {'body':b'z'*32} if damage=='digest' else {}
        if damage=='tag':
            grant=flows[0].authority.grant
            packet=flows[0].cipher.seal(grant.digest,FrameKind.READY,authorize=flows[0].authority.live)
            packet=packet[:-1]+bytes([packet[-1]^1]);framed=PREFIX.pack(len(packet))+packet
            workers[0].sock.sendall(framed)
        else:framed=inject(flows[0],**kwargs)
        assert flows[1].step() is False
        assert not flows[1]._ready_received and flows[1].flow._startup_first_at is None
        assert flows[1].counts['rx_refused']==1
        receipt=workers[1].journal.snapshot()
        assert receipt['received_stream_bytes']==len(framed) and receipt['attempts']==0


@pytest.mark.parametrize('kind',[FrameKind.DATA,FrameKind.ACK])
def test_authenticated_inner_frame_before_ready_consumes_replay_without_ack(tmp_path,kind):
    with pair(tmp_path/kind.name,payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        inner=build_data_frame(flow_id=1,generation=1,seq=0,total=1,last=True,payload=b'x') if kind is FrameKind.DATA else \
            build_ack_frame(flow_id=1,generation=1,ack_seq=0,credit=4,sack=0)
        inject(flows[0],kind,inner)
        assert flows[1].step() is False
        assert stores[1]._db.execute('SELECT high FROM state').fetchone()==(1,)
        assert not flows[1]._ready_received and flows[1].flow._startup_first_at is None
        assert workers[1].journal.snapshot()['attempts']==0


def test_replayed_or_new_nonce_ready_does_not_reset_startup_or_restore_failed_flow(tmp_path):
    with pair(tmp_path/'repeat',payload(),startup_mode=READY_MODE,limits=IpLimits(max_retries=3)) as (flows,workers,stores,clock):
        ServiceTransfer(flows[0]).send(payload());flows[1].step()
        first=flows[1].flow._startup_first_at
        inject(flows[0]);inject(flows[0],counter=2)
        flows[1].step()
        assert flows[1].counts['rx_READY']==1 and flows[1].counts['rx_refused']==2
        assert flows[1].flow._startup_first_at==first and flows[1].flow.startup_pending
        clock.advance(6800000)
        with pytest.raises(IpError):flows[1].step()
        before=workers[1].journal.snapshot()
        with pytest.raises(IpError,match='ip_flow_closed'):flows[1].step()
        assert workers[1].journal.snapshot()==before and flows[1].flow._startup_first_at==first


@pytest.mark.parametrize('damage',['expiry','revocation','owner'])
def test_waiting_receiver_keeps_original_deadline_authority_and_owner_checks(tmp_path,damage):
    with pair(tmp_path/damage,payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        b=flows[1];deadline=b.authority.application_deadline_us
        if damage=='expiry':clock.advance(60000000)
        elif damage=='revocation':revoke(b.authority.path)
        else:workers[1].close()
        with pytest.raises(Exception):b.step()
        assert b.closed and b.authority.application_deadline_us==deadline
        assert workers[1].journal.snapshot()['attempts']==0 and b.flow._startup_first_at is None


def test_ready_sender_direction_and_exact_object_refuse_before_tx(tmp_path):
    with pair(tmp_path/'sender',payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        a,b=flows
        with pytest.raises(IpError):b.cipher.seal(b.authority.grant.digest,FrameKind.READY,authorize=b.authority.live)
        with a.authority.application_lease():
            with pytest.raises(IpError,match='ip_ready_object_mismatch'):a.send(b'x'*len(payload()))
        assert all(s._db.execute('SELECT tx FROM state').fetchone()==(0,) for s in stores)
        assert all(w.journal.snapshot()['attempts']==0 for w in workers)


@pytest.mark.parametrize('damage',['version','direction','counter','zero','length','session','lane'])
def test_worker_refuses_unbound_ready_header_before_reserving_bytes(tmp_path,damage):
    with pair(tmp_path/damage,payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        grant=flows[0].authority.grant
        version=1 if damage=='version' else 2
        direction=1 if damage=='direction' else 0
        counter=2 if damage=='counter' else 0 if damage=='zero' else 1
        length=31 if damage=='length' else 32
        session=b'z'*16 if damage=='session' else grant.session.session_id
        packet=HEADER.pack(MAGIC,version,int(FrameKind.READY),direction,session,counter,length)+b'x'*(length+16)
        with flows[0].authority.application_lease():
            with pytest.raises(IpError):workers[0].transmit(packet,'ACK' if damage=='lane' else 'READY',flows[0].authority)
        assert workers[0].journal.snapshot()['attempts']==0
        assert stores[0]._db.execute('SELECT tx FROM state').fetchone()==(0,)


@pytest.mark.parametrize('field',['generation','object','route','expiry','limits','mode'])
def test_ready_cannot_cross_exact_grant_context(tmp_path,field):
    with pair(tmp_path/field,payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        a,b=flows;grant=a.authority.grant
        changes={'generation':{'session':SessionKey(A,B,grant.session.session_id,b'z'*16)},
            'object':{'object_sha256':b'z'*32},'route':{'route_binding_digest':b'z'*32},
            'expiry':{'application_expires_epoch_us':grant.application_expires_epoch_us-1},
            'limits':{'limits':replace(grant.limits,window_frames=7)},'mode':{'startup_mode':None}}[field]
        other=replace(grant,**changes)
        store=SessionStore(b.authority.path/'other.sqlite',other,B,boot_id=BOOT,clock=clock,
            original_deadline_us=b.authority.application_deadline_us,create=True)
        try:
            packet=a.cipher.seal(grant.digest,FrameKind.READY,authorize=a.authority.live)
            with pytest.raises(IpError):IpCipher(other,B,store).open(packet,authorize=b.authority.live)
            assert store._db.execute('SELECT high FROM state').fetchone()==(0,)
        finally:store.close()


@pytest.mark.parametrize('budget',['bytes','frames'])
def test_ready_consumes_original_caps_and_exhaustion_never_refunds(tmp_path,budget):
    limits=IpLimits(max_tx_stream_bytes=128) if budget=='bytes' else IpLimits(max_frames=2,max_plaintext=64)
    with pair(tmp_path/budget,payload(),startup_mode=READY_MODE,limits=limits) as (flows,workers,stores,clock):
        ServiceTransfer(flows[0]).send(payload());flows[1].step()
        with pytest.raises(IpError):flows[0].step()
        receipt=workers[0].journal.snapshot()
        assert flows[0].closed and receipt['lanes']['READY']==1 and receipt['reserved_stream_bytes']>=83
        assert receipt['reserved_stream_bytes']<=limits.max_tx_stream_bytes and receipt['attempts']<=limits.max_frames
        assert stores[0]._db.execute('SELECT tx FROM state').fetchone()==(2,)
        with pytest.raises(IpError):flows[0].send(payload())
        assert workers[0].journal.snapshot()==receipt


def test_ambiguous_ready_write_fences_and_persists_nonce_and_debt(tmp_path,monkeypatch):
    with pair(tmp_path/'unknown',payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        a=flows[0];journal=workers[0].journal;store=stores[0]
        def failed_progress(*args):raise OSError('injected post-write durable failure')
        monkeypatch.setattr(journal,'progress',failed_progress)
        with pytest.raises(OSError):ServiceTransfer(a).send(payload())
        assert a.closed and workers[0].closed
        receipt=journal.snapshot()
        assert receipt['fenced'] and receipt['outcomes']=={'UNKNOWN':1} and receipt['reserved_stream_bytes']==83
        assert store._db.execute('SELECT tx FROM state').fetchone()==(1,)
        with pytest.raises(IpError):a.send(payload())
        assert journal.snapshot()==receipt
        journal.close();reopened=IpJournal(journal.path,journal.grant)
        try:assert reopened.snapshot()==receipt
        finally:reopened.close()
        store.close();reopened=SessionStore(a.authority.path/'session.sqlite',store.grant,A,boot_id=BOOT,clock=clock,
            original_deadline_us=store.original_deadline_us)
        try:assert reopened._db.execute('SELECT tx FROM state').fetchone()==(1,)
        finally:reopened.close()


def test_sender_rechecks_original_live_authority_after_ready_write(tmp_path,monkeypatch):
    with pair(tmp_path/'bookend',payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        transmit=workers[0].transmit
        def expire_after_write(*args):
            result=transmit(*args);clock.advance(60000000);return result
        monkeypatch.setattr(workers[0],'transmit',expire_after_write)
        with pytest.raises(Exception):ServiceTransfer(flows[0]).send(payload())
        assert flows[0].closed and workers[0].journal.snapshot()['reserved_stream_bytes']==83
        assert workers[0].journal.snapshot()['outcomes']=={'KERNEL_ACCEPTED':1}


def test_ready_replay_survives_store_reopen_and_grant_cannot_restart(tmp_path):
    with pair(tmp_path/'reopen',payload(),startup_mode=READY_MODE) as (flows,workers,stores,clock):
        a,b=flows;packet=a.cipher.seal(a.authority.grant.digest,FrameKind.READY,authorize=a.authority.live)
        b.cipher.open(packet,authorize=b.authority.live)
        stores[1].close();s=SessionStore(b.authority.path/'session.sqlite',b.authority.grant,B,boot_id=BOOT,clock=clock,
            original_deadline_us=b.authority.application_deadline_us)
        try:
            with pytest.raises(IpError,match='replayed_or_too_old'):IpCipher(b.authority.grant,B,s).open(packet,authorize=b.authority.live)
        finally:s.close()
        with pytest.raises(IpError,match='already_claimed'):IpFlow(authority=a.authority,worker=workers[0],store=stores[0])
        with pytest.raises(FileExistsError):IpAuthority(a.authority.path,local_peer=A,host_boot_id=BOOT,clock=clock,owner_probe=workers[0].owned)


@pytest.mark.parametrize('damage',['v1_extra','v2_missing','unknown','wrong_type','too_small'])
def test_plan_mode_is_explicit_and_limits_are_not_silently_changed(tmp_path,damage):
    with pair(tmp_path/damage,payload()) as (flows,workers,stores,clock):
        p=dict(flows[0].authority.plan)
        p.update(schema='meshticulum.ip-service-run.v2',startup_mode=READY_MODE)
        if damage=='v1_extra':p['schema']='meshticulum.ip-service-run.v1'
        elif damage=='v2_missing':p.pop('startup_mode')
        elif damage=='unknown':p['startup_mode']='another_mode'
        elif damage=='wrong_type':p['startup_mode']=True
        else:p['limits']=dict(p['limits'],max_frames=1)
        with pytest.raises(IpError):grant_from_plan(p,b's'*32,local_peer=A,host_boot_id=BOOT)


def test_v1_context_and_ciphertext_match_frozen_core_vector():
    # Generated from exact Core3b wire.py (SHA256 3d076cfe...175f), never v2 code.
    grant=IpGrant(SessionKey(A,B,b'i'*16,b'g'*16),'MR1.request',32,b'o'*32,b'r'*32,
        1000000,91000000,61000000,('127.0.0.1',10001),('127.0.0.1',10002),IpLimits(),b's'*32)
    assert grant.digest.hex()=='935bae886d968f25e1da1809d7273d560ca83a568292514b4ec87ff0d796d4b4'
    class Store:
        binding=(grant.digest,0)
        def reserve_transmit(self,*,authorize):
            assert authorize() is True
            return 1
    packet=IpCipher(grant,A,Store()).seal(b'authenticated fixture',FrameKind.DATA,authorize=lambda:True)
    assert packet.hex()=='4d49010100696969696969696969696969696969690000000000000001001528e9c554289fb3cb90b2480f8cf710536ea446524d36d7644caf6e187e211676a8ac45f4fb'
