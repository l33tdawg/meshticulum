"""Current source stays separate when reviewed extraction history is retained."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def test_current_boundary_and_explicit_strict_history(tmp_path, monkeypatch, capsys):
    tools = Path(__file__).resolve().parents[1] / 'tools'
    monkeypatch.syspath_prepend(str(tools))
    spec = importlib.util.spec_from_file_location('history_boundary_control', tools / 'check_public_history.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.ROOT = tmp_path

    def git(*args):
        subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)

    def commit(message):
        git('add', '-A')
        git('commit', '-qm', message)

    git('init', '-q', '-b', 'main')
    git('config', 'user.name', 'Boundary fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    old = tmp_path / 'src/meshticulum/integrations/sage/adapter.py'
    old.parent.mkdir(parents=True)
    old.write_text('import sage_sdk\n')
    commit('Historical extraction fixture')
    monkeypatch.setattr(sys, 'argv', ['check_public_history'])
    with pytest.raises(ValueError):
        checker.main()

    old.unlink()
    current = tmp_path / 'src/meshticulum/resources/example.py'
    current.parent.mkdir(parents=True)
    current.write_text('VALUE = 1\n')
    commit('Keep current source reusable')
    checker.main()
    report = json.loads(capsys.readouterr().out)
    assert report['current_tree_boundary_passed'] is True
    assert report['historical_product_file_versions'] == 1
    assert report['historical_lantern_code_retained'] is True
    assert report['github_cached_views_verified'] is False

    monkeypatch.setattr(sys, 'argv', ['check_public_history', '--strict-history'])
    with pytest.raises(ValueError):
        checker.main()

    current.write_text('import lantern_meshticulum\n')
    commit('Forbidden reverse import fixture')
    monkeypatch.setattr(sys, 'argv', ['check_public_history'])
    with pytest.raises(ValueError):
        checker.main()
