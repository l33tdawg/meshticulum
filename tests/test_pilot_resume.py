"""Finite resume/policy controls with invented native records; no device proof."""
import base64

import pytest
from meshticulum.bench.pilot_controller import Controller
from meshticulum.bench.wire import WireError
from meshticulum.resources import resource
from tests.test_pilot_controller import ModelOperator,Clock,PEERS,BOOTS,PERMISSION,MANIFEST


class ResumeOperator(ModelOperator):
    def __init__(self,clock,*,slower=False,cache_fault=False):
        super().__init__(clock);self.have=set();self.slower=slower;self.cache_fault=cache_fault;self.transfers=[]
    def start(self,unit,pair):
        handle=super().start(unit,pair)
        if unit==pair['sender']:
            self.transfers.append((pair['transport'],pair['format'],pair.get('test_rx_cutoff')))
            for peer in (pair['sender'],pair['receiver']):
                result=self.results[(peer,pair['run_id'])];result['start_monotonic_us']=10000000
                if pair.get('test_rx_cutoff') is not None:
                    self.handles[(peer,pair['run_id'])].code=1;result['complete']=False
                    result['service_object_durable']=False
                    result['test_tcp_loss']=dict(injected=True,known_rx_stream_bytes=pair['test_rx_cutoff'])
                    result['byte_journal']=dict(fenced=True)
        return handle
    def verify(self,unit,campaign,request,response):
        value=super().verify(unit,campaign,request,response)
        packet=self.packages[(unit,request)]
        raw=base64.b64decode(packet['payload_base64'])
        digest=resource.decode_request(raw)[0].rsplit('/',1)[-1][:-5]
        self.have.add(digest);value['chunk_sha256']=digest
        transport=packet['transport']
        duration=1000000 if transport=='lora' else (2000000 if self.slower else 100000)
        value.update(verified_monotonic_us=10000000+duration,useful_verified_bytes=1000)
        return value
    def cache_status(self,unit,campaign):
        have=set(self.have)
        if self.cache_fault and len(self.transfers)>=4:have.add('0'*64)
        return dict(schema='meshticulum.public-pilot-cache-status.v1',unit=unit,campaign_id=campaign,
            host_boot_id=BOOTS[unit],manifest_sha256=MANIFEST,verified_chunks=sorted(have),
            original_application_expires_epoch_us=1160000000)


def setup(*,slower=False,cache_fault=False):
    clock=Clock();op=ResumeOperator(clock,slower=slower,cache_fault=cache_fault)
    pilot=Controller(op,campaign_id='d'*32,resource_sha256=MANIFEST,expires_epoch_us=1160000000,
        handoff_deadline_monotonic=200,peers=PEERS,boots=BOOTS,radio_permission=PERMISSION,
        clock=clock.mono,epoch=clock.epoch,sleep=clock.sleep)
    exchanges=[]
    for char in 'abcd':
        request=resource.encode_request('/packs/'+MANIFEST+'/chunks/'+char*64+'.json',0,16356)
        exchanges.append((request,resource.BlockServer(lambda _:b'x'*8192).handle(request)))
    return pilot,op,exchanges


def test_original_resource_survives_loss_and_only_fresh_measured_faster_ip_is_returned_to():
    pilot,op,exchanges=setup()
    result=pilot.failure_resume(exchanges)
    assert result['returned_to_ip'] and result['faster_ip_observed']
    assert len(pilot.verified)==4 and len(pilot.links)==10
    assert [t for t,format,_ in op.transfers if format=='MR1.response']==['tcp','tcp','lora','tcp','tcp']
    assert sum(1 for row in pilot.links if row['phase']=='expected_tcp_loss')==1
    assert result['reused_verified_chunks']==['a'*64]
    assert len({row['run_id'] for row in pilot.links})==10
    assert all(p['application_expires_epoch_us']==1160000000 for p in op.packages.values())


def test_slower_new_ip_sample_keeps_faster_ip_qualification_open():
    pilot,op,exchanges=setup(slower=True)
    result=pilot.failure_resume(exchanges)
    assert not result['faster_ip_observed'] and not result['returned_to_ip'] and result['return_piece'] is None
    assert len(pilot.verified)==3 and len(pilot.links)==8


def test_partial_interrupted_piece_cannot_be_promoted_by_cache_metadata():
    pilot,op,exchanges=setup(cache_fault=True)
    with pytest.raises(WireError,match='pilot_incomplete_piece_entered_cache'):
        pilot.failure_resume(exchanges)
    assert len(pilot.links)==4 and len(pilot.verified)==1
