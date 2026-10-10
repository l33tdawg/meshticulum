"""Stage the source-only bench runtime and write a hash-pinned sudo operator."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/meshticulum-abc-deployment-20261009'
SSH = {unit: ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=8', host] for unit, host in [('unit-a', 'lantern-a'), ('unit-b', 'lantern-b')]}
SSH['unit-c'] = ['ssh', '-T', '-i', '/Users/l33tdawg/.ssh/id_ed25519_lantern_management',
    '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
    '-o', 'HostKeyAlias=192.168.50.104', '-o', 'UserKnownHostsFile=/Users/l33tdawg/.ssh/known_hosts',
    'root@192.168.50.2']

OPERATOR = '''#!/usr/bin/env python3
"""Install the exact source runtime and inspect disarmed hardware. No RF grant."""
import argparse,hashlib,os,shlex
from pathlib import Path
ROWS={rows!r}
DIGEST={digest!r}
BOOTSTRAP={bootstrap!r}
p=argparse.ArgumentParser(description=__doc__);p.add_argument('unit',choices=ROWS);a=p.parse_args()
raw=Path(__file__).with_name('runtime_bootstrap.py').read_bytes()
if hashlib.sha256(raw).hexdigest()!=BOOTSTRAP: raise SystemExit('Changed root bootstrap; refused.')
row=ROWS[a.unit]
command=['/usr/bin/python3','-I','-B','-c',raw.decode(),a.unit,row['archive'],DIGEST]
ssh=row['ssh'][:]
if a.unit!='unit-c':
 command.insert(0,'sudo');ssh[1]='-tt'
os.execvp('ssh',ssh+[shlex.join(command)])
'''


def main():
    record = json.loads((WORK / 'RUNTIME_BUNDLE.json').read_text())
    archive = Path(record['archive'])
    data = archive.read_bytes()
    assert hashlib.sha256(data).hexdigest() == record['sha256']
    spec = importlib.util.spec_from_file_location('ab_stager', ROOT / 'firmware/meshticulum-bench/stage_ab_bundle.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    stager = module.STAGER.replace("'ab-flash-'", "'runtime-'")
    results = []
    for unit, ssh in SSH.items():
        result = subprocess.run(ssh + [shlex.join(['python3', '-c', stager, record['sha256']])],
                                input=data, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError(unit + ':' + result.stderr.decode(errors='replace'))
        row = json.loads(result.stdout); row['unit'] = unit; row['ssh'] = ssh
        results.append(row)
        print(json.dumps(row), flush=True)
    (WORK / 'RUNTIME_STAGING.json').write_text(json.dumps({'bundle_sha256': record['sha256'],
                                                         'results': results}, indent=2) + '\n')
    operator = OPERATOR.format(rows={row['unit']: row for row in results},
                               digest=record['sha256'], bootstrap=record['bootstrap_sha256'])
    compile(operator, 'operator.py', 'exec')
    (archive.parent / 'operator.py').write_text(operator)
    (archive.parent / 'OPERATOR.txt').write_text('Meshticulum bounded bench runtime\n\n'
        'Install and inspect only (no grant, ARM or RF):\n'
        + '\n'.join('python3 ' + str(archive.parent / 'operator.py') + ' ' + unit for unit in SSH)
        + '\n\nA separate finite root-provisioned run is required. Check the current owner country and antenna record before RF. '
        'No service or access-policy changes. The bundle excludes SAGE application code and private keys.\n')


if __name__ == '__main__': main()
