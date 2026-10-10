# SPDX-License-Identifier: Apache-2.0
"""Real primitives with synthetic authority and durable SQLite test counters."""
import hashlib
import json
from pathlib import Path
import sqlite3
import struct
import uuid

import pytest
from nacl.public import SealedBox
from nacl.signing import SigningKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.exceptions import InvalidTag

from meshticulum.privacy.forward_session import (
    ForwardContext, ForwardSession, ForwardSessionError, HEADER, DOMAIN, nonce)
from meshticulum.privacy.mesh_envelope import Peer, seal_message, open_envelope
from meshticulum.privacy.mesh_binary_envelope import to_binary, to_text


class Counters:
    """Test-only transactional persistence; not native policy or secret storage."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS claims(binding BLOB PRIMARY KEY, tx INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS received(binding BLOB, n INTEGER, PRIMARY KEY(binding,n))')
        self.binding = None
        self.fail_reserve = self.fail_receive = False
    def claim(self, binding):
        try:self.db.execute('INSERT INTO claims VALUES(?,0)', (binding,))
        except sqlite3.IntegrityError:return False
        self.binding = binding
        return True
    def reserve_transmit(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            n=self.db.execute('SELECT tx FROM claims WHERE binding=?',(self.binding,)).fetchone()[0]+1
            self.db.execute('UPDATE claims SET tx=? WHERE binding=?',(n,self.binding))
            self.db.execute('COMMIT')
        except BaseException:self.db.execute('ROLLBACK');raise
        if self.fail_reserve:raise OSError('return_lost_after_nonce_commit')
        return n
    def record_receive(self,n):
        try:self.db.execute('INSERT INTO received VALUES(?,?)',(self.binding,n))
        except sqlite3.IntegrityError:return False
        if self.fail_receive:raise OSError('return_lost_after_replay_commit')
        return True
    @property
    def reserved(self):return self.db.execute('SELECT tx FROM claims WHERE binding=?',(self.binding,)).fetchone()[0]


def pair(tmp_path, *, context=None, keys=None, name='one'):
    keys=keys or (SigningKey(bytes(range(32))),SigningKey(bytes(range(32,64))))
    peers=(Peer('fixture-i',bytes(keys[0].verify_key)),Peer('fixture-r',bytes(keys[1].verify_key)))
    context=context or ForwardContext(*peers,uuid.uuid4().bytes,b'a'*32,10000000,100000000,64)
    clock={'epoch':20000000,'mono':1000000,'live':True}
    stores=(Counters(tmp_path/(name+'-i.sqlite')),Counters(tmp_path/(name+'-r.sqlite')))
    def build(role,index):return ForwardSession(context=context,initiator=role,signing_key=keys[index],
        counters=stores[index],authorize=lambda:clock['live'],clock=lambda:(clock['epoch'],clock['mono']),
        application_deadline_monotonic_us=81000000)
    return build(True,0),build(False,1),context,keys,peers,stores,clock


def ready(p):
    i,r=p[:2]
    first=i.start();second=r.respond(first);i.finish(second)
    third=i.confirm();fourth=r.accept_confirmation(third);i.verify_confirmation(fourth)
    assert i.state==r.state=='ready' and i.handshake_hash==r.handshake_hash
    return first,second,third,fourth


def envelope(p, text='private source fixture'):
    return to_binary(seal_message(p[3][0],sender=p[4][0],recipient=p[4][1],message_id=str(uuid.uuid4()),
        content=text,issued_at=10,expires_at=100).wire)


def test_round_trip_retains_original_ciphertext_signatures_and_deadline(tmp_path):
    p=pair(tmp_path);messages=ready(p);plain=envelope(p)
    assert all(len(m)<=193 for m in messages)  # fitted flow payload, no claim of RF execution
    wire=p[0].seal(plain);opened=p[1].open(wire);assert opened==plain
    result=open_envelope(to_text(opened),p[3][1],expected_sender=p[4][0],expected_recipient=p[4][1],now=20)
    assert result.content=='private source fixture' and result.issued_at==10 and result.expires_at==100
    assert b'private source fixture' not in b''.join(messages)+wire
    assert p[0]._e is p[1]._e is p[0]._s is p[1]._s is None
    assert p[5][0].reserved==2 and p[5][1].reserved==1


@pytest.mark.parametrize('side',[0,1])
def test_no_application_record_before_both_confirmation_phases(tmp_path,side):
    p=pair(tmp_path);i,r=p[:2];h=i.start();i.finish(r.respond(h))
    with pytest.raises(ForwardSessionError):p[side].seal(envelope(p))
    assert p[side].state=='closed'


@pytest.mark.parametrize('offset',[0,4,5,HEADER.size-1,HEADER.size, -1])
def test_tampered_header_counter_context_and_ciphertext_never_advance_replay(tmp_path,offset):
    p=pair(tmp_path);ready(p);wire=bytearray(p[0].seal(envelope(p)));wire[offset]^=1
    before=p[5][1].db.execute('SELECT count(*) FROM received').fetchone()[0]
    with pytest.raises(ForwardSessionError):p[1].open(bytes(wire))
    assert p[5][1].db.execute('SELECT count(*) FROM received').fetchone()[0]==before
    assert p[1].state=='closed'


def test_authenticated_replay_is_rejected_durably(tmp_path):
    p=pair(tmp_path);ready(p);wire=p[0].seal(envelope(p));p[1].open(wire)
    with pytest.raises(ForwardSessionError):p[1].open(wire)
    assert p[5][1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==1


def test_restart_cannot_reclaim_the_same_context_or_reset_nonce_state(tmp_path):
    p=pair(tmp_path);ready(p);p[0].seal(envelope(p));p[0].close()
    store=Counters(tmp_path/'one-i.sqlite')
    with pytest.raises(ForwardSessionError):ForwardSession(context=p[2],initiator=True,signing_key=p[3][0],
        counters=store,authorize=lambda:True,clock=lambda:(20000000,1000000),application_deadline_monotonic_us=81000000)
    assert p[5][0].reserved==2


@pytest.mark.parametrize('mode',['expiry','monotonic','rollback','revoke'])
def test_original_lifetime_and_live_authority_refusals(tmp_path,mode):
    p=pair(tmp_path);ready(p)
    if mode=='expiry':p[6]['epoch']=100000000
    elif mode=='monotonic':p[6]['mono']=81000000
    elif mode=='rollback':p[6]['epoch']-=1
    else:p[6]['live']=False
    with pytest.raises(ForwardSessionError):p[0].seal(envelope(p))
    assert p[5][0].reserved==1 and p[0].state=='closed'


def test_unknown_committed_send_counter_is_retained_and_session_fenced(tmp_path):
    p=pair(tmp_path);ready(p);p[5][0].fail_reserve=True
    with pytest.raises(ForwardSessionError):p[0].seal(envelope(p))
    assert p[5][0].reserved==2 and p[0].state=='closed'


def test_unknown_authenticated_replay_commit_exposes_no_record(tmp_path):
    p=pair(tmp_path);ready(p);wire=p[0].seal(envelope(p));p[5][1].fail_receive=True
    with pytest.raises(ForwardSessionError):p[1].open(wire)
    assert p[5][1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==1
    assert p[1].state=='closed'


def test_peer_identity_and_context_substitution_refused(tmp_path):
    p=pair(tmp_path);other=pair(tmp_path,name='other')
    with pytest.raises(ForwardSessionError):other[1].respond(p[0].start())
    with pytest.raises(ForwardSessionError):ForwardSession(context=p[2],initiator=True,signing_key=SigningKey.generate(),
        counters=Counters(tmp_path/'wrong.sqlite'),authorize=lambda:True,clock=lambda:(20000000,1000000),application_deadline_monotonic_us=81000000)


def test_no_legacy_or_plaintext_fallback(tmp_path):
    p=pair(tmp_path);ready(p)
    with pytest.raises(ForwardSessionError):p[1].open(envelope(p))
    q=pair(tmp_path,name='q');ready(q)
    with pytest.raises(ForwardSessionError):q[0].seal(b'public resource or plaintext')


def test_original_receiver_key_does_not_open_the_outer_capture(tmp_path):
    p=pair(tmp_path);ready(p);inner=envelope(p);outer=p[0].seal(inner)
    # The old sealed record opens with the original recipient key; the newly
    # captured network record cannot be passed to that old opener or SealedBox.
    assert open_envelope(to_text(inner),p[3][1],expected_sender=p[4][0],expected_recipient=p[4][1],now=20).content
    with pytest.raises(Exception):SealedBox(p[3][1].to_curve25519_private_key()).decrypt(outer[HEADER.size:])
    p[0].close();p[1].close();assert p[0]._tx is p[1]._rx is None
    with pytest.raises(ForwardSessionError):p[1].open(outer)


def test_same_session_id_cannot_be_reclaimed_with_extended_expiry(tmp_path):
    from dataclasses import replace
    p=pair(tmp_path);p[0].close();changed=replace(p[2],expires_epoch_us=110000000)
    with pytest.raises(ForwardSessionError):ForwardSession(context=changed,initiator=True,signing_key=p[3][0],
        counters=Counters(tmp_path/'one-i.sqlite'),authorize=lambda:True,clock=lambda:(20000000,1000000),application_deadline_monotonic_us=91000000)


def test_confirmation_requires_the_exact_ed25519_signer_not_only_transport_key(tmp_path):
    p=pair(tmp_path);i,r=p[:2];i.finish(r.respond(i.start()))
    i._signer=SigningKey.generate()  # authenticated transport holder, wrong signer
    before=p[5][1].db.execute('SELECT count(*) FROM received').fetchone()[0]
    with pytest.raises(ForwardSessionError):r.accept_confirmation(i.confirm())
    assert p[5][1].db.execute('SELECT count(*) FROM received').fetchone()[0]==before


def test_foreign_thread_cannot_share_or_dispatch_session_keys(tmp_path):
    import threading
    p=pair(tmp_path);ready(p);errors=[]
    def foreign():
        try:p[0].seal(envelope(p))
        except ForwardSessionError:errors.append(True)
    thread=threading.Thread(target=foreign);thread.start();thread.join(timeout=2)
    assert not thread.is_alive() and errors==[True] and p[0].state=='closed'
    assert p[5][0].reserved==1


def test_model_clock_revocation_after_nonce_commit_keeps_debt_and_fences(tmp_path):
    p=pair(tmp_path);ready(p);original=p[5][0].reserve_transmit
    def changed():
        value=original();p[6]['live']=False;return value
    p[5][0].reserve_transmit=changed
    with pytest.raises(ForwardSessionError):p[0].seal(envelope(p))
    assert p[5][0].reserved==2 and p[0].state=='closed'


@pytest.mark.parametrize('check',[1,2,3])
def test_clock_callback_close_never_returns_ciphertext(tmp_path,check):
    p=pair(tmp_path);ready(p);calls=[]
    def clock():
        calls.append(True)
        if len(calls)==check:p[0].close()
        return p[6]['epoch'],p[6]['mono']
    p[0].clock=clock
    with pytest.raises(ForwardSessionError):p[0].seal(envelope(p))
    assert p[0].state=='closed' and p[5][0].reserved==(1 if check==1 else 2)


@pytest.mark.parametrize('check',[1,2,3])
def test_authorization_callback_close_cannot_return_ciphertext(tmp_path,check):
    p=pair(tmp_path);ready(p);calls=[]
    def authorize():
        calls.append(True)
        if len(calls)==check:p[0].close()
        return True
    p[0].authorize=authorize
    with pytest.raises(ForwardSessionError):p[0].seal(envelope(p))
    assert p[0].state=='closed' and p[5][0].reserved==(1 if check==1 else 2)


def test_counter_callback_reentrant_send_fences_both_operations(tmp_path):
    p=pair(tmp_path);ready(p);original=p[5][0].reserve_transmit;nested=[];plain=envelope(p)
    def reserve():
        n=original()
        # Catching a nested refusal must not let the outer operation continue.
        try:p[0].seal(plain)
        except ForwardSessionError:nested.append('refused')
        return n
    p[5][0].reserve_transmit=reserve
    with pytest.raises(ForwardSessionError):p[0].seal(plain)
    assert nested==['refused'] and p[0].state=='closed' and p[5][0].reserved==2


def test_clock_callback_reentrant_handshake_is_fenced(tmp_path):
    p=pair(tmp_path);nested=[]
    def clock():
        try:p[0].start()
        except ForwardSessionError:nested.append('refused')
        return p[6]['epoch'],p[6]['mono']
    p[0].clock=clock
    with pytest.raises(ForwardSessionError):p[0].start()
    assert nested==['refused'] and p[0].state=='closed' and p[5][0].reserved==0


def test_authenticated_receive_callback_reentrancy_keeps_replay_commit(tmp_path):
    p=pair(tmp_path);ready(p);wire=p[0].seal(envelope(p));original=p[5][1].record_receive;nested=[]
    def accept(n):
        result=original(n)
        try:p[1].open(wire)
        except ForwardSessionError:nested.append('refused')
        return result
    p[5][1].record_receive=accept
    with pytest.raises(ForwardSessionError):p[1].open(wire)
    assert nested==['refused'] and p[1].state=='closed'
    assert p[5][1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==1


def test_cancellation_after_nonce_commit_propagates_and_fences(tmp_path):
    p=pair(tmp_path);ready(p);original=p[5][0].reserve_transmit
    def reserve():
        original();raise KeyboardInterrupt()
    p[5][0].reserve_transmit=reserve
    with pytest.raises(KeyboardInterrupt):p[0].seal(envelope(p))
    assert p[5][0].reserved==2 and p[0].state=='closed'


@pytest.mark.parametrize('phase',['h1','h2'])
@pytest.mark.parametrize('point',[bytes(32),b'\x01'+bytes(31)])
def test_low_order_ephemeral_public_keys_refused(tmp_path,phase,point):
    p=pair(tmp_path);first=p[0].start()
    if phase=='h1':
        packet=first[:HEADER.size]+point+first[HEADER.size+32:];target=p[1]
        with pytest.raises(ForwardSessionError):target.respond(packet)
    else:
        second=p[1].respond(first);packet=second[:HEADER.size]+point+second[HEADER.size+32:];target=p[0]
        with pytest.raises(ForwardSessionError):target.finish(packet)
    assert target.state=='closed' and all(s.reserved==0 for s in p[5])


def test_same_session_id_with_different_application_context_cannot_authenticate(tmp_path):
    from dataclasses import replace
    p=pair(tmp_path);q=pair(tmp_path,context=replace(p[2],binding_digest=b'b'*32),name='different')
    with pytest.raises(ForwardSessionError):q[1].respond(p[0].start())
    assert q[1].state=='closed'


def test_record_limit_includes_confirmations_and_consumed_reservations(tmp_path):
    from dataclasses import replace
    seed=pair(tmp_path,name='seed');p=pair(tmp_path,context=replace(seed[2],session_id=uuid.uuid4().bytes,max_records=2))
    ready(p);plain=envelope(p);assert p[1].open(p[0].seal(plain))==plain
    with pytest.raises(ForwardSessionError):p[0].seal(plain)
    assert p[0].state=='closed' and p[5][0].reserved==3  # the refused reservation stays consumed


def test_closure_after_replay_commit_exposes_no_opened_record(tmp_path):
    p=pair(tmp_path);ready(p);wire=p[0].seal(envelope(p));original=p[5][1].record_receive
    def accept(n):
        result=original(n);p[1].close();return result
    p[5][1].record_receive=accept
    with pytest.raises(ForwardSessionError):p[1].open(wire)
    assert p[1].state=='closed' and p[5][1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==1


def test_noise_kk_known_answer_from_independent_reference(tmp_path,monkeypatch):
    from meshticulum.privacy.forward_session import ForwardContext
    path=Path(__file__).parent/'fixtures/noise-kk-vector.json'
    v=json.loads(path.read_text())
    keys=(SigningKey(bytes.fromhex(v['initiator_signing_seed'])),SigningKey(bytes.fromhex(v['responder_signing_seed'])))
    peers=(Peer('fixture-i',bytes(keys[0].verify_key)),Peer('fixture-r',bytes(keys[1].verify_key)))
    context=ForwardContext(*peers,bytes.fromhex('0102030405060708090a0b0c0d0e0f10'),b'a'*32,10000000,100000000,64)
    assert context.prologue.hex()==v['prologue_hex']
    values=iter([v['initiator_ephemeral'],v['responder_ephemeral']])
    monkeypatch.setattr(X25519PrivateKey,'generate',staticmethod(lambda:X25519PrivateKey.from_private_bytes(bytes.fromhex(next(values)))))
    p=pair(tmp_path,context=context,keys=keys);first=p[0].start();second=p[1].respond(first);p[0].finish(second)
    assert first[HEADER.size:].hex()==v['first_hex'] and second[HEADER.size:].hex()==v['second_hex']
    assert p[0].handshake_hash.hex()==p[1].handshake_hash.hex()==v['handshake_hash_hex']
    assert p[0]._tx.hex()==v['initiator_transmit_key_hex'] and p[0]._rx.hex()==v['initiator_receive_key_hex']
