"""Finite public-pilot stages over an explicitly supplied management operator.

The operator owns actual child handles, native status, preparation, collection,
verification and revocation. This module installs no runtime, discovers no host
and grants no RF window. A source/model operator provides source evidence only.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import secrets
import time
import uuid

from ..contracts.identity import PeerID
from ..ip.wire import IpLimits
from ..resources import resource
from .pilot_provision import identifier
from .profiles import profile_for
from .wire import require

UNITS = ('unit-a', 'unit-b', 'unit-c')


def pair_packages(*, campaign_id, resource_sha256, application_expires_epoch_us,
                  sender, receiver, transport, format, descriptor, source,
                  request, peers, boots, now_epoch_us, endpoints=None, profile='sf7-500', radio_permission=None):
    """Fresh independent adjacent-link sessions; receiver never gets a body."""
    identifier(campaign_id)
    require(sender in UNITS and receiver in UNITS and sender != receiver
            and transport in ('lora', 'tcp') and format in ('MR1.request', 'MR1.response'), 'invalid_pilot_pair')
    require(type(application_expires_epoch_us) is int and type(now_epoch_us) is int
            and now_epoch_us < application_expires_epoch_us <= now_epoch_us + 180000000,
            'invalid_pilot_original_expiry')
    require(type(descriptor) is dict and set(descriptor) == {'bytes','sha256'}
            and type(descriptor['bytes']) is int and 0 < descriptor['bytes'] <= 16384
            and len(bytes.fromhex(descriptor['sha256'])) == 32
            and len(bytes.fromhex(resource_sha256)) == 32, 'invalid_pilot_pair_object')
    require((source is None and format == 'MR1.request' and type(request) is bytes
             and len(request) == descriptor['bytes'] and hashlib.sha256(request).hexdigest() == descriptor['sha256'])
            or (source is not None and request is None), 'pilot_pair_payload_provenance_required')
    for unit in (sender,receiver):
        require(str(uuid.UUID(boots[unit])) == boots[unit], 'invalid_pilot_pair_boot')
        PeerID(**peers[unit])
    run_id, generation, key = secrets.token_hex(16), secrets.token_hex(16), secrets.token_bytes(32)
    shared = dict(issued_epoch_us=max(1,now_epoch_us-1000000), expires_epoch_us=application_expires_epoch_us)
    values = {}
    for unit in (sender,receiver):
        if transport == 'tcp':
            require(type(endpoints) is dict and set(endpoints) == {sender,receiver}, 'pilot_exact_ip_endpoints_required')
            route = dict(campaign_id=campaign_id,resource_sha256=resource_sha256,
                sender=sender,receiver=receiver,transport=transport,format=format)
            grant = dict(shared,schema='meshticulum.ip-service-run.v1',host_boot_id=boots[unit],
                local_peer=peers[unit],session=dict(initiator=peers[sender],responder=peers[receiver],
                    session_id=run_id,generation=generation),format=format,object=descriptor,
                route_binding_digest=hashlib.sha256(json.dumps(route,sort_keys=True).encode()).hexdigest(),
                application_expires_epoch_us=application_expires_epoch_us,
                application_deadline_monotonic_us=None,initiator_address=list(endpoints[sender]),
                responder_address=list(endpoints[receiver]),limits=asdict(IpLimits()))
        else:
            require(type(radio_permission) is dict and radio_permission == {'country':'MY','antenna_confirmed':True},
                    'pilot_explicit_radio_confirmation_required')
            grant = dict(shared,schema='meshticulum.service-run.v1',unit=unit,host_boot_id=boots[unit],
                local_peer=peers[unit],initiator=peers[sender],responder=peers[receiver],
                session_id=run_id,generation=generation,profile=asdict(profile_for(profile)),
                object=dict(descriptor,object_id=descriptor['sha256'],chunk_index=0),
                sender_unit=sender,country='MY',antenna_confirmed=True,max_frames=512,
                service=dict(kind='knowledge',format=format,application_expires_epoch_us=application_expires_epoch_us))
        values[unit] = dict(schema='meshticulum.public-pilot-package.v1',unit=unit,
            campaign_id=campaign_id,resource_sha256=resource_sha256,
            application_expires_epoch_us=application_expires_epoch_us,transport=transport,grant=grant,
            secret_base64=base64.b64encode(key).decode(),
            payload_base64=base64.b64encode(request).decode() if unit==sender and request is not None else None,
            source_run_id=source if unit==sender else None)
    return dict(run_id=run_id,sender=sender,receiver=receiver,transport=transport,format=format,
        descriptor=descriptor,packages=values)


class Controller:
    def __init__(self, operator, *, campaign_id, resource_sha256, expires_epoch_us,
                 handoff_deadline_monotonic, peers, boots, radio_permission=None,
                 clock=time.monotonic, epoch=None, sleep=time.sleep):
        self.operator,self.clock,self.sleep=operator,clock,sleep
        self.epoch=epoch or (lambda:time.time_ns()//1000)
        self.campaign_id=identifier(campaign_id);self.resource_sha256=resource_sha256
        self.expires_epoch_us=expires_epoch_us;self.peers,self.boots=peers,boots
        self.radio_permission=radio_permission
        now=self.clock();remaining=(expires_epoch_us-self.epoch())/1e6
        require(0 < remaining <= 180 and handoff_deadline_monotonic > now+10,
                'pilot_current_finite_window_required')
        # Reserve ten seconds for revocation and owned-job cleanup.
        self.deadline=min(now+remaining,handoff_deadline_monotonic-10)
        self.last_clock=(self.epoch(),now)
        self.links=[];self.verified=[];self.cleanup=[]

    def current(self):
        mono,epoch=self.clock(),self.epoch()
        require(epoch>=self.last_clock[0] and mono>=self.last_clock[1], 'pilot_controller_clock_regressed')
        self.last_clock=(epoch,mono)
        require(mono<self.deadline and epoch<self.expires_epoch_us,
                'pilot_original_resource_or_handoff_expired')

    def link(self,sender,receiver,transport,format,payload,source=None):
        self.current()
        descriptor=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest())
        pair=pair_packages(campaign_id=self.campaign_id,resource_sha256=self.resource_sha256,
            application_expires_epoch_us=self.expires_epoch_us,sender=sender,receiver=receiver,
            transport=transport,format=format,descriptor=descriptor,source=source,
            request=payload if source is None else None,peers=self.peers,boots=self.boots,
            now_epoch_us=self.epoch(),endpoints=self.operator.endpoints(sender,receiver) if transport=='tcp' else None,
            radio_permission=self.radio_permission)
        row={k:v for k,v in pair.items() if k!='packages'}
        row.update(phase='preparing',started_monotonic=self.clock(),outcomes={})
        self.links.append(row);handles={};prepared=[];row['prepared']=prepared
        row['attempted']=[];row['retired']=[]
        try:
            for unit in (receiver,sender):
                self.current();row['attempted'].append(unit)
                self.operator.prepare(pair['packages'][unit]);prepared.append(unit)
            row['phase']='starting_receiver'
            handles[receiver]=self.operator.start(receiver,pair)
            setup_deadline=min(self.deadline,self.clock()+10)
            while True:
                self.current()
                require(handles[receiver].poll() is None and self.clock()<setup_deadline,
                        'pilot_receiver_failed_or_setup_expired')
                status=self.operator.status(receiver,pair['run_id'])
                if status.get('kernel_handle_opened') is True and status.get('live') is True and status.get('ready') is True:
                    require(status['unit']==receiver and status['run_id']==pair['run_id']
                            and status['host_boot_id']==self.boots[receiver], 'pilot_receiver_identity_changed')
                    break
                self.sleep(.02)
            handles[sender]=self.operator.start(sender,pair)
            row['phase']='running'
            while any(handle.poll() is None for handle in handles.values()):
                self.current();self.sleep(.02)
            for unit in (sender,receiver):
                status=self.operator.status(unit,pair['run_id'])
                require(status['unit']==unit and status['host_boot_id']==self.boots[unit]
                        and status['run_id']==pair['run_id'] and status['live'] is False,
                        'pilot_native_process_not_terminal')
                result=self.operator.collect(unit,pair['run_id'])
                row['outcomes'][unit]=result
                require(handles[unit].poll()==0 and result['complete'] is True
                        and result['unit']==unit and result['host_boot_id']==self.boots[unit]
                        and result['session_id']==pair['run_id'] and result['service_format']==format
                        and result['sends'] is (unit==sender)
                        and result['original_application_expires_epoch_us']==self.expires_epoch_us
                        and result['object']['bytes']==descriptor['bytes']
                        and result['object']['sha256']==descriptor['sha256']
                        and not result.get('cleanup_errors'), 'pilot_link_incomplete_or_rebound')
                require(result['native_job']['pid']==status['pid']
                        and result['native_job']['start_ticks']==status['start_ticks']
                        and result['grant_context_sha256']==status['grant_context_sha256'],
                        'pilot_result_process_binding_changed')
                if transport=='lora':
                    require(result.get('disarmed_on_exit') is True
                            and result['attempt_journal']['fenced'] is False, 'pilot_radio_cleanup_unproven')
                if unit==receiver:
                    require(result.get('service_object_durable') is True
                            and result['service_object_sha256']==descriptor['sha256'], 'pilot_receiver_capture_missing')
            row.update(phase='complete',ended_monotonic=self.clock())
            return pair['run_id']
        except BaseException as error:
            row.update(phase='failed',error=type(error).__name__+':'+str(error)[:200])
            self._retire(row,stop=True)
            raise
        finally:
            for handle in handles.values(): self.operator.reap(handle)

    def mixed_piece(self,request,response):
        """Actual C→A RF→B IP request; actual B→A IP→C RF response."""
        path,offset,size=resource.decode_request(request)
        require(offset==0 and path.startswith('/packs/'+self.resource_sha256+'/chunks/')
                and path.endswith('.json'), 'pilot_requires_whole_selected_public_chunk')
        chunk_sha256=path.rsplit('/',1)[-1][:-5]
        try:
            first=self.link('unit-c','unit-a','lora','MR1.request',request)
            second=self.link('unit-a','unit-b','tcp','MR1.request',request,source=first)
            third=self.link('unit-b','unit-a','tcp','MR1.response',response,
                source=dict(kind='served_public_chunk',request_run_id=second))
            fourth=self.link('unit-a','unit-c','lora','MR1.response',response,source=third)
            self.current()
            verified=self.operator.verify('unit-c',self.campaign_id,first,fourth)
            self.current()
            require(verified['schema']=='meshticulum.pilot-verified-piece.v1'
                    and verified['publisher_verified'] is True and verified['private_admission'] is False
                    and verified['sage_imported'] is False and verified['campaign_id']==self.campaign_id
                    and verified['manifest_sha256']==self.resource_sha256
                    and verified['unit']=='unit-c' and verified['response_run_id']==fourth
                    and verified['chunk_sha256']==chunk_sha256
                    and verified['original_application_expires_epoch_us']==self.expires_epoch_us
                    and type(verified['original_application_deadline_us']) is int
                    and 0 < verified['original_application_deadline_us'] <= min(
                        self.links[0]['outcomes']['unit-c']['original_application_deadline_us'],
                        self.links[-1]['outcomes']['unit-c']['original_application_deadline_us']),
                    'pilot_native_piece_verification_missing_or_rebound')
            self.verified.append(verified)
            return verified
        except BaseException:
            self.retire()
            raise

    def _retire(self,row,*,stop=False):
        for unit in row['attempted']:
            if unit in row['retired']: continue
            evidence=dict(unit=unit,run_id=row['run_id'],safe_handoff_proven=False)
            try:
                value=self.operator.revoke(unit,row['run_id'],row['transport'])
                require(value['retired'] is True and value['unit']==unit and value['run_id']==row['run_id'],
                        'pilot_retirement_unconfirmed')
                row['retired'].append(unit);evidence['retirement']=value
            except BaseException as error:
                evidence['retirement_error']=type(error).__name__
            if stop:
                try:evidence['stop_status']=self.operator.stop(unit,row['run_id'])
                except BaseException as error:evidence['stop_error']=type(error).__name__
            self.cleanup.append(evidence)

    def retire(self):
        """Explicitly retire every prepared child; never refunds its journal."""
        for row in self.links:
            self._retire(row)
        return self.cleanup
