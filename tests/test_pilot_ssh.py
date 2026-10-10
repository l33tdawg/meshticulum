"""Management integrity controls; local children do not qualify devices."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from meshticulum.bench.pilot_ssh import Job,SSHOperator,PROMPT,one_record
from meshticulum.bench.wire import WireError


def config():
    source=b'print("test bootstrap")'
    result=dict(schema='meshticulum.finite-ssh-pilot.v1',runtime_sha256='a'*64,manifest_sha256='b'*64,
        provision_sha256=hashlib.sha256(source).hexdigest(),runtime_bootstrap_sha256='c'*64,
        handoff_end_epoch_us=1200000000,port_base=35000,units={})
    for i,unit in enumerate(('unit-a','unit-b','unit-c'),1):
        result['units'][unit]=dict(ssh=['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','host-'+str(i)],
            python='/usr/bin/python3',sudo=unit!='unit-c',host_boot_id='00000000-0000-4000-8000-'+str(i).zfill(12),ip_address='192.0.2.'+str(i))
    return result,source


def operator(value=None):
    cfg,source=config()
    return SSHOperator(value or cfg,provision_source=source,clock=lambda:10,epoch=lambda:1000000000)


def test_constructor_connects_nothing_and_allocates_distinct_exact_endpoints(monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('unexpected process'))
    op=operator()
    assert op.endpoints('unit-a','unit-b')=={'unit-a':('192.0.2.1',35000),'unit-b':('192.0.2.2',35001)}
    assert op.endpoints('unit-b','unit-a')=={'unit-b':('192.0.2.2',35002),'unit-a':('192.0.2.1',35003)}


@pytest.mark.parametrize('fault',['pin','window','hostkey','proxy','duplicate'])
def test_invalid_target_authority_or_source_pins_refused_before_connection(fault):
    cfg,source=config()
    if fault=='pin':cfg['provision_sha256']='d'*64
    if fault=='window':cfg['handoff_end_epoch_us']=900000000
    if fault=='hostkey':cfg['units']['unit-a']['ssh'][5]='StrictHostKeyChecking=no'
    if fault=='proxy':cfg['units']['unit-a']['ssh'][5]='ProxyCommand=unsafe'
    if fault=='duplicate':cfg['units']['unit-a']['ssh'][-1:-1]=['-o','StrictHostKeyChecking=no']
    with pytest.raises(WireError):operator(cfg)


def test_commands_pin_manifest_and_choose_tty_only_for_sudo():
    op=operator()
    root=op._argv('unit-a',['/usr/bin/python3','-I','-c','safe source'],root=True)
    assert root[1]=='-tt' and 'sudo' in root[-1] and '-n' in root[-1]
    stage=op._argv('unit-a',['python3','-I','-c','safe source'],root=False)
    assert stage[1]=='-T' and 'sudo' not in stage[-1]
    assert 'ControlMaster=no' in root and 'ForwardAgent=no' in root


def test_one_record_rejects_ambiguous_or_unexpected_schema():
    with pytest.raises(WireError):one_record(b'{"schema":"x"}\n{"schema":"x"}\n','x')
    with pytest.raises(WireError):one_record(b'{"schema":"other"}\n','x')
    assert one_record(PROMPT+b'\r\n{"schema":"x","ok":true}\n','x')['ok']


def test_password_reaches_only_one_prompt_and_is_never_retained_in_output():
    code="import sys;print('MESHTICULUM_SUDO_PROMPT:',end='',flush=True);assert sys.stdin.readline().strip()=='source-only-dummy';print('\\n{\"schema\":\"test\"}')"
    job=Job([sys.executable,'-c',code],password='source-only-dummy',deadline=time.monotonic()+3)
    try:
        assert job.wait()==0
        assert job.prompted and b'source-only-dummy' not in job.output
    finally:job.close()
    assert job.password is None


@pytest.mark.parametrize('failure',['repeated','echo','timeout'])
def test_bad_authentication_or_stalled_owned_child_is_reaped(failure):
    if failure=='repeated':code="import sys;print('MESHTICULUM_SUDO_PROMPT:',end='',flush=True);sys.stdin.readline();print('MESHTICULUM_SUDO_PROMPT:',end='',flush=True);sys.stdin.readline()"
    elif failure=='echo':code="print('source-only-dummy',flush=True)"
    else:code="import time;time.sleep(10)"
    job=Job([sys.executable,'-c',code],password='source-only-dummy',deadline=time.monotonic()+(.1 if failure=='timeout' else 3))
    try:
        with pytest.raises((WireError,RuntimeError)):job.wait()
    finally:job.close()
    assert job.process.poll() is not None and job.password is None
    if failure=='echo':assert not job.output
