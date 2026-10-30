"""Actual eight-frame firmware path under deterministic source-only faults."""
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module', params=[0, 1])
def eight_native(request, tmp_path_factory):
    source = ROOT / 'tests/meshticulum_bench_native'
    output = tmp_path_factory.mktemp('eight-native') / 'controls'
    subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
        '-fsanitize=address,undefined', '-g', f'-DMTBENCH_NATIVE_PROFILE={request.param}',
        '-I', str(source), str(source/'eight.cpp'), '-o', str(output)],
        check=True, capture_output=True, text=True)
    return output


@pytest.mark.parametrize('case', ['invalid_depth', 'capability', 'full', 'wrong_profile', 'open_replay',
    'underfill', 'output', 'expiry', 'expired_suffix', 'rx_failure', 'cancel'])
def test_scalar_eight_native_lifecycle(eight_native, case):
    result = subprocess.run([str(eight_native), case], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
