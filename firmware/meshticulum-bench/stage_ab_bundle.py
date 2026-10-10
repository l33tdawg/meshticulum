"""Stage a frozen archive through pinned management SSH; never flash or use sudo."""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

WORK = Path(__file__).resolve().parents[2] / 'work/meshticulum-abc-deployment-20261009'
STAGER = '''import hashlib,json,os,stat,sys
from pathlib import Path
expected=sys.argv[1]
data=sys.stdin.buffer.read(20000001)
assert len(data)<=20000000 and hashlib.sha256(data).hexdigest()==expected
directory=Path.home()/'meshticulum-bench-staging'
directory.mkdir(mode=0o700,exist_ok=True)
info=directory.lstat()
assert stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700
p=directory/('ab-flash-'+expected[:16]+'.tar.gz')
if not p.exists():
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:
  f.write(data);f.flush();os.fsync(f.fileno())
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
with os.fdopen(fd,'rb') as f:
 info=os.fstat(f.fileno())
 assert stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and info.st_nlink==1 and stat.S_IMODE(info.st_mode)==0o600
 assert f.read()==data
print(json.dumps({'archive':str(p),'sha256':expected,'bytes':len(data),'uid':os.geteuid(),'python_version':sys.version.split()[0],'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'radio_opened':False,'firmware_written':False}))
'''

OPERATOR = '''#!/usr/bin/env python3
"""Run the frozen operator installation over the existing pinned SSH aliases.
Sudo prompts stay in the terminal. No credentials are stored in this helper.
"""
import argparse, hashlib, os, shlex
from pathlib import Path
ROWS = {rows!r}
ARCHIVE_SHA256 = {archive!r}
BOOTSTRAP_SHA256 = {bootstrap!r}
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('unit', choices=ROWS)
p.add_argument('--action', choices=['flash', 'status', 'rollback'], default='flash')
a = p.parse_args()
bootstrap = Path(__file__).with_name('ab_bootstrap.py').read_bytes()
if hashlib.sha256(bootstrap).hexdigest() != BOOTSTRAP_SHA256:
    raise SystemExit('Local bootstrap changed; refusing privileged execution.')
row = ROWS[a.unit]
remote = shlex.join(['sudo', '/usr/bin/python3', '-I', '-B', '-c', bootstrap.decode(),
                     a.action, a.unit, row['archive'], ARCHIVE_SHA256])
os.execvp('ssh', ['ssh', '-tt', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                  '-o', 'StrictHostKeyChecking=yes', row['host'], remote])
'''


def main():
    bundle = json.loads((WORK / 'AB_BUNDLE.json').read_text())
    data = Path(bundle['archive']).read_bytes()
    assert hashlib.sha256(data).hexdigest() == bundle['sha256']
    old = WORK / 'AB_STAGING.json'
    if old.exists():
        prior = json.loads(old.read_text())
        (WORK / ('AB_STAGING-' + prior['bundle_sha256'][:16] + '.json')).write_bytes(old.read_bytes())
    results = []
    for unit, host in (('unit-a', 'lantern-a'), ('unit-b', 'lantern-b')):
        command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'ConnectTimeout=8', host, shlex.join(['python3', '-c', STAGER, bundle['sha256']])]
        run = subprocess.run(command, input=data, capture_output=True, timeout=30)
        result = {'unit': unit, 'host': host, 'returncode': run.returncode,
                  'stderr': run.stderr.decode(errors='replace')}
        if run.returncode == 0:
            result.update(json.loads(run.stdout))
        else:
            result['stdout'] = run.stdout.decode(errors='replace')[-1000:]
        results.append(result)
        print(json.dumps(result), flush=True)
    old.write_text(json.dumps({'bundle_sha256': bundle['sha256'], 'results': results}, indent=2) + '\n')
    if not all(v['returncode'] == 0 for v in results):
        raise SystemExit('Staging incomplete; no flash attempted.')
    directory = Path(bundle['archive']).parent
    rows = {v['unit']: {'host': v['host'], 'archive': v['archive']} for v in results}
    source = OPERATOR.format(rows=rows, archive=bundle['sha256'], bootstrap=bundle['bootstrap_sha256'])
    compile(source, 'operator.py', 'exec')
    operator = directory / 'operator.py'
    operator.write_text(source)
    notes = directory / 'OPERATOR.txt'
    notes.write_text('Meshticulum experimental A/B installation\n\n'
        'The installer verifies the whole archive before running root-owned code. It pins USB and eFuse MAC, '
        'requires an erased or exact reviewed Arduino app0 selector, checks the current r7 image, '
        'backs up the app sectors being replaced, verifies the write and unchanged outside regions, '
        'then checks the new firmware boots disarmed. No experimental ARM/TX, service or access-policy changes.\n\n'
        'Run from the Mac:\n\npython3 ' + str(operator) + ' unit-a\npython3 ' + str(operator) + ' unit-b\n\n'
        'The sudo prompt is on the device. Stored status: add --action status. '
        'Rollback: add --action rollback. The same unit/USB/bundle and exact private sector backup are required. '
        'Current NVS and SPIFFS are preserved. If USB no longer enumerates, physical bootloader entry may be needed.\n\n'
        'Requires /root/esp-venv-r3/bin/python with esptool 4.x and pyserial. '
        'A has independently verified Python 3.11.2, esptool 4.5.1, pyserial 3.5.\n\n'
        'Archive SHA-256: ' + bundle['sha256'] + '\nFirmware identity: ' + bundle['firmware_identity'] + '\n')
    bundle.update({'staged': True, 'flashed': False, 'staging_receipt': str(old),
                   'operator_helper': str(operator), 'operator_helper_sha256': hashlib.sha256(source.encode()).hexdigest(),
                   'operator_notes': str(notes)})
    for destination in (WORK / 'AB_BUNDLE.json', directory / 'BUNDLE.json'):
        destination.write_text(json.dumps(bundle, indent=2) + '\n')


if __name__ == '__main__':
    main()
