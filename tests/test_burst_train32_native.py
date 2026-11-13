from pathlib import Path
import subprocess,pytest
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture(scope='module',params=[0,1])
def native(request,tmp_path_factory):
 source=ROOT/'tests/meshticulum_bench_native';out=tmp_path_factory.mktemp('train32')/'native'
 subprocess.run(['clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-g',f'-DMTBENCH_NATIVE_PROFILE={request.param}','-I',str(source),str(source/'train32.cpp'),'-o',str(out)],check=True,capture_output=True,text=True)
 return out
@pytest.mark.parametrize('mode',['capability','late_budget','cancel','thirtytwo'])
def test_exact_native_train32_original_limits(native,mode):
 result=subprocess.run([str(native),mode],capture_output=True,timeout=10)
 assert result.returncode==0,result.stderr.decode()
