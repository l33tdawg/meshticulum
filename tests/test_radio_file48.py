"""Explicit48KiB source admission; no device access."""
import copy
import hashlib
import json
import os
import pytest
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('file48_fixture',Path(__file__).with_name('test_radio_file_example.py'))
f=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(f)
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.objects import VerifiedChunks, VerifiedFile48
from meshticulum.bench.wire import WireError
from meshticulum.bench.admission import Gfsk250Admission
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
DATA=bytes(range(256))*192
M=f.example.manifest(DATA,file48=True)
RAW=json.dumps(M).encode();PIN=hashlib.sha256(RAW).hexdigest()
CAP=dict(schema='meshticulum.file48-capability.v1',max_object_bytes=49152,max_total_frames=512,manifest_sha256=PIN)

def plan(clock):
    return dict(schema='meshticulum.bench-run.v1',unit='sender',host_boot_id=f.BOOT,
        local_peer=f.PEERS[0],initiator=f.PEERS[0],responder=f.PEERS[1],session_id='11'*16,
        generation='22'*16,issued_epoch_us=clock()[0],expires_epoch_us=clock()[0]+180000000,
        profile=Gfsk250Profile(921375000,0,180000,512,60000000).to_dict(),radio_modem='gfsk250',
        sender_unit='sender',country='MY',antenna_confirmed=True,max_frames=512,
        object=copy.deepcopy(M['chunks'][0]),file_capability=copy.deepcopy(CAP),
        operation_limits=dict(sender=dict(attempts=290,estimated_airtime_us=18000000),receiver=dict(attempts=192,estimated_airtime_us=9000000)))

def admit(root,p,clock):
    root.mkdir(mode=0o700)
    for name,data in [('grant.json',json.dumps(p).encode()),('secret.bin',b'K'*32),('active',b'active\n')]:
        fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:out.write(data)
    return RunAuthority(root,expected_unit=p['unit'],host_boot_id=f.BOOT,clock=clock,
        owner_probe=lambda:True,radio_admission=Gfsk250Admission('MY',(921375000,),(0,),1000,180000,512,60000000))

@pytest.mark.parametrize('mutation',[
 lambda p:p.pop('file_capability'),lambda p:p.pop('operation_limits'),
 lambda p:p['file_capability'].update(max_object_bytes=True),
 lambda p:p['file_capability'].update(max_total_frames=513),
 lambda p:p['file_capability'].update(manifest_sha256='Z'*64),
 lambda p:p.update(schema='meshticulum.service-run.v1'),
 lambda p:p['object'].update(bytes=49151),lambda p:p['object'].update(chunk_index=True),
 lambda p:p.update(max_frames=513)])
def test_invalid_admission(tmp_path,mutation):
    clock=f.Clock();p=plan(clock);mutation(p)
    with pytest.raises((WireError,ValueError)):admit(tmp_path/'grant',p,clock)

def test_manifest_cache(tmp_path):
    path=tmp_path/'m';path.write_bytes(RAW)
    assert len(f.example.manifest(DATA)['chunks'])==3
    with pytest.raises(WireError):f.example.load_manifest(path,PIN)
    assert f.example.load_manifest(path,PIN,file48=True)==M
    with pytest.raises(WireError):f.example.manifest(DATA[:-1],file48=True)
    with pytest.raises(WireError):f.example.manifest(DATA,file48=1)
    d=M['chunks'][0]
    with pytest.raises(WireError):VerifiedChunks(tmp_path/'old').commit(d,DATA)
    cache=VerifiedFile48(tmp_path/'cache');assert cache.commit(d,DATA)
    assert not VerifiedFile48(tmp_path/'cache').commit(d,DATA)
    assert cache.assemble([d])==DATA
    with pytest.raises(WireError):cache.assemble([d,d])
    cache._paths(d)[2].write_bytes(b'X'*49152)
    with pytest.raises(WireError):cache.get(d)

@pytest.mark.parametrize('field,value',[('bytes',49151),('schema',f.example.FILE_SCHEMA),('chunks',M['chunks']*2)])
def test_noncanonical(tmp_path,field,value):
    m=copy.deepcopy(M);m[field]=value;raw=json.dumps(m).encode();p=tmp_path/'m';p.write_bytes(raw)
    with pytest.raises(WireError):f.example.load_manifest(p,hashlib.sha256(raw).hexdigest(),file48=True)

def test_contract_and_route_immutable(tmp_path):
    clock=f.Clock();a=admit(tmp_path/'grant',plan(clock),clock)
    try:
        assert a.contract.limits.max_queued_bytes==49152
        assert a.contract.limits.max_total_frames==512
        assert a.route_record['file_capability']==CAP
        assert a.operation_limits.attempts==290
        a.plan['file_capability']['manifest_sha256']='0'*64
        with pytest.raises(WireError):a.admitted_service_binding()
    finally:a.close()

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.serial_worker import AttemptResult
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher

class Worker(f.ModelWorker):
    def transmit(self,packet):
        request=self.journal.reserve(packet,12000)
        self.cumulative[0]+=1
        self.journal.settle(request,packet,'RF_DONE','source_radio_model')
        self.sent.append(packet)
        if self.corrupt_once:
            self.corrupt_once=False;packet=packet[:-1]+bytes([packet[-1]^1])
        self.peer.incoming.append(packet)
        return AttemptResult(request,'RF_DONE',12000,None)

@pytest.mark.parametrize('loss,expire',[(False,False),(True,False),(False,True)])
def test_one_operation48k(tmp_path,loss,expire):
    clock=f.Clock();opened=[];transfers=[];workers=[]
    try:
        for unit in ('sender','receiver'):
            p=plan(clock);p['unit']=unit;p['local_peer']=f.PEERS[int(unit=='receiver')]
            root=tmp_path/unit;a=admit(root,p,clock)
            journal=AttemptJournal(root/'attempts.db',binding=bytes(32),max_attempts=a.operation_limits.attempts,
                max_airtime_us=a.operation_limits.estimated_airtime_us,create=True)
            w=Worker(journal,clock,[0]);w.corrupt_once=loss and unit=='sender'
            store=SessionStore(root/'wire.db',a.grant,a.local_peer,boot_id=f.BOOT,clock=clock,create=True)
            cache=VerifiedFile48(tmp_path/'cache')
            window=f.example.bind_file_window(cache,M,p['issued_epoch_us'],p['expires_epoch_us'],time_source=clock,boot_reader=lambda:f.BOOT)
            transfer=f.example.FileTransfer(a,w,journal,SessionCipher(a.grant,a.local_peer,store),
                M['chunks'][0],cache,clock=clock,window=window,chunk=0,confirm_receipt=True)
            transfers.append(transfer);workers.append(w);opened.append((transfer,store,a,journal))
        workers[0].peer,workers[1].peer=workers[1],workers[0]
        transfers[0].send(DATA)
        if expire:
            clock.advance(180000001)
            with pytest.raises(WireError):transfers[0].step()
            assert workers[0].cumulative[0]==0
            assert VerifiedFile48(tmp_path/'cache').get(M['chunks'][0]) is None
        else:
            f.finish(transfers,clock)
            assert bytes(transfers[1].endpoint.received)==DATA
            assert VerifiedFile48(tmp_path/'cache').assemble(M['chunks'])==DATA
            assert 255<=workers[0].cumulative[0]<=290
            if not loss:
                from meshticulum.bench.wire import HEADER, FrameKind
                kinds=[HEADER.unpack(packet[:HEADER.size])[2] for packet in workers[0].sent]
                assert kinds.count(FrameKind.DATA)==255 and kinds.count(FrameKind.CONTROL)==1
                assert workers[0].cumulative[0]==256
            assert transfers[1].endpoint.receipt_confirmed
            assert clock()[0]<p['expires_epoch_us']
            assert len(workers)==2
    finally:f.close_pair(opened)

from types import SimpleNamespace
import subprocess
import sys

@pytest.mark.parametrize('mismatch',['missing','pin'])
def test_transfer_refuses_before_hardware(tmp_path,mismatch,monkeypatch):
    p=plan(f.Clock())
    if mismatch=='missing':p.pop('file_capability')
    else:p['file_capability']['manifest_sha256']='0'*64
    root=tmp_path/'grant';root.mkdir(mode=0o700)
    raw=json.dumps(p).encode();g=root/'grant.json';g.write_bytes(raw);g.chmod(0o600)
    m=tmp_path/'manifest';m.write_bytes(RAW)
    args=SimpleNamespace(manifest=m,manifest_sha256=PIN,file48=True,chunk=0,
        grant_directory=root,grant_sha256=hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(f.example,'ExclusiveSerial',lambda *a,**k:pytest.fail('hardware opened'))
    with pytest.raises(WireError,match='example_file48_'):
        f.example.transfer_chunk(args)


def test_cli_file48_requires_flag_and_exact_size(tmp_path):
    src=tmp_path/'data';src.write_bytes(DATA)
    script=Path(__file__).resolve().parents[1]/'examples/radio_file.py'
    run=lambda *args:subprocess.run([sys.executable,str(script),*map(str,args)],capture_output=True,text=True)
    ordinary=run('manifest',src);special=run('manifest',src,'--file48')
    assert ordinary.returncode==special.returncode==0
    assert len(json.loads(ordinary.stdout)['chunks'])==3
    assert json.loads(special.stdout)==M
    src.write_bytes(DATA[:-1]);assert run('manifest',src,'--file48').returncode!=0

@pytest.mark.parametrize('size',[0,16384,49151,49153,65536])
def test_exact_48_size_only(size):
 with pytest.raises(WireError):f.example.manifest(bytes(size),file48=True)

@pytest.mark.parametrize('file64,file48',[(True,True),(False,1),(1,False),(None,False),(False,None)])
def test_strict_modes(file64,file48):
 with pytest.raises(WireError):f.example.manifest(DATA,file64=file64,file48=file48)

def test_48_64_cache_and_manifest_domain_separation(tmp_path):
 from meshticulum.bench.objects import VerifiedFile64
 old=f.example.manifest(bytes(65536),file64=True);ordinary=f.example.manifest(DATA)
 assert ordinary['schema']==f.example.FILE_SCHEMA and len(ordinary['chunks'])==3
 assert old['schema']==f.example.FILE64_SCHEMA and old['bytes']==65536
 for cache,descriptor,data in [(VerifiedFile64(tmp_path/'same'),M['chunks'][0],DATA),(VerifiedFile48(tmp_path/'same'),old['chunks'][0],bytes(65536))]:
  with pytest.raises(WireError):cache.commit(descriptor,data)
 for flags in ({},{'file64':True},{'file48':True,'file64':True}):
  p=tmp_path/'m';p.write_bytes(RAW)
  with pytest.raises(WireError):f.example.load_manifest(p,PIN,**flags)
 assert VerifiedFile48(tmp_path/'same').commit(M['chunks'][0],DATA)
 assert (tmp_path/'same/file48-v1').is_dir() and (tmp_path/'same/file64-v1').is_dir()

@pytest.mark.parametrize('field,value',[('schema','meshticulum.file64-capability.v1'),('max_object_bytes',49152.0),('max_total_frames',True),('max_object_bytes',65536)])
def test_capability_type_and_cross_mode_refusal(tmp_path,field,value):
 clock=f.Clock();p=plan(clock);p['file_capability'][field]=value
 with pytest.raises((WireError,ValueError)):admit(tmp_path/'grant',p,clock)

def test_cli_modes_mutually_exclusive(tmp_path):
 src=tmp_path/'data';src.write_bytes(DATA);script=Path(__file__).resolve().parents[1]/'examples/radio_file.py'
 r=subprocess.run([sys.executable,str(script),'manifest',str(src),'--file48','--file64'],capture_output=True,text=True)
 assert r.returncode!=0

def test_borrow_cannot_change_file48_mode(monkeypatch,tmp_path):
 monkeypatch.setattr(f.example,'host_boot',lambda:f.BOOT)
 args=SimpleNamespace(interface='hat',device=None,unit='receiver',file48=True,file64=False,hat_spi_hz=1000000)
 owner=f.example.FileHardware(args)
 args.file48=False
 monkeypatch.setattr(f.example,'LinuxHAT',lambda **kw:pytest.fail('hardware opened'))
 with pytest.raises(WireError,match='hardware_binding_changed'):owner.borrow(args,None,None)
