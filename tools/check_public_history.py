# SPDX-License-Identifier: Apache-2.0
"""Enforce the current-tree boundary and report retained development history.

The project preserves its earlier extraction history, including Lantern code.
Use --strict-history when checking a distribution that promises no such history.
GitHub's cached views are outside the scope of a local Git inspection.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from check_repository_boundary import check_member

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def check_path(commit, name, mode, kind, blob):
    if kind != b'blob' or mode not in (b'100644', b'100755'):
        raise ValueError('unexpected link/submodule in publication tree')
    path = Path(name)
    if path.parts[0] in ('work', 'internal-docs', 'software', '.codex', '.claude'):
        raise ValueError('private or product directory in publication tree')
    if name.startswith('firmware/meshticulum-bench/') and (
            path.name.startswith('pilot_') or path.name in
            ('run_public_pilot.py', 'runtime_bootstrap.py', 'analyze_runs.py')):
        raise ValueError('native product tooling in publication tree')
    check_member(name, git('cat-file', 'blob', blob.decode()))


def entries(commit):
    for row in git('ls-tree', '-rz', commit).split(b'\0'):
        if row:
            header, name = row.split(b'\t', 1)
            yield name.decode('utf-8'), *header.split()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--strict-history', action='store_true')
    args = parser.parse_args()
    if git('rev-parse', '--is-shallow-repository').strip() == b'true':
        raise ValueError('publication history check requires a complete clone')
    refs = git('for-each-ref', '--format=%(refname)').decode().splitlines()
    commits = git('rev-list', '--all').decode().splitlines()
    if not refs or not commits:
        raise ValueError('no committed history to review')
    head = git('rev-parse', 'HEAD').decode().strip()
    current_files = 0
    for name, mode, kind, blob in entries(head):
        check_path(head, name, mode, kind, blob)
        current_files += 1
    seen, files, historical_product_files = set(), 0, []
    for commit in commits:
        for name, mode, kind, blob in entries(commit):
            pair = (name, blob)
            if pair not in seen:
                try:
                    check_path(commit, name, mode, kind, blob)
                except ValueError:
                    if args.strict_history:
                        raise
                    historical_product_files.append(dict(path=name, blob=blob.decode()))
                seen.add(pair)
                files += 1
    print(json.dumps(dict(current_commit=head, current_files=current_files, current_tree_boundary_passed=True,
                          commits=len(commits), retained_refs=len(refs), unique_file_versions=files,
                          historical_product_file_versions=len(historical_product_files),
                          historical_lantern_code_retained=bool(historical_product_files),
                          strict_history_requested=args.strict_history, github_cached_views_verified=False)))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
