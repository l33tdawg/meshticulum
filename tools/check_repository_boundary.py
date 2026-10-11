# SPDX-License-Identifier: Apache-2.0
"""Reject product implementations and reverse imports in Meshticulum artifacts."""
import argparse
import ast
import json
from pathlib import Path
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORTS = ('sage_sdk', 'sage_lantern', 'lantern_meshticulum', 'meshticulum.integrations')
FORBIDDEN_TEXT = ('/Users/', '192.168.50.', '/root/esp-venv', 'sage-lantern-', '/etc/sage-lamplighter-lab')


def check_python(name, raw):
    text = raw.decode('utf-8')
    for node in ast.walk(ast.parse(text, filename=name)):
        imports = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
            [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
        for value in imports:
            if any(value == prefix or value.startswith(prefix + '.') for prefix in FORBIDDEN_IMPORTS):
                raise ValueError(f'{name}: application import {value}')
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module':
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if any(arg.value == prefix or arg.value.startswith(prefix + '.') for prefix in FORBIDDEN_IMPORTS):
                        raise ValueError(f'{name}: application dynamic import {arg.value}')
    for value in FORBIDDEN_TEXT:
        if value in text:
            raise ValueError(f'{name}: product deployment value {value}')


def check_member(name, raw):
    parts = Path(name).parts
    if 'lantern_meshticulum' in parts or 'sage_lantern' in parts or 'lantern' in parts:
        raise ValueError(f'{name}: Lantern package or tooling in Meshticulum distribution')
    if 'meshticulum' in parts and 'integrations' in parts:
        raise ValueError(f'{name}: application adapters in Meshticulum distribution')
    if 'meshticulum' in parts and (Path(name).name in ('node.py', 'service_node.py', 'profiles.py')
            or Path(name).name.startswith('pilot_')):
        raise ValueError(f'{name}: fleet implementation in Meshticulum distribution')
    if name.endswith('.py') and 'meshticulum' in parts and 'tests' not in parts:
        check_python(name, raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path)
    parser.add_argument('--sdist', type=Path)
    args = parser.parse_args()
    files = 0
    for path in sorted((ROOT / 'src/meshticulum').rglob('*')):
        if path.is_file() and path.suffix == '.py':
            check_member(str(path.relative_to(ROOT / 'src')), path.read_bytes())
            files += 1
    artifacts = []
    if args.wheel:
        with zipfile.ZipFile(args.wheel) as archive:
            for name in archive.namelist():
                check_member(name, archive.read(name))
        artifacts.append(args.wheel.name)
    if args.sdist:
        with tarfile.open(args.sdist, 'r:gz') as archive:
            for member in archive:
                if member.isfile():
                    check_member(member.name, archive.extractfile(member).read())
        artifacts.append(args.sdist.name)
    print(json.dumps(dict(core_python_files=files, artifacts=artifacts, boundary='Lantern depends on Meshticulum')))


if __name__ == '__main__':
    main()
