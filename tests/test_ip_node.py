"""Finite job controls: real child processes/TCP, stubbed device/root facts.

These exercise source lifecycle, not physical host/native-backend qualification.
"""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest
from meshticulum.bench.services import resource
from meshticulum.contracts.identity import PeerID, SessionKey
from meshticulum.ip import node
from meshticulum.ip.wire import IpGrant, IpLimits
from tests.test_ip_transport import BOOT


def facts(unit):
    return dict(unit=unit, host_boot_id=BOOT, services={'source_model_only': 'no_hardware'}, service_lifetimes={})


def prepare(tmp_path, payload, format='MR1.request'):
    sockets = [socket.socket(), socket.socket()]
    for channel in sockets:
        channel.bind(('127.0.0.1', 0))
    endpoints = [s.getsockname() for s in sockets]
    for channel in sockets:
        channel.close()
    epoch, mono = node.clock()
    peers = [PeerID('ip-node-source', x * 64) for x in ('a', 'b')]
    grant = IpGrant(SessionKey(*peers, b'n' * 16, b'g' * 16), format, len(payload),
        hashlib.sha256(payload).digest(), b'r' * 32, epoch - 1000000, epoch + 30000000,
        epoch + 20000000, *endpoints, IpLimits(), b'k' * 32)
    directories = []
    for index, peer in enumerate(peers):
        directory = tmp_path / str(index)
        directory.mkdir(mode=0o700)
        plan = dict(schema='meshticulum.ip-service-run.v1', host_boot_id=BOOT, local_peer=asdict(peer),
            session=dict(initiator=asdict(peers[0]), responder=asdict(peers[1]),
                         session_id=grant.session.session_id.hex(), generation=grant.session.session_generation.hex()),
            format=format, object=dict(bytes=len(payload), sha256=grant.object_sha256.hex()),
            route_binding_digest=grant.route_binding_digest.hex(), issued_epoch_us=grant.issued_epoch_us,
            expires_epoch_us=grant.expires_epoch_us, application_expires_epoch_us=grant.application_expires_epoch_us,
            application_deadline_monotonic_us=mono + 19000000,
            initiator_address=list(endpoints[0]), responder_address=list(endpoints[1]), limits=asdict(grant.limits))
        for name, raw in [('grant.json', json.dumps(plan).encode()), ('secret.bin', grant.secret), ('active', b'active\n')]:
            node._write(directory / name, raw)
        if index == 0:
            node._write(directory / 'sender.bin', payload)
        directories.append(directory)
    return directories, grant


CHILD = '''import json,sys
from meshticulum.ip import node
node._facts=lambda unit: dict(unit=unit,host_boot_id=sys.argv[3],services={'source_model_only':'no_hardware'},service_lifetimes={})
result=node.run_public_service(sys.argv[1],sys.argv[2])
print(json.dumps(result))
raise SystemExit(0 if result['complete'] else 1)
'''


def start(directory, unit):
    return subprocess.Popen([sys.executable, '-c', CHILD, unit, str(directory), BOOT],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def ready(process, directory):
    bound = time.monotonic() + 4
    while time.monotonic() < bound:
        assert process.poll() is None, process.communicate()[1]
        if (directory / 'socket-ready.json').exists():
            return
        time.sleep(.005)
    raise AssertionError('bounded source listener did not become ready')


def reap(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.communicate(timeout=3)


@pytest.mark.parametrize('format', ['MR1.request', 'MR1.response'])
def test_finite_public_jobs_complete_in_two_real_processes_with_exact_quarantine(tmp_path, format):
    request = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 16384)
    payload = request if format == 'MR1.request' else resource.BlockServer(lambda _: b'x' * 16384).handle(request)
    directories, grant = prepare(tmp_path, payload, format)
    receiver = start(directories[1], 'unit-b')
    sender = None
    try:
        ready(receiver, directories[1])
        sender = start(directories[0], 'unit-a')
        outputs = []
        for process in (sender, receiver):
            out, err = process.communicate(timeout=10)
            assert process.returncode == 0, (out, err)
            outputs.append(json.loads(out))
        for result in outputs:
            assert result['complete'] and result['facts_before'] == result['facts_after']
            assert not result['radio_opened'] and not result['publisher_verified'] and not result['private_admission']
            assert result['original_application_expires_epoch_us'] == grant.application_expires_epoch_us
            assert result['byte_journal']['attempts'] > 0
            assert 'RF_DONE' not in result['byte_journal']['outcomes']
            assert result['byte_journal']['measured_airtime_us'] is None
            assert not result.get('cleanup_errors')
        assert not (directories[0] / 'receiver.bin').exists()
        assert (directories[1] / 'receiver.bin').read_bytes() == payload
        assert outputs[1]['application_status'] == 'public_block_quarantined'
        assert outputs[1]['service_object_durable']
        assert (directories[1] / 'receiver.bin').stat().st_mode & 0o777 == 0o600
    finally:
        reap(sender); reap(receiver)


@pytest.mark.parametrize('failure', ['private', 'malformed', 'wrong_boot', 'expired'])
def test_invalid_native_or_payload_context_refused_before_any_connection(tmp_path, monkeypatch, failure):
    payload = b'private fixture' if failure == 'private' else resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    if failure == 'malformed':
        payload = b'not MR1'
    directories, grant = prepare(tmp_path, payload, 'LME.binary' if failure == 'private' else 'MR1.request')
    monkeypatch.setattr(node, '_facts', facts)
    if failure in ('wrong_boot', 'expired'):
        path = directories[0] / 'grant.json'
        plan = json.loads(path.read_text())
        if failure == 'wrong_boot':
            plan['host_boot_id'] = '31f9c178-6090-40d9-9359-ecf1c7b950b1'
        else:
            plan['application_deadline_monotonic_us'] = 1
        path.write_text(json.dumps(plan))
    connections = []
    monkeypatch.setattr(node, '_connected', lambda *args: connections.append(args))
    result = node.run_public_service('unit-a', directories[0])
    assert not result['complete'] and 'error' in result and not connections
    assert not (directories[0] / 'wire.sqlite').exists()
    assert not (directories[0] / 'bytes.sqlite').exists()
    assert not (directories[0] / 'receiver.bin').exists()


def test_sigterm_closes_the_live_listener_and_leaves_the_original_claim_consumed(tmp_path):
    payload = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    directories, grant = prepare(tmp_path, payload)
    process = start(directories[1], 'unit-b')
    try:
        ready(process, directories[1])
        process.terminate()
        out, err = process.communicate(timeout=3)
        assert process.returncode == 1, (out, err)
        result = json.loads(out)
        assert result['stop_requested'] and not result['complete']
        assert result['byte_journal']['attempts'] == 0 and not result.get('cleanup_errors')
        assert (directories[1] / 'started').read_text().strip() == grant.digest.hex()
        assert not (directories[1] / 'receiver.bin').exists()
        # A new owner can bind the released listening endpoint, but the old
        # grant directory still retains its consumed native single-use claim.
        with socket.socket() as check:
            check.bind(grant.responder_address)
    finally:
        reap(process)


def test_create_only_writer_keeps_original_data_and_closes_owned_directory_on_failure(tmp_path, monkeypatch):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    node._write(directory / 'record', b'original')
    opened, closed = [], []
    original_open, original_close = node.os.open, node.os.close
    def open_fd(*args, **kwargs):
        fd = original_open(*args, **kwargs); opened.append(fd); return fd
    def close_fd(fd):
        closed.append(fd); return original_close(fd)
    monkeypatch.setattr(node.os, 'open', open_fd)
    monkeypatch.setattr(node.os, 'close', close_fd)
    with pytest.raises(FileExistsError):
        node._write(directory / 'record', b'replacement')
    assert opened == closed
    assert (directory / 'record').read_bytes() == b'original'


def test_help_never_opens_network_hardware_or_private_backend():
    result = subprocess.run([sys.executable, '-m', 'meshticulum.ip.node', '--help'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and '--grant-dir' in result.stdout
