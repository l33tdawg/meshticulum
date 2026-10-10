# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Operator/analysis source fixtures only. No result here is hardware evidence."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper(name):
    path = ROOT / 'firmware/meshticulum-bench' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('unit,expected', [('unit-a', '/root/esp-venv-r3/bin/python'),
    ('unit-b', '/root/esp-venv-r3/bin/python'), ('unit-c', '/usr/bin/python3')])
@pytest.mark.parametrize('action', ['provision', 'run', 'revoke'])
def test_generated_operator_uses_verified_interpreter_and_pinned_management(tmp_path, monkeypatch, unit, expected, action):
    prepare = helper('prepare_run')
    for name in ('grant_bootstrap.py', 'collect_run.py'):
        (tmp_path / name).write_bytes((ROOT / 'firmware/meshticulum-bench' / name).read_bytes())
    code = prepare.OPERATOR.format(rows={unit: {'archive': '/private/grant.json', 'sha256': 'a' * 64,
        'ssh': ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', 'pinned-host']}},
        run='b' * 32, runtime='c' * 64, manifest='d' * 64,
        bootstrap=hashlib.sha256((tmp_path / 'grant_bootstrap.py').read_bytes()).hexdigest(),
        collector=hashlib.sha256((tmp_path / 'collect_run.py').read_bytes()).hexdigest())
    class Captured(Exception): pass
    captured = []
    def execvp(name, values): captured.append((name, values)); raise Captured()
    monkeypatch.setattr(os, 'execvp', execvp)
    monkeypatch.setattr(sys, 'argv', [str(tmp_path / 'operator.py'), action, unit])
    with pytest.raises(Captured):
        exec(compile(code, str(tmp_path / 'operator.py'), 'exec'), {'__file__': str(tmp_path / 'operator.py')})
    name, argv = captured[0]
    assert name == argv[0] == 'ssh' and 'StrictHostKeyChecking=yes' in argv
    remote = shlex.split(argv[-1])
    assert remote[:2] == (['sudo', expected] if unit != 'unit-c' else [expected, '-I'])
    assert '-I' in remote and '-B' in remote
    assert argv[1] == ('-tt' if unit != 'unit-c' else '-T')


@pytest.mark.parametrize('unit', ['unit-a', 'unit-b', 'unit-c'])
def test_collection_saves_only_bound_json_while_sudo_chatter_remains_visible(tmp_path, monkeypatch, unit):
    import subprocess
    prepare = helper('prepare_run')
    source = (ROOT / 'firmware/meshticulum-bench/collect_run.py').read_bytes()
    (tmp_path / 'collect_run.py').write_bytes(source)
    run = 'b' * 32
    code = prepare.OPERATOR.format(rows={unit: {'ssh': ['ssh', '-T', '-o', 'StrictHostKeyChecking=yes', 'pinned']}},
        run=run, runtime='c' * 64, manifest='d' * 64, bootstrap='unused',
        collector=hashlib.sha256(source).hexdigest())
    data = {'schema': 'meshticulum.collected-run.v1', 'unit': unit, 'run_id': run}
    stream = tmp_path / 'terminal-fixture.txt'
    stream.write_text('[sudo] password for source-fixture-user: \n' + json.dumps(data) + '\n')
    captured = []

    class Process:
        def __init__(self, argv, **kwargs):
            captured.append(argv)
            self.stdout = stream.open('rb')
        def wait(self): self.stdout.close(); return 0

    monkeypatch.setattr(subprocess, 'Popen', Process)
    monkeypatch.setattr(sys, 'argv', [str(tmp_path / 'operator.py'), 'collect', unit])
    exec(compile(code, 'operator.py', 'exec'), {'__file__': str(tmp_path / 'operator.py')})
    assert json.loads((tmp_path / (unit + '-collected.json')).read_text()) == data
    remote = shlex.split(captured[0][-1])
    expected = '/usr/bin/python3' if unit == 'unit-c' else '/root/esp-venv-r3/bin/python'
    assert remote[:2] == ([expected, '-I'] if unit == 'unit-c' else ['sudo', expected])
    assert captured[0][1] == ('-T' if unit == 'unit-c' else '-tt')


def test_multichunk_manifest_stays_stable_across_fresh_sessions_without_receiver_payload(tmp_path):
    objects, prepare = helper('make_object'), helper('prepare_run')
    chunks = [b'a' * 100, b'b' * 71]
    manifest = objects.manifest_for(chunks)
    for i, value in enumerate(chunks): (tmp_path / ('%02d.bin' % i)).write_bytes(value)
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(manifest))
    peers = {u: {'chain_id': 'operator-source-test', 'agent_id': c * 64}
             for u, c in [('unit-c', 'a'), ('unit-a', 'b')]}
    common = {'now_us': 1000000000, 'boot_ids': {u: '21f9c178-6090-40d9-9359-ecf1c7b950b1' for u in peers}, 'peers': peers}
    ids = []
    for index in (0, 1, 1):
        loaded, payload = objects.load_chunk(path, index)
        identifier, packages = prepare.packages('unit-c', 'unit-a', **common, payload=payload,
            descriptor=loaded['chunks'][index], object_manifest=loaded)
        ids.append(identifier)
        assert packages['unit-a']['payload'] is None
        assert packages['unit-a']['object_manifest'] == manifest
        assert packages['unit-c']['grant']['object']['object_id'] == manifest['object_id']
    assert len(set(ids)) == 3
    (tmp_path / '01.bin').write_bytes(b'changed')
    with pytest.raises(ValueError, match='synthetic_chunk_changed'): objects.load_chunk(path, 0)


def test_relay_package_never_supplies_payload_to_either_endpoint():
    objects, prepare = helper('make_object'), helper('prepare_run')
    manifest = objects.manifest_for([b'first', b'second'])
    peers = {u: {'chain_id': 'operator-source-test', 'agent_id': c * 64}
             for u, c in [('unit-a', 'a'), ('unit-b', 'b')]}
    _, packages = prepare.packages('unit-a', 'unit-b', now_us=1000000000,
        boot_ids={u: '21f9c178-6090-40d9-9359-ecf1c7b950b1' for u in peers}, peers=peers,
        descriptor=manifest['chunks'][1], object_manifest=manifest)
    assert all(value['payload'] is None for value in packages.values())
    assert packages['unit-a']['reuse_verified_chunk'] is True
    assert packages['unit-b']['reuse_verified_chunk'] is False


def evidence():
    # Deliberately fabricated arithmetic fixture: never saved as a bench run.
    obj = {'object_id': 'e' * 64, 'sha256': 'e' * 64, 'chunk_index': 0, 'bytes': 100}
    plan = {'sender': 'unit-c', 'receiver': 'unit-a', 'run_id': 'a' * 32, 'object': obj,
        'runtime_archive_sha256': 'b' * 64, 'runtime_manifest_sha256': 'c' * 64,
        'profile': {'fixture_only': True}, 'staged': {u: {'sha256': u} for u in ('unit-c', 'unit-a')}}
    collections = {}
    for unit, sender in [('unit-c', True), ('unit-a', False)]:
        kind, opposite = ('DATA', 'ACK') if sender else ('ACK', 'DATA')
        row = {'schema': 'meshticulum.collected-run.v1', 'unit': unit, 'run_id': plan['run_id'],
            'runtime_archive_sha256': plan['runtime_archive_sha256'],
            'runtime_manifest_sha256': plan['runtime_manifest_sha256'], 'attempt_snapshot_stable': True,
            'grant': {'unit': unit, 'session_id': plan['run_id'], 'profile': plan['profile'], 'object': obj,
                      'sender_unit': plan['sender'], 'host_boot_id': 'source-fixture-boot'},
            'provision': {'unit': unit, 'run_id': plan['run_id'], 'package_sha256': unit, 'object': obj,
                          'receiver_payload_installed': False},
            'attempts': [{'request': 1, 'packet_sha256': kind, 'bytes': 100, 'reserved_estimated_us': 10000,
                'outcome': 'RF_DONE', 'driver_evidence': {'irq': 1, 'error': 0, 'driver_elapsed_us': 5000}}],
            'result': {'unit': unit, 'session_id': plan['run_id'], 'host_boot_id': 'source-fixture-boot',
                'object': obj, 'sends': sender, 'grant_context_sha256': 'same-source-fixture', 'complete': True,
                'disarmed_on_exit': True, 'start_monotonic_us': 1000000, 'flow_complete_monotonic_us': 3000000,
                'chunk_durable': not sender, 'verified_chunk_sha256': obj['sha256'],
                'flow': {'received_bytes': 0 if sender else 100, 'acked_frames': 1 if sender else 0,
                    'events': [{'event': 'tx', 'outer_kind': kind, 'outcome': 'RF_DONE', 'packet_sha256': kind},
                               {'event': 'rx', 'outer_kind': opposite, 'packet_sha256': opposite}]}}}
        collections[unit] = row
    return plan, collections


def test_analysis_counts_failure_and_keeps_uninstrumented_quantities_null():
    analyze = helper('analyze_runs')
    plan, collected = evidence()
    success = analyze.one(plan, collected)
    assert success['sender_start_to_flow_ack_seconds'] == 2
    assert success['sender_acknowledged_goodput_bytes_per_second'] == 50
    collected['unit-c']['result']['complete'] = False
    collected['unit-c']['result']['error'] = 'interrupted-source-fixture'
    failure = analyze.one(plan, collected)
    summary = analyze.aggregate([success, failure])
    assert summary['completion_fraction_of_recorded_runs'] == .5
    assert summary['sender_start_to_flow_ack_seconds']['n'] == 1
    assert summary['measured_energy_joules'] is summary['measured_airtime_seconds'] is None
    assert analyze.aggregate([])['sender_start_to_flow_ack_seconds'] is None


@pytest.mark.parametrize('fault,reason', [('wrong_pin', 'runtime_pin_mismatch'),
    ('wrong_object', 'wrong_observed_session'), ('missing_driver', 'rf_done_without_driver_evidence'),
    ('ip_payload', 'receiver_payload_copied_over_ip'), ('missing_path', 'correlated_rf_path'),
    ('too_few_frames', 'correlated_rf_path'), ('unknown', 'unknown_attempt'),
    ('missing_commit', 'verified_chunk'), ('missing_stored_chunk', 'chunk_not_present')])
def test_analysis_refuses_success_from_incomplete_or_conflicting_evidence(fault, reason):
    analyze = helper('analyze_runs')
    plan, collected = evidence()
    tx, rx = collected['unit-c'], collected['unit-a']
    if fault == 'wrong_pin': tx['runtime_manifest_sha256'] = 'wrong'
    if fault == 'wrong_object': rx['result']['object'] = dict(plan['object'], bytes=99)
    if fault == 'missing_driver': tx['attempts'][0]['driver_evidence'] = 'source_chip_model'
    if fault == 'ip_payload': rx['provision']['receiver_payload_installed'] = True
    if fault == 'missing_path': rx['result']['flow']['events'][1]['packet_sha256'] = 'not-the-tx-packet'
    if fault == 'too_few_frames': tx['result']['flow']['acked_frames'] = 0
    if fault == 'unknown':
        tx['attempts'].append({'request': 2, 'packet_sha256': 'other', 'bytes': 100,
            'reserved_estimated_us': 10000, 'outcome': 'UNKNOWN', 'driver_evidence': 'timeout'})
    if fault == 'missing_commit': rx['result']['chunk_durable'] = False
    if fault == 'missing_stored_chunk':
        plan['object_manifest'] = {'source_fixture': True}
        rx['object_manifest'] = plan['object_manifest']
        rx['object_receipt'] = {'object_id': plan['object']['object_id'], 'verified_chunks': []}
    with pytest.raises(ValueError, match=reason): analyze.one(plan, collected)
