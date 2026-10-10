# SPDX-License-Identifier: Apache-2.0
"""Real single-file SQLite transactions, synthetic authority; not native policy."""
import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest

from meshticulum.privacy.forward_session import ForwardSessionError, ReceiveCandidate
from tests.test_forward_session import Counters, pair, ready, envelope


class AtomicCounters(Counters):
    """Fixture only: real atomic replay+sealed-record commit, no protected backend."""
    def __init__(self,path):
        super().__init__(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS capsules(binding BLOB,n INTEGER,sealed BLOB,metadata TEXT,PRIMARY KEY(binding,n))')
        self.mode='normal';self.calls=0;self.expected=None;self.after=None

    def commit_receive(self,candidate):
        assert type(candidate) is ReceiveCandidate and candidate.counter_binding==self.binding
        assert candidate.counter>=2 and candidate.sealed_sha256==hashlib.sha256(candidate.sealed_record).digest()
        if self.expected is not None:assert candidate.sealed_sha256==self.expected
        self.calls+=1
        metadata=json.dumps({'context':candidate.context.prologue.hex(),'local_initiator':candidate.local_initiator,
            'handshake_hash':candidate.handshake_hash.hex(),'packet_sha256':candidate.packet_sha256.hex(),
            'deadline':candidate.application_deadline_monotonic_us,'observed_epoch':candidate.observed_epoch_us,
            'observed_monotonic':candidate.observed_monotonic_us},sort_keys=True)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO received VALUES(?,?)',(self.binding,candidate.counter))
            if self.mode=='between':raise OSError('synthetic failure before recovery capsule')
            self.db.execute('INSERT INTO capsules VALUES(?,?,?,?)',(self.binding,candidate.counter,candidate.sealed_record,metadata))
            self.db.execute('COMMIT')
        except sqlite3.IntegrityError:
            self.db.execute('ROLLBACK');return False
        except BaseException:
            self.db.execute('ROLLBACK');raise
        if self.after is not None:self.after()
        if self.mode=='after':raise OSError('synthetic lost return after joint commit')
        return True

    def rows(self):
        return self.db.execute('SELECT n,sealed,metadata FROM capsules WHERE binding=?',(self.binding,)).fetchall()


def setup(tmp_path):
    p=pair(tmp_path,counter_factory=AtomicCounters,receive_mode='commit');ready(p)
    inner=envelope(p);p[5][1].expected=hashlib.sha256(inner).digest()
    return p,inner,p[0].seal(inner)


def test_joint_receive_commits_replay_and_recoverable_sealed_bytes_without_returning_payload(tmp_path):
    p,inner,packet=setup(tmp_path)
    assert p[1].receive_into(packet) is None
    rows=p[5][1].rows();assert len(rows)==1 and rows[0][:2]==(2,inner)
    metadata=json.loads(rows[0][2]);assert metadata['context']==p[2].prologue.hex()
    assert metadata['handshake_hash']==p[1].handshake_hash.hex()
    assert metadata['packet_sha256']==hashlib.sha256(packet).hexdigest()
    assert metadata['deadline']==81000000 and metadata['observed_epoch']==20000000
    assert p[5][1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==1
    p[0].close();p[1].close()
    # Recover only the already committed sealed bytes; no old session key exists.
    recovered=sqlite3.connect(tmp_path/'one-r.sqlite').execute('SELECT sealed FROM capsules WHERE n=2').fetchone()[0]
    assert recovered==inner


@pytest.mark.parametrize('boundary',['between','after'])
def test_interrupted_transaction_has_no_orphan_replay_acceptance(tmp_path,boundary):
    p,inner,packet=setup(tmp_path);p[5][1].mode=boundary
    with pytest.raises(ForwardSessionError):p[1].receive_into(packet)
    assert p[1].state=='closed'
    accepted=p[5][1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]
    assert accepted==len(p[5][1].rows())==(0 if boundary=='between' else 1)
    if boundary=='after':assert p[5][1].rows()[0][1]==inner


def test_duplicate_authenticated_data_never_replaces_recovery_capsule(tmp_path):
    p,inner,packet=setup(tmp_path);p[1].receive_into(packet);before=p[5][1].rows()
    with pytest.raises(ForwardSessionError):p[1].receive_into(packet)
    assert p[1].state=='closed' and p[5][1].rows()==before


@pytest.mark.parametrize('change',['tamper','expiry','revoke'])
def test_failed_authentication_or_original_authority_never_invokes_commit(tmp_path,change):
    p,inner,packet=setup(tmp_path)
    if change=='tamper':packet=packet[:-1]+bytes([packet[-1]^1])
    elif change=='expiry':p[6]['epoch']=p[2].expires_epoch_us
    else:p[6]['live']=False
    with pytest.raises(ForwardSessionError):p[1].receive_into(packet)
    assert p[5][1].calls==0 and not p[5][1].rows()


@pytest.mark.parametrize('change',['close','revoke','cancel'])
def test_post_commit_closure_or_interruption_keeps_joint_recovery_state(tmp_path,change):
    p,inner,packet=setup(tmp_path)
    def after():
        if change=='close':p[1].close()
        elif change=='revoke':p[6]['live']=False
        else:raise KeyboardInterrupt()
    p[5][1].after=after
    with pytest.raises(KeyboardInterrupt if change=='cancel' else ForwardSessionError):p[1].receive_into(packet)
    assert p[1].state=='closed' and p[5][1].rows()[0][1]==inner
    assert p[5][1].db.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]==1


def test_caught_nested_receive_refusal_fences_outer_after_committed_capsule(tmp_path):
    p,inner,packet=setup(tmp_path);nested=[]
    def after():
        try:p[1].receive_into(packet)
        except ForwardSessionError:nested.append(True)
    p[5][1].after=after
    with pytest.raises(ForwardSessionError):p[1].receive_into(packet)
    assert nested==[True] and p[1].state=='closed' and p[5][1].rows()[0][1]==inner


def test_commit_mode_cannot_use_return_only_api(tmp_path):
    p,inner,packet=setup(tmp_path)
    with pytest.raises(ForwardSessionError):p[1].open(packet)
    assert p[1].state=='closed' and not p[5][1].rows()


def test_default_return_mode_cannot_silently_select_commit_api(tmp_path):
    p=pair(tmp_path,counter_factory=AtomicCounters);ready(p);packet=p[0].seal(envelope(p))
    with pytest.raises(ForwardSessionError):p[1].receive_into(packet)
    assert p[1].state=='closed' and p[5][1].calls==0


def test_commit_mode_requires_provider_before_any_claim(tmp_path):
    with pytest.raises(ForwardSessionError):pair(tmp_path,receive_mode='commit')
    db=sqlite3.connect(tmp_path/'one-i.sqlite')
    assert db.execute('SELECT count(*) FROM claims').fetchone()[0]==0


@pytest.mark.parametrize('boundary',['before_commit','after_commit'])
def test_process_death_recovers_both_or_neither_transaction_rows(tmp_path,boundary):
    p,inner,packet=setup(tmp_path);db_path=tmp_path/'one-r.sqlite';binding=p[5][1].binding
    # This crash probe tests the fixture's actual transaction boundary. It does
    # not run another handshake, resume an old key, or qualify power-loss media.
    script='''import sqlite3,sys,os
db=sqlite3.connect(sys.argv[1],isolation_level=None)
db.execute('PRAGMA synchronous=FULL')
binding=bytes.fromhex(sys.argv[2]);sealed=bytes.fromhex(sys.argv[3])
db.execute('BEGIN IMMEDIATE')
db.execute('INSERT INTO received VALUES(?,2)',(binding,))
db.execute('INSERT INTO capsules VALUES(?,2,?,?)',(binding,sealed,'public crash fixture'))
if sys.argv[4]=='after_commit':db.execute('COMMIT')
os._exit(37)
'''
    result=subprocess.run([sys.executable,'-c',script,str(db_path),binding.hex(),inner.hex(),boundary],
                          capture_output=True,timeout=10)
    assert result.returncode==37,result.stderr
    reader=sqlite3.connect(db_path)
    received=reader.execute('SELECT count(*) FROM received WHERE n=2').fetchone()[0]
    capsules=reader.execute('SELECT sealed FROM capsules WHERE n=2').fetchall()
    assert received==len(capsules)==(1 if boundary=='after_commit' else 0)
    if capsules:assert capsules[0][0]==inner
    reader.close();p[0].close();p[1].close()
