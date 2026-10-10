"""Extraction controls: preserved class identities and no optional SDK loading."""
import importlib
from pathlib import Path
import os
import subprocess
import sys


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
        'import meshticulum.privacy.mesh_binary_envelope; '
        'assert not any(n == "sage_sdk" or n.startswith("sage_sdk.") '
        'or n == "sage_lantern" or n.startswith("sage_lantern.") '
        'or n.startswith("meshticulum.integrations.sage.") for n in sys.modules)'],
        cwd=tmp_path, env=os.environ.copy(), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
