# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Dedicated Linux-entry source replay with modeled hardware, never RF evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

from meshticulum.bench import node
from meshticulum.bench.services import resource
from tests.test_meshticulum_bench_runtime import BOOT, Clock, ModelWorker, provision
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.service_helpers import scope


def isolate_node(monkeypatch, clock):
    original = node.Path.read_text
    monkeypatch.setattr(node.Path, 'read_text', lambda path, *a, **k:
        BOOT if str(path) == '/proc/sys/kernel/random/boot_id' else original(path, *a, **k))
    monkeypatch.setattr(node, 'clock', clock)
    monkeypatch.setattr(node, 'roles', lambda unit: {'source_model_only': 'no_service_change'})


def test_private_service_grant_is_refused_before_radio_or_private_backend(tmp_path, monkeypatch):
    clock = Clock()
    directory = tmp_path / 'grant'
    plan = provision(directory, 'unit-a', b'x', clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('LME.binary', clock)
    (directory / 'grant.json').write_text(json.dumps(plan))
    isolate_node(monkeypatch, clock)
    opened = []
    monkeypatch.setattr(node, '_serial', lambda unit: opened.append(unit))
    result = node.run_public_service('unit-a', directory)
    assert result['complete'] is False
    assert result['error'].endswith('private_service_requires_qualified_native_provider')
    assert not opened and not (directory / 'wire.sqlite').exists()
    assert not (directory / 'receiver.bin').exists()
    assert 'object' not in result and 'grant_context_sha256' not in result


def test_malformed_public_service_payload_is_refused_before_radio(tmp_path, monkeypatch):
    clock = Clock()
    directory = tmp_path / 'grant'
    payload = b'not a resource request'
    plan = provision(directory, 'unit-a', payload, clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('MR1.request', clock)
    (directory / 'grant.json').write_text(json.dumps(plan))
    descriptor = os.open(directory / 'sender.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(payload)
    isolate_node(monkeypatch, clock)
    opened = []
    monkeypatch.setattr(node, '_serial', lambda unit: opened.append(unit))
    result = node.run_public_service('unit-a', directory)
    assert result['complete'] is False and 'resource request' in result['error']
    assert not opened and not (directory / 'wire.sqlite').exists()


def test_public_service_entry_uses_real_flow_pipeline_and_disarms_on_exit(tmp_path, monkeypatch):
    clock = Clock()
    payload = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    with carrier_pair(tmp_path / 'peer', payload, clock, 'unit-a', 'unit-b', 91,
                      service=scope('MR1.request', clock)) as pair:
        # Only the peer receiver runs. The helper's unused sender never emits
        # a nonce/frame; its public deterministic plan provides test inputs.
        directory = tmp_path / 'entry'
        directory.mkdir(mode=0o700)
        for name in ('grant.json', 'secret.bin', 'active'):
            data = (pair.sender.authority.path / name).read_bytes()
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data)
        descriptor = os.open(directory / 'sender.bin', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(payload)
        isolate_node(monkeypatch, clock)
        fd = os.open(tmp_path / 'channel-model', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        channel = SimpleNamespace(fd=fd, lock_fd=fd, close=lambda: os.close(fd))
        class Worker(ModelWorker):
            def __init__(self):
                super().__init__(None, clock)
                self.identity = SimpleNamespace(boot=b'B' * 16, board_id=1, version=node.FIRMWARE)
                self.armed = False
            def arm(self, *, journal, **kwargs):
                self.journal = journal
                self.armed = True
            def disarm(self):
                self.armed = False
                self.owned = False
        worker = Worker()
        worker.peer, pair.workers[1].peer = pair.workers[1], worker
        monkeypatch.setattr(node, '_serial', lambda unit: (channel, worker,
            {'boot_attempts': 0, 'boot_reserved_estimated_airtime_us': 0, 'source_model_only': True}))
        def tick(seconds):
            pair.receiver.step()
            clock.advance(50000)
        monkeypatch.setattr(node.time, 'sleep', tick)
        result = node.run_public_service('unit-a', directory)
        assert result['complete'] is True and result['disarmed_on_exit'] is True
        assert result['publisher_verified'] is False and result['private_admission'] is False
        assert result['service_runner']['state'] == 'wire_complete'
        assert result['attempt_journal']['attempts'] > 0 and not worker.armed
        assert bytes(pair.receiver.received) == payload
        assert not (directory / 'receiver.bin').exists()
        assert (directory / 'result.json').is_file()


def test_quarantine_is_bounded_create_only_and_never_follows_existing_file(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir(mode=0o700)
    node._quarantine(directory, b'public block')
    assert (directory / 'receiver.bin').read_bytes() == b'public block'
    assert (directory / 'receiver.bin').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        node._quarantine(directory, b'replacement')
    assert (directory / 'receiver.bin').read_bytes() == b'public block'


def test_public_receiver_quarantines_exact_flow_bytes_without_seeding(tmp_path, monkeypatch):
    clock = Clock()
    request = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    payload = resource.BlockServer(lambda path: b'public fixture').handle(request)
    with carrier_pair(tmp_path / 'peer', payload, clock, 'unit-a', 'unit-b', 92,
                      service=scope('MR1.response', clock)) as pair:
        directory = tmp_path / 'entry'
        directory.mkdir(mode=0o700)
        for name in ('grant.json', 'secret.bin', 'active'):
            descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write((pair.receiver.authority.path / name).read_bytes())
        isolate_node(monkeypatch, clock)
        fd = os.open(tmp_path / 'channel-model', os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        channel = SimpleNamespace(fd=fd, lock_fd=fd, close=lambda: os.close(fd))
        class Worker(ModelWorker):
            def __init__(self):
                super().__init__(None, clock)
                self.identity = SimpleNamespace(boot=b'C' * 16, board_id=1, version=node.FIRMWARE)
            def arm(self, *, journal, **kwargs):
                self.journal = journal
            def disarm(self):
                self.owned = False
        worker = Worker()
        worker.peer, pair.workers[0].peer = pair.workers[0], worker
        monkeypatch.setattr(node, '_serial', lambda unit: (channel, worker,
            {'boot_attempts': 0, 'boot_reserved_estimated_airtime_us': 0, 'source_model_only': True}))
        pair.sender.send(payload)
        def tick(seconds):
            pair.sender.step()
            clock.advance(50000)
        monkeypatch.setattr(node.time, 'sleep', tick)
        result = node.run_public_service('unit-b', directory)
        assert result['complete'] is True and result['disarmed_on_exit'] is True
        assert result['application_status'] == 'public_block_quarantined'
        assert (directory / 'receiver.bin').read_bytes() == payload
        assert result['service_object_durable'] is True and result['publisher_verified'] is False
        assert 'chunk_durable' not in result and 'verified_chunk_sha256' not in result
        assert not (directory / 'chunks').exists()
    assert (directory / 'receiver.bin').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        node._quarantine(directory, b'replacement')
    assert (directory / 'receiver.bin').read_bytes() == payload


def test_quarantine_refuses_oversized_payload_and_preexisting_symlink(tmp_path):
    directory = tmp_path / 'run'
    directory.mkdir(mode=0o700)
    with pytest.raises(ValueError):
        node._quarantine(directory, b'x' * 16385)
    assert not (directory / 'receiver.bin').exists()
    outside = tmp_path / 'sentinel'
    outside.write_bytes(b'preserved')
    (directory / 'receiver.bin').symlink_to(outside)
    with pytest.raises(FileExistsError):
        node._quarantine(directory, b'replacement')
    assert outside.read_bytes() == b'preserved'


def test_entry_help_has_no_device_or_private_runtime_side_effect():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-m', 'meshticulum.bench.service_node', '--help'],
        cwd=root, env={**os.environ, 'PYTHONPATH': str(root)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and '--grant-dir' in result.stdout


def test_frozen_public_runtime_contains_service_dependencies_and_imports_without_private_home(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'bundle'
    result = subprocess.run([sys.executable, str(root / 'firmware/meshticulum-bench/build_runtime.py'),
        '--output-dir', str(output)], cwd=root, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    record = json.loads((output / 'RUNTIME_BUNDLE.json').read_text())
    assert record['installed'] is False
    manifest = record['manifest']
    assert manifest['private_services_enabled'] is False
    assert manifest['public_service_formats'] == ['MR1.request', 'MR1.response']
    assert manifest['public_ip_service_formats'] == ['MR1.request', 'MR1.response']
    assert 'meshticulum/ip/node.py' in manifest['files']
    assert 'meshticulum/resources/resource.py' in manifest['files']
    assert 'meshticulum/bench/pilot_resource_node.py' in manifest['files']
    assert {'LICENSE', 'NOTICE', 'docs/THIRD_PARTY.md', 'docs/EXTRACTION.md',
            'provenance/UPSTREAM_SOURCE.json', 'provenance/THIRD_PARTY_SOURCE_AUDIT.json'} <= set(manifest['files'])
    assert not any(p.startswith('sage_lantern/') or 'sdk' in p for p in manifest['files'])
    extracted = tmp_path / 'extracted'
    extracted.mkdir()
    with tarfile.open(record['archive'], 'r:gz') as archive:
        archive.extractall(extracted, filter='data')
    assert (extracted / 'LICENSE').read_bytes() == (root / 'LICENSE').read_bytes()
    assert (extracted / 'NOTICE').read_bytes() == (root / 'NOTICE').read_bytes()
    result = subprocess.run([sys.executable, '-c',
        'import sys; from meshticulum.bench import node, services, service_runner; '
        'node.BenchFlow; node.run_public_service; node.BenchFlow.__module__; '
        'from meshticulum.ip import node as ip_node; ip_node.run_public_service; '
        'from meshticulum.bench import pilot_provision, pilot_resources, pilot_resource_node; '
        'assert not any(n == "sage_lantern" or n.startswith("sage_lantern.") '
        'or n == "sage_sdk" or n.startswith("sage_sdk.") for n in sys.modules)'],
        cwd=extracted, env={**os.environ, 'PYTHONPATH': str(extracted)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
