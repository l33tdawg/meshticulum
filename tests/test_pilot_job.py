"""Process-handle controls. Invented facts do not qualify native radio ownership."""
import json
import os
import select
import signal
import subprocess
import sys

import pytest
from meshticulum.bench import pilot_job as job
from meshticulum.bench.wire import WireError

BOOT = '0fe39bce-ccf1-4250-91db-fd8e538392f3'
RUN = 'c' * 32


def prepared(tmp_path, monkeypatch):
    path = tmp_path / 'run'; path.mkdir(mode=0o700)
    monkeypatch.setattr(job, 'kernel', lambda pid: dict(start_ticks=17, state='R', command_sha256='a'*64))
    value = job.record(path, unit='unit-a', boot=BOOT, run_id=RUN, grant_digest='b'*64, transport='lora')
    return path, value


def handles(tmp_path, monkeypatch, *, live=True):
    opened, closed, signals, active = [], [], [], set()
    original_close = os.close
    def open_handle(pid, flags):
        fd = os.open(tmp_path / 'fake-pidfd', os.O_RDONLY | os.O_CREAT, 0o600)
        opened.append(fd); active.add(fd); return fd
    def close(fd):
        if fd in active:
            closed.append(fd); active.remove(fd)
        original_close(fd)
    def send(fd, number):
        signals.append((fd,number))
    class Poll:
        def register(self,fd,events): self.fd=fd
        def poll(self,timeout):
            return [(self.fd,select.POLLIN)] if not live or signals else []
    monkeypatch.setattr(job.os, 'pidfd_open', open_handle, raising=False)
    monkeypatch.setattr(job.os, 'close', close)
    monkeypatch.setattr(job.signal, 'pidfd_send_signal', send, raising=False)
    monkeypatch.setattr(select, 'poll', Poll)
    return opened,closed,signals


def test_live_kernel_handle_is_required_even_with_a_ready_file(tmp_path, monkeypatch):
    path,value=prepared(tmp_path,monkeypatch)
    job.ready(path,value)
    opened,closed,signals=handles(tmp_path,monkeypatch)
    result=job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT)
    assert result['live'] and result['ready'] and result['kernel_handle_opened']
    assert not result['safe_handoff_proven'] and not result['disarm_proven']
    assert opened==closed and not signals


def test_stop_signals_only_the_rechecked_held_pidfd_and_never_claims_disarm(tmp_path,monkeypatch):
    path,value=prepared(tmp_path,monkeypatch)
    opened,closed,signals=handles(tmp_path,monkeypatch)
    result=job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT,stop=True)
    assert signals==[(opened[0],signal.SIGTERM)] and opened==closed
    assert result['stop_signal_sent'] and not result['live'] and not result['safe_handoff_proven']


@pytest.mark.parametrize('changed',['start','command','after_open'])
def test_pid_reuse_or_exec_rebinding_refuses_signal_and_closes_handle(tmp_path,monkeypatch,changed):
    path,value=prepared(tmp_path,monkeypatch)
    opened,closed,signals=handles(tmp_path,monkeypatch)
    calls=[]
    def kernel(pid):
        calls.append(pid)
        return dict(start_ticks=18 if changed=='start' or changed=='after_open' and len(calls)>1 else 17,
            state='R',command_sha256='f'*64 if changed=='command' else 'a'*64)
    monkeypatch.setattr(job,'kernel',kernel)
    with pytest.raises(WireError,match='pilot_process_identity_changed'):
        job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT,stop=True)
    assert not signals and opened==closed


def test_scheduler_state_change_is_not_an_identity_change(tmp_path,monkeypatch):
    path,value=prepared(tmp_path,monkeypatch)
    handles(tmp_path,monkeypatch)
    states=iter(['R','S'])
    monkeypatch.setattr(job,'kernel',lambda pid:dict(start_ticks=17,state=next(states),command_sha256='a'*64))
    assert job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT)['live']


def test_missing_or_terminal_kernel_process_cannot_be_reported_ready(tmp_path,monkeypatch):
    path,value=prepared(tmp_path,monkeypatch)
    job.ready(path,value)
    def gone(pid): raise FileNotFoundError()
    monkeypatch.setattr(job,'kernel',gone)
    result=job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT)
    assert not result['live'] and not result['ready'] and not result['kernel_handle_opened']
    assert not result['safe_handoff_proven']


@pytest.mark.parametrize('change',[{'unit':'unit-b'},{'host_boot_id':'31f9c178-6090-40d9-9359-ecf1c7b950b1'},{'run_id':'d'*32}])
def test_wrong_native_identity_refused_before_process_operation(tmp_path,monkeypatch,change):
    path,value=prepared(tmp_path,monkeypatch)
    value.update(change);(path/'job.json').write_text(json.dumps(value))
    opened,closed,signals=handles(tmp_path,monkeypatch)
    with pytest.raises(WireError,match='invalid_pilot_job_identity'):
        job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT,stop=True)
    assert not opened and not signals


@pytest.mark.skipif(sys.platform!='linux' or not hasattr(os,'pidfd_open'),reason='Linux pidfd control')
def test_actual_linux_child_is_inspected_and_stopped_by_its_held_handle(tmp_path):
    path=tmp_path/'child';path.mkdir(mode=0o700)
    source='''import sys,time
from meshticulum.bench.pilot_job import record
record(sys.argv[1],unit='unit-a',boot=sys.argv[2],run_id=sys.argv[3],grant_digest='b'*64,transport='tcp')
print('recorded',flush=True)
time.sleep(10)
'''
    process=subprocess.Popen([sys.executable,'-c',source,str(path),BOOT,RUN],stdout=subprocess.PIPE)
    try:
        assert select.select([process.stdout],[],[],3)[0]
        assert process.stdout.readline()==b'recorded\n'
        result=job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT)
        assert result['live'] and result['pid']==process.pid and result['kernel_handle_opened']
        stopped=job.inspect(path,unit='unit-a',run_id=RUN,boot=BOOT,stop=True,wait_seconds=2)
        assert stopped['stop_signal_sent'] and not stopped['live']
        assert process.wait(timeout=2)==-signal.SIGTERM
        assert not stopped['safe_handoff_proven']
    finally:
        if process.poll() is None: process.kill();process.wait(timeout=2)
        process.stdout.close()


def test_native_public_retirement_preserves_other_files_and_needs_no_live_role(tmp_path):
    from meshticulum.ip.node import _write
    path=tmp_path/'grant';path.mkdir(mode=0o700)
    provision=dict(schema='meshticulum.provisioned-public-pilot.v1',unit='unit-a',run_id=RUN,transport='tcp')
    grant=dict(schema='meshticulum.ip-service-run.v1',host_boot_id=BOOT,session=dict(session_id=RUN),format='MR1.request')
    for name,value in [('provision.json',provision),('grant.json',grant)]:_write(path/name,json.dumps(value).encode())
    _write(path/'active',b'active\n');_write(path/'unrelated',b'preserve')
    value=job.retire(path,unit='unit-a',run_id=RUN,boot=BOOT)
    assert value['retired'] and not value['debt_refunded'] and not value['safe_handoff_proven']
    assert (path/'active').read_bytes()==b'retired\n' and (path/'unrelated').read_bytes()==b'preserve'


@pytest.mark.parametrize('change',[{'unit':'unit-b'},{'run_id':'d'*32},{'schema':'meshticulum.provisioned-grant.v1'}])
def test_retirement_refuses_foreign_or_legacy_grant_without_modifying_marker(tmp_path,change):
    from meshticulum.ip.node import _write
    path=tmp_path/'grant';path.mkdir(mode=0o700)
    provision=dict(schema='meshticulum.provisioned-public-pilot.v1',unit='unit-a',run_id=RUN,transport='tcp')
    provision.update(change)
    grant=dict(schema='meshticulum.ip-service-run.v1',host_boot_id=BOOT,session=dict(session_id=RUN),format='MR1.request')
    for name,value in [('provision.json',provision),('grant.json',grant)]:_write(path/name,json.dumps(value).encode())
    _write(path/'active',b'active\n')
    with pytest.raises(WireError):job.retire(path,unit='unit-a',run_id=RUN,boot=BOOT)
    assert (path/'active').read_bytes()==b'active\n'
