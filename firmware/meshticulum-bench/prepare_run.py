# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Prepare finite pairwise RF runs after explicit location/antenna confirmation.

Fresh independent keys and synthetic random payloads go only to the selected
sender/receiver over pinned management. Receiver packages contain a digest and
length, never the application bytes. A relay run reads its sender's previously
verified local radio chunk instead of re-sending application bytes over SSH.
The private session directory contains temporary bench secrets; public receipts
and operator stdout never do. No personal identity or key is imported.
"""
import argparse
import base64
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/meshticulum-abc-deployment-20261009'
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.profiles import MODES, profile_for
from make_object import load_chunk, manifest_for, validate

STAGE = '''import hashlib,json,os,stat,sys
from pathlib import Path
digest,run=sys.argv[1:]
data=sys.stdin.buffer.read(40001)
assert len(data)<=40000 and hashlib.sha256(data).hexdigest()==digest
root=Path.home()/'meshticulum-bench-staging'
info=root.lstat();assert stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==0o700
p=root/('grant-'+run+'.json')
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
print(json.dumps({'archive':str(p),'sha256':digest}))
'''

OPERATOR = '''#!/usr/bin/env python3
"""Use pinned management and temporary root grants; sudo stays in the terminal."""
import argparse,hashlib,os,shlex,subprocess,sys
from pathlib import Path
ROWS={rows!r}
RUN={run!r}
RUNTIME={runtime!r}
MANIFEST={manifest!r}
BOOTSTRAP={bootstrap!r}
COLLECTOR={collector!r}
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('action',choices=['provision','run','revoke','result','collect'])
p.add_argument('unit',choices=ROWS)
a=p.parse_args();row=ROWS[a.unit]
python='/usr/bin/python3' if a.unit=='unit-c' else '/root/esp-venv-r3/bin/python'
launcher='/opt/meshticulum-bench/runtime-'+RUNTIME[:16]+'/bench_node.py'
if a.action=='provision':
 source=Path(__file__).with_name('grant_bootstrap.py').read_bytes()
 if hashlib.sha256(source).hexdigest()!=BOOTSTRAP:raise SystemExit('Changed grant bootstrap; refused.')
 command=[python,'-I','-B','-c',source.decode(),a.unit,row['archive'],row['sha256'],RUNTIME,MANIFEST]
elif a.action=='result':
 command=['/usr/bin/cat','/var/lib/meshticulum-bench/runs/'+RUN+'/result.json']
elif a.action=='collect':
 source=Path(__file__).with_name('collect_run.py').read_bytes()
 if hashlib.sha256(source).hexdigest()!=COLLECTOR:raise SystemExit('Changed evidence collector; refused.')
 command=[python,'-I','-B','-c',source.decode(),a.unit,RUN,RUNTIME,MANIFEST]
else:
 command=[python,'-I','-B',launcher,a.action,a.unit,'--run-id',RUN]
ssh=row['ssh'][:]
if a.unit!='unit-c':command.insert(0,'sudo');ssh[1]='-tt'
if a.action!='collect':os.execvp('ssh',ssh+[shlex.join(command)])
# A/B require a remote terminal for sudo. Keep its prompts visible on stderr,
# then save only the collector's single JSON record, never terminal chatter.
process=subprocess.Popen(ssh+[shlex.join(command)],stdout=subprocess.PIPE)
raw=bytearray()
while True:
 part=os.read(process.stdout.fileno(),4096)
 if not part:break
 raw.extend(part);sys.stderr.buffer.write(part);sys.stderr.buffer.flush()
 if len(raw)>1000000:process.terminate();raise SystemExit('Collection output limit.')
if process.wait()!=0:raise SystemExit('Remote collection failed; no evidence saved.')
import json
records=[]
for line in raw.decode('utf-8').splitlines():
 try:value=json.loads(line)
 except ValueError:continue
 if isinstance(value,dict) and value.get('schema')=='meshticulum.collected-run.v1':records.append(value)
if len(records)!=1 or records[0]['unit']!=a.unit or records[0]['run_id']!=RUN:
 raise SystemExit('Missing or ambiguous collection record.')
target=Path(__file__).with_name(a.unit+'-collected.json')
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'w') as f:json.dump(records[0],f,indent=2);f.write(chr(10));f.flush();os.fsync(f.fileno())
print(json.dumps(dict(saved=str(target),unit=a.unit,run_id=RUN)))
'''


def peer(unit, receipt):
    hardware = receipt.get('radio', {}).get('usb_serial', 'orangepi5plus-spi0.0-sx1262-hf')
    return {'chain_id': 'lantern-bench-v1',
            'agent_id': hashlib.sha256(('lantern-bench-v1/' + unit + '/' + hardware).encode()).hexdigest()}


def packages(sender, receiver, *, now_us, boot_ids, peers, payload=None, descriptor=None, object_manifest=None, profile_name='baseline'):
    if sender == receiver: raise ValueError('distinct endpoints required')
    if descriptor is None:
        if type(payload) is not bytes or not 1 <= len(payload) <= 16384: raise ValueError('invalid synthetic payload')
        digest = hashlib.sha256(payload).hexdigest()
        descriptor = {'object_id': digest, 'chunk_index': 0, 'sha256': digest, 'bytes': len(payload)}
        object_manifest = manifest_for([payload])
    if object_manifest is not None:
        validate(object_manifest)
        if object_manifest['chunks'][descriptor['chunk_index']] != descriptor:
            raise ValueError('descriptor_does_not_match_manifest')
    if payload is not None and (len(payload) != descriptor['bytes'] or hashlib.sha256(payload).hexdigest() != descriptor['sha256']):
        raise ValueError('payload_does_not_match_descriptor')
    identifier, generation, secret = secrets.token_hex(16), secrets.token_hex(16), secrets.token_bytes(32)
    profile = profile_for(profile_name)
    result = {}
    for unit in (sender, receiver):
        plan = {'schema': 'meshticulum.bench-run.v1', 'unit': unit, 'host_boot_id': boot_ids[unit],
            'local_peer': peers[unit], 'initiator': peers[sender], 'responder': peers[receiver],
            'session_id': identifier, 'generation': generation, 'issued_epoch_us': now_us - 1000000,
            'expires_epoch_us': now_us + 239000000, 'profile': asdict(profile), 'object': descriptor,
            'sender_unit': sender, 'country': 'MY', 'antenna_confirmed': True, 'max_frames': 512}
        result[unit] = {'grant': plan, 'secret': base64.b64encode(secret).decode(),
            'object_manifest': object_manifest,
            'payload': base64.b64encode(payload).decode() if unit == sender and payload is not None else None,
            'reuse_verified_chunk': unit == sender and payload is None}
    return identifier, result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('sender', choices=('unit-a', 'unit-b', 'unit-c'))
    p.add_argument('receiver', choices=('unit-a', 'unit-b', 'unit-c'))
    p.add_argument('--country', choices=('MY',), required=True)
    p.add_argument('--antennas-confirmed', action='store_true', required=True)
    p.add_argument('--profile', choices=MODES, default='baseline')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--synthetic-bytes', type=int, default=2048)
    mode.add_argument('--relay-from-receipt', type=Path)
    mode.add_argument('--object-manifest', type=Path)
    p.add_argument('--chunk-index', type=int)
    args = p.parse_args()
    if args.sender == args.receiver: p.error('distinct units required')
    if args.chunk_index is not None and args.object_manifest is None:
        p.error('--chunk-index is only used with --object-manifest')
    os.umask(0o077)
    spec = importlib.util.spec_from_file_location('staging', ROOT / 'firmware/meshticulum-bench/stage_runtime.py')
    staging = importlib.util.module_from_spec(spec); spec.loader.exec_module(staging)
    bundle = json.loads((WORK / 'RUNTIME_BUNDLE.json').read_text())
    if not bundle['installed']: raise SystemExit('Install and inspect the exact runtime first.')
    if hashlib.sha256(Path(bundle['archive']).read_bytes()).hexdigest() != bundle['sha256']:
        raise SystemExit('Local runtime archive no longer matches its pin.')
    with tarfile.open(bundle['archive'], 'r:gz') as archive:
        manifest_pin = hashlib.sha256(archive.extractfile('manifest.json').read()).hexdigest()
    receipts = {unit: json.loads((WORK / (unit[-1].upper() + '_RUNTIME_INSTALLED.json')).read_text())
                for unit in (args.sender, args.receiver)}
    for row in receipts.values():
        if row['bundle_manifest_sha256'] != manifest_pin: raise SystemExit('Device runtime does not match current bundle.')
    descriptor = object_manifest = None
    if args.relay_from_receipt:
        prior = json.loads(args.relay_from_receipt.read_text())
        if prior.get('schema') == 'meshticulum.collected-run.v1':
            object_manifest = prior.get('object_manifest')
            prior = prior['result']
        if (prior['unit'] != args.sender or not prior.get('chunk_durable')
                or prior['verified_chunk_sha256'] != prior['object']['sha256']):
            raise SystemExit('Relay source must be this sender\'s verified prior radio receipt.')
        descriptor = prior['object']; payload = None
    elif args.object_manifest:
        if args.chunk_index is None: p.error('--object-manifest requires --chunk-index')
        object_manifest, payload = load_chunk(args.object_manifest, args.chunk_index)
        descriptor = object_manifest['chunks'][args.chunk_index]
    else:
        if not 1 <= args.synthetic_bytes <= 16384: p.error('synthetic size must be 1..16384')
        payload = secrets.token_bytes(args.synthetic_bytes)
    identifier, values = packages(args.sender, args.receiver, now_us=time.time_ns() // 1000,
        boot_ids={unit: row['host_boot_id'] for unit, row in receipts.items()},
        peers={unit: peer(unit, row) for unit, row in receipts.items()}, payload=payload, descriptor=descriptor,
        object_manifest=object_manifest, profile_name=args.profile)
    base = WORK / 'sessions'; base.mkdir(mode=0o700, exist_ok=True)
    directory = base / identifier; directory.mkdir(mode=0o700)
    rows = {}
    for unit, value in values.items():
        data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        digest = hashlib.sha256(data).hexdigest()
        (directory / (unit + '-private.json')).write_bytes(data)
        command = staging.SSH[unit] + [shlex.join(['python3', '-c', STAGE, digest, identifier])]
        result = subprocess.run(command, input=data, capture_output=True, timeout=30)
        if result.returncode: raise RuntimeError('Grant staging failed for ' + unit)
        rows[unit] = dict(json.loads(result.stdout), ssh=staging.SSH[unit])
    bootstrap = (ROOT / 'firmware/meshticulum-bench/grant_bootstrap.py').read_bytes()
    (directory / 'grant_bootstrap.py').write_bytes(bootstrap)
    collector = (ROOT / 'firmware/meshticulum-bench/collect_run.py').read_bytes()
    (directory / 'collect_run.py').write_bytes(collector)
    operator = OPERATOR.format(rows=rows, run=identifier, runtime=bundle['sha256'],
        manifest=manifest_pin, bootstrap=hashlib.sha256(bootstrap).hexdigest(),
        collector=hashlib.sha256(collector).hexdigest())
    compile(operator, 'operator.py', 'exec'); (directory / 'operator.py').write_text(operator)
    public = {'schema': 'meshticulum.prepared-run.v1', 'run_id': identifier, 'sender': args.sender,
        'receiver': args.receiver, 'object': values[args.sender]['grant']['object'],
        'object_manifest': values[args.sender]['object_manifest'],
        'profile': values[args.sender]['grant']['profile'],
        'runtime_archive_sha256': bundle['sha256'], 'runtime_manifest_sha256': manifest_pin,
        'expires_epoch_us': values[args.sender]['grant']['expires_epoch_us'],
        'operator': str(directory / 'operator.py'), 'staged': rows,
        'provisioned': False, 'run_started': False,
        'warning': 'Private package files contain short-lived bench keys; do not attach or publish them.'}
    (directory / 'RUN.json').write_text(json.dumps(public, indent=2) + '\n')
    print(json.dumps({k: v for k, v in public.items() if k != 'staged'}))


if __name__ == '__main__': main()
