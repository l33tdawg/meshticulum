"""Finite native job identity and pidfd stop; no authority from a state file.

Only the current Linux process records its identity after public admission.
Inspection opens the corresponding kernel process handle and rechecks boot,
start ticks and command-line digest. Stop can signal only that held handle.
An absent process never proves radio disarm, debt settlement or safe handoff.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal

from .authority import _open, _private_info
from .wire import require


def kernel(pid):
    raw = (Path('/proc') / str(pid) / 'stat').read_text()
    fields = raw.rsplit(')', 1)[1].split()
    require(len(fields) >= 20, 'invalid_pilot_process_stat')
    return dict(start_ticks=int(fields[19]), state=fields[0],
        command_sha256=hashlib.sha256((Path('/proc') / str(pid) / 'cmdline').read_bytes()).hexdigest())


def record(directory, *, unit, boot, run_id, grant_digest, transport):
    directory = Path(directory)
    _private_info(directory.lstat(), directory=True)
    require(transport in ('lora', 'tcp'), 'invalid_pilot_job_transport')
    process = kernel(os.getpid())
    value = dict(schema='meshticulum.native-public-job.v1', unit=unit,
        host_boot_id=boot, run_id=run_id, grant_context_sha256=grant_digest,
        transport=transport, pid=os.getpid(), start_ticks=process['start_ticks'],
        command_sha256=process['command_sha256'])
    fd = os.open(directory / 'job.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(json.dumps(value, sort_keys=True).encode() + b'\n')
            stream.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try: os.fsync(fd)
    finally: os.close(fd)
    return value


def _identity(directory, unit, run_id, boot):
    directory = Path(directory)
    _private_info(directory.lstat(), directory=True)
    fd = _open(directory / 'job.json')
    try: raw = os.read(fd, 4097)
    finally: os.close(fd)
    require(len(raw) <= 4096, 'pilot_job_identity_too_large')
    value = json.loads(raw)
    require(type(value) is dict and set(value) == {'schema', 'unit', 'host_boot_id', 'run_id',
        'grant_context_sha256', 'transport', 'pid', 'start_ticks', 'command_sha256'}
        and value['schema'] == 'meshticulum.native-public-job.v1'
        and value['unit'] == unit and value['run_id'] == run_id and value['host_boot_id'] == boot
        and value['transport'] in ('lora', 'tcp') and type(value['pid']) is int and value['pid'] > 1
        and type(value['start_ticks']) is int and value['start_ticks'] > 0, 'invalid_pilot_job_identity')
    require(all(type(value[k]) is str and re.fullmatch('[0-9a-f]{64}',value[k])
                for k in ('command_sha256','grant_context_sha256')), 'invalid_pilot_job_digest')
    return value


def retire(directory, *, unit, run_id, boot):
    """Retire only this native-provisioned public child, even after role changes."""
    directory = Path(directory)
    _private_info(directory.lstat(), directory=True)
    values = {}
    for name in ('grant.json','provision.json'):
        fd = _open(directory / name)
        try: raw=os.read(fd,16385)
        finally: os.close(fd)
        require(len(raw)<=16384, 'pilot_retire_record_too_large')
        values[name]=json.loads(raw)
    provision, plan = values['provision.json'], values['grant.json']
    require(provision['schema']=='meshticulum.provisioned-public-pilot.v1'
            and provision['unit']==unit and provision['run_id']==run_id and plan['host_boot_id']==boot,
            'pilot_retire_requires_this_native_public_child')
    if provision['transport']=='tcp':
        require(plan['schema']=='meshticulum.ip-service-run.v1' and plan['session']['session_id']==run_id
                and plan['format'] in ('MR1.request','MR1.response'), 'pilot_retire_private_or_wrong_session')
        from ..ip.authority import revoke
    else:
        require(provision['transport']=='lora' and plan['schema']=='meshticulum.service-run.v1'
                and plan['unit']==unit and plan['session_id']==run_id
                and plan['service']['format'] in ('MR1.request','MR1.response'), 'pilot_retire_private_or_wrong_session')
        from .authority import revoke
    revoke(directory)
    return dict(schema='meshticulum.retired-public-pilot.v1', unit=unit, run_id=run_id,
        retired=True, debt_refunded=False, safe_handoff_proven=False)


def ready(directory, identity):
    """Published by the admitted radio job after ARM and flow start."""
    path = Path(directory) / 'radio-ready.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(json.dumps(dict(identity, status='radio-flow-ready'), sort_keys=True).encode() + b'\n')
            stream.flush(); os.fsync(fd)
    finally: os.close(fd)


def _ready(directory, value):
    name = 'radio-ready.json' if value['transport'] == 'lora' else 'socket-ready.json'
    try: fd = _open(Path(directory) / name)
    except FileNotFoundError: return False
    try: raw = os.read(fd, 4097)
    finally: os.close(fd)
    require(len(raw) <= 4096, 'pilot_job_ready_too_large')
    data = json.loads(raw)
    require(data['grant_context_sha256'] == value['grant_context_sha256'], 'pilot_ready_binding_changed')
    if value['transport'] == 'lora':
        require(all(data[k] == v for k,v in value.items()) and data['status'] == 'radio-flow-ready',
                'pilot_ready_identity_changed')
    else:
        require(data['schema'] == 'meshticulum.ip-socket-ready.v1' and data['status'] == 'listening',
                'pilot_socket_not_ready')
    return True


def _handle(value):
    try:
        before = kernel(value['pid'])
    except (FileNotFoundError, ProcessLookupError):
        return None
    require(before['start_ticks'] == value['start_ticks']
            and (before['state'] == 'Z' or before['command_sha256'] == value['command_sha256']),
            'pilot_process_identity_changed')
    try:
        fd = os.pidfd_open(value['pid'], 0)
    except ProcessLookupError:
        return None
    try:
        after = kernel(value['pid'])
        require(after['start_ticks'] == value['start_ticks']
                and (after['state'] == 'Z' or after['command_sha256'] == value['command_sha256']),
                'pilot_process_identity_changed')
        return fd
    except (FileNotFoundError, ProcessLookupError):
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise


def inspect(directory, *, unit, run_id, boot, stop=False, wait_seconds=5):
    require(type(stop) is bool and type(wait_seconds) in (int, float) and 0 < wait_seconds <= 5,
            'invalid_pilot_process_operation')
    try:
        value = _identity(directory, unit, run_id, boot)
    except FileNotFoundError:
        return dict(schema='meshticulum.native-public-job-status.v1',unit=unit,run_id=run_id,
            host_boot_id=boot,job_record_found=False,kernel_handle_opened=False,live=None,ready=False,
            stop_signal_sent=False,disarm_proven=False,safe_handoff_proven=False)
    fd = _handle(value)
    result = dict(schema='meshticulum.native-public-job-status.v1', unit=unit,
        run_id=run_id, host_boot_id=boot, pid=value['pid'], start_ticks=value['start_ticks'],
        transport=value['transport'], grant_context_sha256=value['grant_context_sha256'],
        job_record_found=True,kernel_handle_opened=fd is not None, live=False, stop_signal_sent=False,
        ready=False, disarm_proven=False, safe_handoff_proven=False)
    if fd is None: return result
    try:
        import select
        poll = select.poll(); poll.register(fd, select.POLLIN)
        result['live'] = not bool(poll.poll(0))
        if result['live']: result['ready'] = _ready(directory, value)
        if stop and result['live']:
            # pidfd prevents a later PID reuse from receiving our signal.
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            result['stop_signal_sent'] = True
            poll.poll(int(wait_seconds * 1000))
            result['live'] = not bool(poll.poll(0))
            if not result['live']: result['ready'] = False
        return result
    finally:
        os.close(fd)
