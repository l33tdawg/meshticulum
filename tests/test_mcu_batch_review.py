"""Independent dormant-prototype review; native synthetic state, no devices."""
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def native_review(tmp_path_factory):
    executable=tmp_path_factory.mktemp('mcu-batch-review')/'controls'
    subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror',
        '-fsanitize=address,undefined','-g',
        '-I',str(ROOT/'firmware/meshticulum-bench'),
        '-I',str(ROOT/'tests/meshticulum_bench_native'),
        str(ROOT/'tests/mcu_batch_native/review_tx_batch.cpp'),'-o',str(executable)],
        check=True,capture_output=True,text=True)
    return executable


@pytest.mark.parametrize('case',[
    'overtaken_staging','exact_budget','release_clock_regression',
    'explicit_unknown_late_done','wrong_open_token','late_known_completion',
])
def test_independent_native_batch_edges(native_review,case):
    subprocess.run([str(native_review),case],check=True,capture_output=True,timeout=10)


def test_native_job_and_preflight_memory_are_fixed_and_separately_visible(native_review):
    result=subprocess.run([str(native_review),'sizes'],check=True,capture_output=True,
                          text=True,timeout=10)
    job,queue,boot=map(int,result.stdout.split())
    assert 0<job<=8192 and 0<queue<job and 0<boot<queue
    # These are native ABI sizes, not target stack-headroom qualification.
