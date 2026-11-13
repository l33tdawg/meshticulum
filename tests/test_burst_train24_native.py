"""Real native train24 adapter under sanitizers; no hardware."""
from pathlib import Path
import subprocess
import pytest
ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module', params=[0, 1])
def native(request, tmp_path_factory):
    source = ROOT/'tests/meshticulum_bench_native'
    out = tmp_path_factory.mktemp('train24-native')/'controls'
    subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
        '-fsanitize=address,undefined', '-g', f'-DMTBENCH_NATIVE_PROFILE={request.param}',
        '-I', str(source), str(source/'train24.cpp'), '-o', str(out)],
        check=True, capture_output=True, text=True)
    return out

@pytest.mark.parametrize('mode', ['capability', 'open_binding', 'expiry', 'cancel',
                                  'late_budget', 'twentyfour'])
def test_native_dynamic_train_keeps_original_bounds(native, mode):
    result = subprocess.run([str(native), mode], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
