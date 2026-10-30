"""Production twelve-frame/eight-slot native path, sanitizers and no devices."""
from pathlib import Path
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module',params=[0,1])
def native(request,tmp_path_factory):
    source=ROOT/'tests/meshticulum_bench_native';out=tmp_path_factory.mktemp('train12-native')/'controls'
    subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g',
        f'-DMTBENCH_NATIVE_PROFILE={request.param}','-I',str(source),str(source/'train12.cpp'),'-o',str(out)],
        check=True,capture_output=True,text=True)
    return out

@pytest.mark.parametrize('mode',['capability','invalid_admission','open_binding','singleton','expiry',
                                  'rx_failure','cancel','suffix','twelve'])
def test_native_train12_bounds(native,mode):
    r=subprocess.run([str(native),mode],capture_output=True,timeout=10)
    assert r.returncode==0,r.stderr.decode()
