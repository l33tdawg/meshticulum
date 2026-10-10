"""Explicit finite mixed public pilot; sudo prompts stay in this terminal.

Use only during a current coordinated device window. Config files and artifact
hashes do not grant RF permission. Import and --help perform no remote action.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from meshticulum.bench.pilot_controller import Controller
from meshticulum.bench.pilot_ssh import SSHOperator
from meshticulum.ip.node import _read, _write
from meshticulum.ip.journal import private
from meshticulum.resources import lamplighter as ll
from meshticulum.resources import resource


def seed_package(config,unit,role,pack,trust,digest,manifest):
    files={name:(pack/name).read_bytes() for name in ('manifest.json','manifest.sig.json')}
    if role=='source':
        for entry in manifest['chunks']:
            name='chunks/'+entry['sha256']+'.json';files[name]=(pack/name).read_bytes()
    return dict(schema='meshticulum.pilot-public-seed.v1',unit=unit,
        host_boot_id=config['units'][unit]['host_boot_id'],role=role,manifest_sha256=digest,trust=trust,
        files={name:base64.b64encode(raw).decode() for name,raw in files.items()})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--runtime-archive',type=Path,required=True)
    p.add_argument('--pack',type=Path,required=True)
    p.add_argument('--trust',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--chunk-index',type=int,default=0)
    p.add_argument('--mode',choices=('mixed','resume'),default='mixed')
    p.add_argument('--loss-rx-bytes',type=int,default=512)
    p.add_argument('--country',choices=['MY'],required=True)
    p.add_argument('--antennas-confirmed',action='store_true',required=True)
    p.add_argument('--current-device-handoff-confirmed',action='store_true',required=True)
    p.add_argument('--install-runtime',action='store_true')
    p.add_argument('--ask-sudo',action='store_true')
    args=p.parse_args();os.umask(0o077)
    config=json.loads(_read(args.config,16384))
    archive=args.runtime_archive.read_bytes()
    if hashlib.sha256(archive).hexdigest()!=config['runtime_sha256']:p.error('Runtime archive differs from config pin.')
    with tarfile.open(args.runtime_archive,'r:gz') as tar:
        manifest_raw=tar.extractfile('manifest.json').read()
    if hashlib.sha256(manifest_raw).hexdigest()!=config['manifest_sha256']:p.error('Runtime manifest differs from config pin.')
    trust=json.loads(_read(args.trust,16384))
    raw=(args.pack/'manifest.json').read_bytes();digest=hashlib.sha256(raw).hexdigest()
    cache=ll.VerifiedCache(args.pack,trust,digest);manifest=cache.value
    if not 0<=args.chunk_index<len(manifest['chunks']):p.error('Chunk index is outside the signed manifest.')
    chunk=manifest['chunks'][args.chunk_index]
    path='/packs/'+digest+'/chunks/'+chunk['sha256']+'.json'
    request=resource.encode_request(path,0,16356)
    response=resource.BlockServer(cache.read).handle(request)
    if len(response)>16384:p.error('Selected whole chunk exceeds the radio pilot bound.')
    exchanges=[]
    if args.mode=='resume':
        if args.chunk_index+4>len(manifest['chunks']):p.error('Resume mode needs four distinct signed chunks.')
        for entry in manifest['chunks'][args.chunk_index:args.chunk_index+4]:
            req=resource.encode_request('/packs/'+digest+'/chunks/'+entry['sha256']+'.json',0,16356)
            reply=resource.BlockServer(cache.read).handle(req)
            if len(reply)>16384:p.error('Resume chunk exceeds the radio pilot bound.')
            exchanges.append((req,reply))
        if not 8<=args.loss_rx_bytes<len(exchanges[1][1]):p.error('Loss cutoff must interrupt the second response.')
    args.output.mkdir(mode=0o700,exist_ok=False);private(args.output.lstat(),True)
    passwords={}
    if args.ask_sudo:
        for unit,row in config['units'].items():
            if row['sudo']:passwords[unit]=getpass.getpass('Sudo for '+unit+' (memory only): ')
    operator=SSHOperator(config,provision_source=(ROOT/'firmware/meshticulum-bench/pilot_grant_bootstrap.py').read_bytes(),
        runtime_source=(ROOT/'firmware/meshticulum-bench/runtime_bootstrap.py').read_bytes(),passwords=passwords)
    passwords.clear()
    result=dict(schema='meshticulum.mixed-public-pilot-controller.v1',complete=False,
        runtime_sha256=config['runtime_sha256'],manifest_sha256=digest,
        publisher_verified=False,private_admission=False,matched_benchmark=False,
        measured_airtime_us=None,measured_energy_joules=None,measured_eirp_dbm=None)
    controller=None
    try:
        if args.install_runtime:
            result['installations']={unit:operator.install_runtime(unit,archive) for unit in config['units']}
        before={unit:operator.facts(unit) for unit in config['units']}
        operator.seed(seed_package(config,'unit-b','source',args.pack,trust,digest,manifest))
        operator.seed(seed_package(config,'unit-c','recipient',args.pack,trust,digest,manifest))
        epoch=time.time_ns()//1000
        expires=min(epoch+160000000,config['handoff_end_epoch_us']-15000000)
        import secrets
        peers={unit:dict(chain_id='meshticulum-public-pilot-v1',agent_id=hashlib.sha256(
            ('meshticulum-public-pilot-v1/'+unit+'/'+row['host_boot_id']).encode()).hexdigest()) for unit,row in config['units'].items()}
        controller=Controller(operator,campaign_id=secrets.token_hex(16),resource_sha256=digest,
            expires_epoch_us=expires,handoff_deadline_monotonic=operator.deadline,peers=peers,
            boots={unit:row['host_boot_id'] for unit,row in config['units'].items()},
            radio_permission=dict(country=args.country,antenna_confirmed=args.antennas_confirmed))
        if args.mode=='resume':
            sequence=controller.failure_resume(exchanges,cutoff=args.loss_rx_bytes)
            piece=sequence['return_piece'];result['resume_sequence']=sequence
        else:piece=controller.mixed_piece(request,response)
        after={unit:operator.facts(unit) for unit in config['units']}
        stable=lambda values:{unit:{k:v for k,v in value.items() if k not in ('epoch_us','monotonic_us')} for unit,value in values.items()}
        if stable(before)!=stable(after):raise RuntimeError('native_service_lifecycle_changed')
        result.update(complete=piece is not None,publisher_verified=piece is not None,
            piece=piece,facts_before=before,facts_after=after,mode=args.mode)
    except BaseException as error:
        result['error_type']=type(error).__name__
    finally:
        if controller is not None:
            result['cleanup']=controller.retire();result['links']=controller.links
            if any('retirement_error' in item or 'stop_error' in item for item in result['cleanup']):result['complete']=False
        for job in operator.jobs.values():operator.reap(job)
        operator.passwords.clear()
        result['management_events']=operator.events
        result['handoff_overrun']=any(item.get('outside_handoff') is True for item in operator.events)
        if result['handoff_overrun']:result['complete']=False
        _write(args.output/'RESULT.json',json.dumps(result,sort_keys=True,indent=2).encode()+b'\n')
    print(json.dumps(dict(saved=str(args.output/'RESULT.json'),complete=result['complete'],publisher_verified=result['publisher_verified'])))
    if not result['complete']:raise SystemExit(1)


if __name__=='__main__':main()
