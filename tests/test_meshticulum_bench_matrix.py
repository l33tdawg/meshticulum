# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
import importlib.util
from pathlib import Path
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'firmware/meshticulum-bench/run_matrix.py'
spec = importlib.util.spec_from_file_location('bench_matrix', SOURCE)
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


@pytest.mark.parametrize('failed_endpoint', [1, 2])
def test_failed_provision_cannot_launch_either_radio(failed_endpoint):
    class Fake:
        calls = []

        def commands(self, argv, **kwargs):
            self.calls.extend(argv)
            if len(self.calls) == failed_endpoint:
                raise RuntimeError('provision denied')
            return [{'returncode': 0, 'output': b'{}'}]

    runner = Fake()
    with pytest.raises(RuntimeError, match='provision denied'):
        matrix.provision_and_run(runner, Path('/bench/operator.py'), 'unit-c', 'unit-a')
    assert all(command[2] == 'provision' for command in runner.calls)


def test_management_failure_revokes_both_endpoints_without_retry():
    class Fake:
        calls = []

        def commands(self, argv, **kwargs):
            self.calls.extend(argv)
            if argv[0][2] == 'run':
                raise TimeoutError('lost management')
            return [{'returncode': 0, 'output': b'{}'}]

    runner = Fake()
    with pytest.raises(TimeoutError):
        matrix.provision_and_run(runner, Path('/bench/operator.py'), 'unit-c', 'unit-a')
    assert [(c[2], c[3]) for c in runner.calls] == [
        ('provision', 'unit-a'), ('provision', 'unit-c'),
        ('run', 'unit-a'), ('run', 'unit-c'),
        ('revoke', 'unit-a'), ('revoke', 'unit-c')]


def test_transfer_failure_returns_for_collection_instead_of_retry():
    class Fake:
        def commands(self, argv, **kwargs):
            if argv[0][2] == 'run':
                assert kwargs['check'] is False
                return [{'returncode': 1, 'output': b'{"complete":false}'}]
            return [{'returncode': 0, 'output': b'{}'}]
    result = matrix.provision_and_run(Fake(), Path('/bench/operator.py'), 'unit-c', 'unit-a')
    assert result[0]['returncode'] == 1


def test_password_only_reaches_prompting_child_and_never_output():
    prompt = "import sys; print('[sudo] password for sage:',end='',flush=True); assert sys.stdin.readline().strip()=='dummy-test-value'; print('\\n{\"ok\":true}')"
    no_prompt = "print('{\"other\":true}')"
    result = matrix.Runner('dummy-test-value').commands([
        [sys.executable, '-c', prompt], [sys.executable, '-c', no_prompt]], timeout=5)
    assert matrix.records(result[0]['output']) == [{'ok': True}]
    assert matrix.records(result[1]['output']) == [{'other': True}]
    assert all(b'dummy-test-value' not in row['output'] for row in result)


def test_repeated_sudo_prompt_aborts_without_resending():
    child = "import sys; print('[sudo] password for sage:',end='',flush=True); sys.stdin.readline(); print('[sudo] password for sage:',end='',flush=True); sys.stdin.readline()"
    with pytest.raises(RuntimeError, match='repeated sudo prompt'):
        matrix.Runner('dummy-test-value').commands([[sys.executable, '-c', child]], timeout=5)


def test_output_limit_stops_unbounded_child():
    with pytest.raises(RuntimeError, match='output limit'):
        matrix.Runner(None).commands([[sys.executable, '-c', "print('x'*1000001)"]], timeout=5)
