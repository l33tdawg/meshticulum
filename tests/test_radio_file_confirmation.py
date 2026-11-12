"""Opt-in public-file composition on journaled model radios; no RF qualification."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
import pytest
from tests.test_radio_file_example import example,Clock,pair,close_pair,finish
from tests.test_radio_receipt_confirmation import frame_kind,drop_confirmations
from meshticulum.bench.wire import FrameKind


def confirming_pair(tmp_path,monkeypatch,*,enabled=True,mutate=None):
    clock=Clock();payload=b'file context original bytes'*30;file=example.manifest(payload)
    issued,expires=clock()[0],clock()[0]+240000000
    contexts=[]
    def factory(authority,worker,journal,cipher,descriptor,cache,*,clock):
        record=dict(manifest=deepcopy(file),issued_epoch_us=issued,expires_epoch_us=expires,
                    host_boot_id='sender' if authority.sends else 'receiver')
        if mutate is not None:mutate(record,authority.sends)
        def check():
            assert issued<=clock()[0]<expires
        window=SimpleNamespace(record=record,check=check)
        transfer=example.FileTransfer(authority,worker,journal,cipher,descriptor,cache,
            clock=clock,window=window,chunk=0,confirm_receipt=enabled)
        contexts.append(transfer.endpoint._receipt_payload)
        return transfer
    monkeypatch.setattr(example,'ChunkTransfer',factory)
    opened,transfers,workers=pair(tmp_path,file['chunks'][0],clock,issued,expires,[0])
    transfers[0].send(payload)
    return opened,transfers,workers,clock,payload,contexts

@pytest.mark.parametrize('enabled',[False,True])
def test_file_context_both_peers_and_durable_acceptance(tmp_path,monkeypatch,enabled):
    opened,transfers,workers,clock,payload,contexts=confirming_pair(tmp_path,monkeypatch,enabled=enabled)
    observed=[];original=workers[0].transmit
    def transmit(packet):
        if frame_kind(packet) is FrameKind.CONTROL:
            assert transfers[1].completed_at is not None
            assert transfers[1].cache.get(transfers[1].descriptor)==payload
            observed.append(workers[0].journal.snapshot()['attempts'])
        return original(packet)
    workers[0].transmit=transmit
    try:
        finish(transfers,clock)
        assert contexts[0]==contexts[1]
        assert (contexts[0] is not None)==enabled
        assert transfers[1].endpoint.receipt_confirmed is enabled
        assert len(observed)==int(enabled)
        if enabled:assert clock()[1]-transfers[1].completed_at<example.ACK_GRACE_US
        else:assert clock()[1]-transfers[1].completed_at>=example.ACK_GRACE_US
    finally:close_pair(opened)


def test_lost_file_confirmation_preserves_finite_grace_and_paid_attempt(tmp_path,monkeypatch):
    opened,transfers,workers,clock,_,_=confirming_pair(tmp_path,monkeypatch)
    dropped=drop_confirmations(workers[0])
    try:
        finish(transfers,clock)
        assert len(dropped)==1 and not transfers[1].endpoint.receipt_confirmed
        assert clock()[1]-transfers[1].completed_at>=example.ACK_GRACE_US
        assert transfers[0].endpoint.receipt_confirmation_outcome=='RF_DONE'
    finally:close_pair(opened)


def test_distinct_file_context_cannot_retire_receiver(tmp_path,monkeypatch):
    def mutate(record,sends):
        if not sends:record['manifest']['bytes']+=1 # modeled mismatched manifest context
    opened,transfers,workers,clock,_,contexts=confirming_pair(tmp_path,monkeypatch,mutate=mutate)
    try:
        assert contexts[0]!=contexts[1]
        finish(transfers,clock)
        assert not transfers[1].endpoint.receipt_confirmed
        assert clock()[1]-transfers[1].completed_at>=example.ACK_GRACE_US
    finally:close_pair(opened)


def test_failed_durable_commit_cannot_mark_receiver_ready(tmp_path,monkeypatch):
    opened,transfers,workers,clock,_,_=confirming_pair(tmp_path,monkeypatch)
    receiver=transfers[1]
    def fail(*args):raise OSError('modeled durable commit failure')
    monkeypatch.setattr(receiver.cache,'commit',fail)
    try:
        with pytest.raises(OSError,match='durable commit failure'):finish(transfers,clock)
        assert receiver.completed_at is None and not receiver.endpoint._receipt_ready()
        assert not receiver.endpoint.receipt_confirmed
    finally:close_pair(opened)

@pytest.mark.parametrize('field',['issued_epoch_us','expires_epoch_us'])
def test_file_context_must_keep_original_grant_deadlines(field):
    descriptor={'id':'model'};record=dict(manifest={'chunks':[descriptor]},issued_epoch_us=1,expires_epoch_us=100)
    authority=SimpleNamespace(plan=dict(issued_epoch_us=1,expires_epoch_us=100))
    record[field]+=1
    with pytest.raises(Exception,match='file_receipt_context_mismatch'):
        example.FileTransfer(authority,None,None,None,descriptor,None,clock=None,
            window=SimpleNamespace(record=record,check=lambda:None),chunk=0,confirm_receipt=True)

@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('interface',['hat','usb'])
def test_public_file_cli_confirmation_default_off(monkeypatch,capsys,enabled,interface):
    args=['radio_file.py','transfer','--manifest','unused','--manifest-sha256','0'*64,
        '--chunks','unused','--chunk','0','--grant-directory','unused','--grant-sha256','1'*64,
        '--file-issued-epoch-us','1','--file-expires-epoch-us','2','--admission','unused',
        '--admission-sha256','2'*64,'--firmware-source','unused','--firmware-source-sha256','3'*64,
        '--unit','model','--interface',interface]
    if interface=='usb':args+=['--device','/never-open','--usb-serial','model']
    if enabled:args+=['--confirm-receipt']
    monkeypatch.setattr(sys,'argv',args)
    monkeypatch.setattr(example,'transfer_chunk',lambda parsed:dict(confirm_receipt=parsed.confirm_receipt))
    example.main();assert json.loads(capsys.readouterr().out)=={'confirm_receipt':enabled}
