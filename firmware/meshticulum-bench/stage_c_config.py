# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Freeze and execute C's SPI configuration check with transmit/receive denied."""
import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/meshticulum-abc-deployment-20261009'
REMOTE = '''import base64,hashlib,json,os,stat,subprocess,sys
from pathlib import Path
os.umask(0o077)
expected=sys.argv[1]
x=json.loads(sys.stdin.buffer.read(200000))
assert hashlib.sha256(json.dumps(x['hashes'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==expected
assert set(x['files'])==set(x['hashes'])
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()=='21f9c178-6090-40d9-9359-ecf1c7b950b1'
root=Path('/opt/meshticulum-bench')/('c-config-'+expected[:16])
root.mkdir(mode=0o700,exist_ok=True)
for n,b in x['files'].items():
 assert not n.startswith('/') and '..' not in Path(n).parts
 data=base64.b64decode(b,validate=True);assert hashlib.sha256(data).hexdigest()==x['hashes'][n]
 p=root/n;p.parent.mkdir(mode=0o700,exist_ok=True)
 for d in [root,p.parent]:
  i=d.lstat();assert stat.S_ISDIR(i.st_mode) and i.st_uid==0 and stat.S_IMODE(i.st_mode)==0o700
 if not p.exists():
  fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 i=p.lstat();assert stat.S_ISREG(i.st_mode) and i.st_uid==0 and i.st_nlink==1 and stat.S_IMODE(i.st_mode)==0o600 and p.read_bytes()==data
receipt=Path('/var/lib/meshticulum-bench')/('config-check-'+expected[:16]+'.json')
assert not receipt.exists(), 'already_checked_this_immutable_bundle'
units=['sage-lantern-personal-sage.service','sage-lantern-voice-loop.service','sage-lantern-llm.service','sage-lantern-vlm.service']
def services():
 return {u:subprocess.run(['/usr/bin/systemctl','is-active',u],capture_output=True,text=True).stdout.strip() for u in units}
before=services()
r=subprocess.run(['/usr/bin/python3','-I','-B',str(root/'check_hat_config.py')],capture_output=True,text=True,timeout=15)
result={'bundle':str(root),'device_receipt':str(receipt),'source_manifest_sha256':expected,'source_hashes':x['hashes'],'returncode':r.returncode,'stderr':r.stderr,'services_before':before,'services_after':services()}
try:result['check']=json.loads(r.stdout)
except ValueError:result['stdout']=r.stdout[-4000:]
fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write(chr(10));f.flush();os.fsync(f.fileno())
print(json.dumps(result))
'''


def main():
    files = {'check_hat_config.py': (ROOT / 'firmware/meshticulum-bench/check_hat_config.py').read_bytes()}
    for name in ('__init__.py', 'linux_hat.py', 'hat_probe.py', 'serial_protocol.py', 'serial_worker.py', 'sx1262.py'):
        files['meshticulum_bench/' + name] = (ROOT / 'src/meshticulum/bench' / name).read_bytes()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    payload = json.dumps({'files': {name: base64.b64encode(data).decode() for name, data in files.items()},
                          'hashes': hashes}).encode()
    command = ['ssh', '-T', '-i', '/Users/l33tdawg/.ssh/id_ed25519_lantern_management',
        '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'HostKeyAlias=192.168.50.104', '-o', 'UserKnownHostsFile=/Users/l33tdawg/.ssh/known_hosts',
        'root@192.168.50.2', shlex.join(['python3', '-c', REMOTE, digest])]
    run = subprocess.run(command, input=payload, capture_output=True, timeout=40)
    if run.returncode:
        print(run.stderr.decode(errors='replace'))
        print(run.stdout.decode(errors='replace'))
        raise SystemExit(run.returncode)
    result = json.loads(run.stdout)
    latest = WORK / 'C_CONFIG_CHECK.json'
    if latest.exists():
        previous = json.loads(latest.read_text())
        (WORK / ('C_CONFIG_CHECK-' + previous['source_manifest_sha256'][:16] + '.json')).write_bytes(latest.read_bytes())
    latest.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
