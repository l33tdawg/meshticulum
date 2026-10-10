"""Finite CLI source replay with invented management; no device evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tarfile
import io

import pytest
from meshticulum.ip.node import _write
from tests.test_meshticulum_resources import pack
from tests.test_pilot_ssh import config


def load():
    path=Path(__file__).resolve().parents[1]/'firmware/meshticulum-bench/run_public_pilot.py'
    spec=importlib.util.spec_from_file_location('public_pilot_entry',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('fault',[None,'lifecycle','retirement','transfer','late_cleanup'])
def test_complete_entry_preserves_manifest_only_recipient_and_failure_evidence(pack,tmp_path,monkeypatch,fault):
    module=load();cfg,_=config();cfg['handoff_end_epoch_us']=module.time.time_ns()//1000+600000000
    manifest=b'{}';cfg['manifest_sha256']=hashlib.sha256(manifest).hexdigest()
    archive=tmp_path/'runtime.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        item=tarfile.TarInfo('manifest.json');item.size=len(manifest);tar.addfile(item,io.BytesIO(manifest))
    cfg['runtime_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    path=tmp_path/'config.json';_write(path,json.dumps(cfg).encode())
    trust=tmp_path/'trust.json';_write(trust,json.dumps(pack[1]).encode())
    output=tmp_path/'results';seeds=[];calls=[]
    class Operator:
        def __init__(self,*args,**kwargs):self.deadline=module.time.monotonic()+600;self.passwords={};self.jobs={};self.events=[dict(outside_handoff=True)] if fault=='late_cleanup' else [];self.round=0
        def facts(self,unit):
            self.round+=1
            return dict(unit=unit,host_boot_id=cfg['units'][unit]['host_boot_id'],epoch_us=self.round,
                monotonic_us=self.round,services={'source_only':self.round if fault=='lifecycle' else 'stable'})
        def seed(self,packet):seeds.append(packet)
        def reap(self,job):calls.append('reap')
    class Controller:
        def __init__(self,*args,**kwargs):self.links=[];self.expiry=kwargs['expires_epoch_us']
        def mixed_piece(self,request,response):
            if fault=='transfer':raise RuntimeError('source transfer failed')
            return dict(publisher_verified=True)
        def retire(self):
            calls.append('retire')
            return [dict(retirement_error='source-only')] if fault=='retirement' else []
    monkeypatch.setattr(module,'SSHOperator',Operator);monkeypatch.setattr(module,'Controller',Controller)
    monkeypatch.setattr(sys,'argv',['pilot','--config',str(path),'--runtime-archive',str(archive),
        '--pack',str(pack[0]),'--trust',str(trust),'--output',str(output),'--country','MY',
        '--antennas-confirmed','--current-device-handoff-confirmed'])
    if fault:
        with pytest.raises(SystemExit) as caught:module.main()
        assert caught.value.code==1
    else:module.main()
    record=json.loads((output/'RESULT.json').read_bytes())
    assert record['complete'] is (fault is None)
    assert calls==['retire']
    assert seeds[0]['unit']=='unit-b' and len(seeds[0]['files'])==10
    assert seeds[1]['unit']=='unit-c' and set(seeds[1]['files'])=={'manifest.json','manifest.sig.json'}
    assert record['measured_airtime_us'] is None and not record['matched_benchmark'] and not record['private_admission']


def test_help_opens_no_management_or_hardware():
    import subprocess
    path=Path(__file__).resolve().parents[1]/'firmware/meshticulum-bench/run_public_pilot.py'
    result=subprocess.run([sys.executable,str(path),'--help'],capture_output=True,text=True,timeout=5)
    assert result.returncode==0 and '--current-device-handoff-confirmed' in result.stdout


@pytest.mark.parametrize('faster',[True,False])
def test_resume_mode_reports_success_only_after_measured_faster_ip_return(pack,tmp_path,monkeypatch,faster):
    module=load();cfg,_=config();cfg['handoff_end_epoch_us']=module.time.time_ns()//1000+600000000
    raw=b'{}';cfg['manifest_sha256']=hashlib.sha256(raw).hexdigest();archive=tmp_path/'runtime.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        item=tarfile.TarInfo('manifest.json');item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
    cfg['runtime_sha256']=hashlib.sha256(archive.read_bytes()).hexdigest()
    path=tmp_path/'config.json';_write(path,json.dumps(cfg).encode())
    trust=tmp_path/'trust.json';_write(trust,json.dumps(pack[1]).encode())
    class Operator:
        def __init__(self,*a,**k):self.deadline=module.time.monotonic()+600;self.passwords={};self.jobs={};self.events=[]
        def facts(self,unit):return dict(unit=unit,source_only=True)
        def seed(self,packet):pass
    class Controller:
        def __init__(self,*a,**k):self.links=[]
        def failure_resume(self,exchanges,cutoff):
            assert len(exchanges)==4 and cutoff==32
            return dict(faster_ip_observed=faster,returned_to_ip=faster,return_piece=dict(publisher_verified=True) if faster else None)
        def retire(self):return []
    monkeypatch.setattr(module,'SSHOperator',Operator);monkeypatch.setattr(module,'Controller',Controller)
    output=tmp_path/'out'
    monkeypatch.setattr(sys,'argv',['pilot','--config',str(path),'--runtime-archive',str(archive),'--pack',str(pack[0]),
        '--trust',str(trust),'--output',str(output),'--country','MY','--antennas-confirmed','--current-device-handoff-confirmed',
        '--mode','resume','--loss-rx-bytes','32'])
    if faster:module.main()
    else:
        with pytest.raises(SystemExit):module.main()
    result=json.loads((output/'RESULT.json').read_bytes())
    assert result['complete'] is faster and result['resume_sequence']['returned_to_ip'] is faster
