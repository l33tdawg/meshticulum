"""Controller failure/provenance controls with invented management records.

These validate finite orchestration, not a physical route or native policy.
"""
from dataclasses import dataclass
import hashlib

import pytest
from meshticulum.bench.pilot_controller import Controller, pair_packages, UNITS
from meshticulum.bench.wire import WireError
from meshticulum.resources import resource

PEERS={unit:dict(chain_id='pilot-source-controls',agent_id=char*64) for unit,char in zip(UNITS,'abc')}
BOOTS={unit:'00000000-0000-4000-8000-'+str(i).zfill(12) for i,unit in enumerate(UNITS,1)}
PERMISSION=dict(country='MY',antenna_confirmed=True)
MANIFEST='e'*64
CHUNK='f'*64
REQUEST=resource.encode_request('/packs/'+MANIFEST+'/chunks/'+CHUNK+'.json',0,16356)
RESPONSE=resource.BlockServer(lambda _:b'public source fixture').handle(REQUEST)


@dataclass
class Clock:
    now:float=10
    def mono(self):return self.now
    def epoch(self):return 1000000000+int((self.now-10)*1000000)
    def sleep(self,seconds):self.now+=seconds


class Handle:
    def __init__(self):self.code=None
    def poll(self):return self.code


class ModelOperator:
    """Opaque modeled receipts/handles; no subprocess, socket or radio."""
    def __init__(self,clock,fault=None):
        self.clock,self.fault=clock,fault;self.events=[];self.packages={};self.handles={};self.results={}
    def endpoints(self,a,b):return {a:('127.0.0.1',35001),b:('127.0.0.1',35002)}
    def prepare(self,p):
        self.events.append(('prepare',p['unit'],p))
        run=p['grant'].get('session_id') or p['grant']['session']['session_id']
        if self.fault=='prepare_sender' and p['unit']=='unit-c':raise RuntimeError('source prepare failure')
        self.packages[(p['unit'],run)]=p
    def start(self,unit,pair):
        self.events.append(('start',unit,pair['run_id']))
        run=pair['run_id'];handle=Handle();self.handles[(unit,run)]=handle
        if unit==pair['sender']:
            for peer in (pair['sender'],pair['receiver']):
                self.handles[(peer,run)].code=0
                self.results[(peer,run)]=dict(complete=True,unit=peer,host_boot_id=BOOTS[peer],session_id=run,
                    service_format=pair['format'],sends=peer==unit,object=pair['descriptor'],
                    original_application_expires_epoch_us=1160000000,original_application_deadline_us=170000000,
                    service_object_durable=True,service_object_sha256=pair['descriptor']['sha256'],
                    disarmed_on_exit=True,attempt_journal=dict(fenced=False),
                    native_job=dict(pid=2000+UNITS.index(peer),start_ticks=17),
                    grant_context_sha256=hashlib.sha256(run.encode()).hexdigest())
        return handle
    def status(self,unit,run):
        handle=self.handles.get((unit,run))
        live=handle is not None and handle.poll() is None
        if self.fault=='terminal_live' and handle is not None and handle.poll()==0:live=True
        return dict(unit=unit,run_id=run,host_boot_id=BOOTS[unit],kernel_handle_opened=True,
            live=live,ready=live and self.fault!='not_ready',pid=2000+UNITS.index(unit),start_ticks=17,
            grant_context_sha256=hashlib.sha256(run.encode()).hexdigest())
    def collect(self,unit,run):
        value=dict(self.results[(unit,run)])
        if self.fault=='rebound_boot':value['host_boot_id']=BOOTS['unit-b']
        if self.fault=='not_disarmed':value['disarmed_on_exit']=False
        if self.fault=='fenced':value['attempt_journal']=dict(fenced=True)
        self.events.append(('collect',unit,run));return value
    def revoke(self,unit,run,transport):
        self.events.append(('revoke',unit,run));return dict(unit=unit,run_id=run,retired=True)
    def stop(self,unit,run):self.events.append(('stop',unit,run));return dict(live=False,safe_handoff_proven=False)
    def reap(self,handle):self.events.append(('reap',))
    def verify(self,unit,campaign,request,response):
        self.events.append(('verify',unit,request,response))
        return dict(schema='meshticulum.pilot-verified-piece.v1',private_admission=False,sage_imported=False,
            unit=unit,campaign_id=campaign,response_run_id=response,manifest_sha256=MANIFEST,
            chunk_sha256='0'*64 if self.fault=='wrong_verified_chunk' else CHUNK,publisher_verified=True,
            original_application_expires_epoch_us=1160000000,original_application_deadline_us=170000000)


def controller(fault=None):
    clock=Clock();operator=ModelOperator(clock,fault)
    return Controller(operator,campaign_id='d'*32,resource_sha256=MANIFEST,expires_epoch_us=1160000000,
        handoff_deadline_monotonic=200,peers=PEERS,boots=BOOTS,radio_permission=PERMISSION,
        clock=clock.mono,epoch=clock.epoch,sleep=clock.sleep),operator,clock


def test_four_stage_graph_keeps_original_expiry_and_actual_local_source_ids():
    pilot,op,clock=controller()
    pilot.mixed_piece(REQUEST,RESPONSE)
    assert [(x['sender'],x['receiver'],x['transport'],x['format']) for x in pilot.links]==[
        ('unit-c','unit-a','lora','MR1.request'),('unit-a','unit-b','tcp','MR1.request'),
        ('unit-b','unit-a','tcp','MR1.response'),('unit-a','unit-c','lora','MR1.response')]
    assert len({x['run_id'] for x in pilot.links})==4
    packets=[e[2] for e in op.events if e[0]=='prepare']
    assert all(p['application_expires_epoch_us']==1160000000 for p in packets)
    senders=[p for p in packets if p['source_run_id'] is not None or p['payload_base64'] is not None]
    assert senders[1]['source_run_id']==pilot.links[0]['run_id']
    assert senders[2]['source_run_id']==dict(kind='served_public_chunk',request_run_id=pilot.links[1]['run_id'])
    assert senders[3]['source_run_id']==pilot.links[2]['run_id']
    assert all(p['payload_base64'] is None for p in senders[1:])
    assert all(x['phase']=='complete' for x in pilot.links) and len(pilot.verified)==1
    pilot.retire()
    assert len([x for x in op.events if x[0]=='revoke'])==8


@pytest.mark.parametrize('fault',['not_ready','terminal_live','rebound_boot','not_disarmed','fenced'])
def test_failed_native_or_cleanup_evidence_stops_before_next_hop_and_retires_both_children(fault):
    pilot,op,clock=controller(fault)
    with pytest.raises((WireError,RuntimeError)):pilot.mixed_piece(REQUEST,RESPONSE)
    assert len(pilot.links)==1 and not pilot.verified
    assert len([x for x in op.events if x[0]=='revoke'])==2
    assert len([x for x in op.events if x[0]=='stop'])==2


def test_uncertain_prepare_failure_retires_both_attempted_children_before_any_start():
    pilot,op,clock=controller('prepare_sender')
    with pytest.raises(RuntimeError):pilot.mixed_piece(REQUEST,RESPONSE)
    assert [x[1] for x in op.events if x[0]=='revoke']==['unit-a','unit-c']
    assert not [x for x in op.events if x[0]=='start']


def test_bound_original_handoff_deadline_cannot_be_extended_by_a_later_stage():
    pilot,op,clock=controller()
    clock.now=pilot.deadline
    with pytest.raises(WireError,match='pilot_original_resource_or_handoff_expired'):
        pilot.mixed_piece(REQUEST,RESPONSE)
    assert not op.events


def test_publisher_flag_does_not_accept_a_different_verified_chunk():
    pilot,op,clock=controller('wrong_verified_chunk')
    with pytest.raises(WireError,match='pilot_native_piece_verification_missing_or_rebound'):
        pilot.mixed_piece(REQUEST,RESPONSE)
    assert len(pilot.links)==4 and not pilot.verified


def test_radio_packages_require_explicit_confirmation_without_activating_anything():
    clock=Clock()
    with pytest.raises(WireError,match='pilot_explicit_radio_confirmation_required'):
        pair_packages(campaign_id='d'*32,resource_sha256=MANIFEST,application_expires_epoch_us=1160000000,
            sender='unit-c',receiver='unit-a',transport='lora',format='MR1.request',
            descriptor=dict(bytes=len(REQUEST),sha256=hashlib.sha256(REQUEST).hexdigest()),source=None,
            request=REQUEST,peers=PEERS,boots=BOOTS,now_epoch_us=clock.epoch())
