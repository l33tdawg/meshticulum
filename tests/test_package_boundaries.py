"""Extraction controls: preserved class identities and no optional SDK loading."""
import importlib
from pathlib import Path
import os
import subprocess
import sys
import pytest


def test_frozen_import_bridges_share_the_canonical_record_types():
    for package, legacy, modules in [
        ('contracts', 'meshticulum_contracts', ('identity', 'carrier', 'negotiation', 'errors')),
        ('scheduler', 'meshticulum_scheduler', ('budget', 'scheduler', 'flow_attempt')),
    ]:
        for child in modules:
            current = importlib.import_module('meshticulum.' + package + '.' + child)
            old = importlib.import_module('meshticulum.' + legacy + '.' + child)
            assert old is current
    from meshticulum.flow.flow_port import FlowKey
    from meshticulum.contracts.identity import FlowKey as Canonical
    assert FlowKey is Canonical


def test_public_and_offline_imports_need_no_parent_checkout_or_sdk(tmp_path):
    result = subprocess.run([sys.executable, '-c',
        'import sys; import meshticulum.contracts.identity, meshticulum.scheduler.scheduler; '
        'import meshticulum.flow.flow_port, meshticulum.bench.services; '
        'import meshticulum.bench.service_runner, meshticulum.resources.lamplighter; '
        'import meshticulum.privacy.mesh_binary_envelope, meshticulum.privacy.forward_session; '
        'assert not any(n == "sage_sdk" or n.startswith("sage_sdk.") '
        'or n == "sage_lantern" or n.startswith("sage_lantern.") '
        'or n.startswith("meshticulum.integrations.sage.") for n in sys.modules)'],
        cwd=tmp_path, env=os.environ.copy(), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_core_runner_refuses_protected_messages_before_a_worker_attempt(tmp_path):
    from meshticulum.bench.services import ServiceTransfer
    from meshticulum.bench.service_runner import ServiceRunner
    from meshticulum.bench.wire import WireError
    from meshticulum.privacy.mesh_binary_envelope import to_binary
    from tests.test_meshticulum_private_carrier import carrier_pair, sealed_message
    from tests.test_meshticulum_bench_runtime import Clock
    from tests.service_helpers import scope
    clock = Clock()
    payload = to_binary(sealed_message(1).wire)
    with carrier_pair(tmp_path / 'pair', payload, clock, 'unit-a', 'unit-b', 211,
                      service=scope('LME.binary', clock)) as pair:
        with pytest.raises(WireError, match='external_application_adapter'):
            ServiceRunner(ServiceTransfer(pair.sender), payload=payload)
        assert not pair.workers[0].sent and not pair.workers[1].sent


def test_core_authority_requires_explicit_radio_admission_before_claim(tmp_path):
    from meshticulum.bench.authority import RunAuthority
    from meshticulum.bench.wire import WireError
    with pytest.raises(WireError, match='explicit_native_radio_admission_required'):
        RunAuthority(tmp_path, expected_unit='example', host_boot_id='unopened',
                     clock=lambda: (0, 0), owner_probe=lambda: True)
    assert not list(tmp_path.iterdir())


def test_distribution_boundary_rejects_reverse_imports_and_fleet_members():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / 'tools/check_repository_boundary.py'
    spec = importlib.util.spec_from_file_location('repository_boundary', path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    with pytest.raises(ValueError, match='application import'):
        tool.check_member('meshticulum/example.py', b'import lantern_meshticulum\n')
    with pytest.raises(ValueError, match='fleet implementation'):
        tool.check_member('meshticulum/bench/pilot_controller.py', b'')
    # Historical signature domains are compatibility bytes, not imports or deployment.
    tool.check_member('meshticulum/crypto.py', b'DOMAIN = b"sage.lantern.mesh-peer.v2"\n')
