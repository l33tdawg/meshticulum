from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from meshticulum.bench.authority import RunAuthority, revoke
from meshticulum.bench.services import PreparedBlockExchange, ServiceTransfer, resource
from meshticulum.bench.service_runner import ServiceRunner
from meshticulum.bench.wire import FrameKind, WireError
from meshticulum.contracts.identity import ServiceKind
from meshticulum.contracts.negotiation import Feature, TrafficClass
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision
from tests.test_meshticulum_resources import pack
from tests.service_helpers import scope


def run_pair(sender, receiver, clock, rig=None):
    for _ in range(1600):
        if rig is not None:
            rig.now = clock()[0] / 1000000
        sender.step(); receiver.step()
        clock.advance(50000)
        if sender.wire_complete and receiver.wire_complete:
            if rig is not None:
                rig.now = clock()[0] / 1000000
            return
    raise AssertionError('modeled_service_runner_did_not_complete')


def test_public_runner_uses_same_flow_and_does_not_claim_publisher_verification(tmp_path):
    clock = Clock()
    payload = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    with carrier_pair(tmp_path / 'ab', payload, clock, 'unit-a', 'unit-b', 86,
                      service=scope('MR1.request', clock)) as pair:
        sender = ServiceRunner(ServiceTransfer(pair.sender), payload=payload)
        receiver = ServiceRunner(ServiceTransfer(pair.receiver))
        sender.start(); receiver.start(); run_pair(sender, receiver, clock)
        assert receiver.public_payload == payload
        assert receiver.application_result is None
        assert receiver.snapshot()['service_kind'] == 'knowledge'
        before = pair.receiver.journal.snapshot()
        assert receiver.close() is True
        assert receiver.public_payload is None and pair.receiver.journal.snapshot() == before


def test_public_runner_module_does_not_load_private_runtime_or_sdk():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-c',
        'import sys; from meshticulum.bench.service_runner import ServiceRunner; '
        'assert not any(n == "sage_lantern" or n.startswith("sage_lantern.") '
        'or n == "sage_sdk" or n.startswith("sage_sdk.") for n in sys.modules)'],
        cwd=root, env={**os.environ, 'PYTHONPATH': str(root)}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
