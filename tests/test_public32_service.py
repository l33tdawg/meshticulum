"""Real AEAD/flow/public-publisher controls with modeled RF; no native evidence."""
import hashlib
import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meshticulum.bench.authority import revoke
from meshticulum.bench.services import ServiceTransfer, _shape
from meshticulum.bench.serial_worker import AttemptResult
from meshticulum.bench.wire import WireError
from meshticulum.resources import knowledge_pack as kp, lamplighter as ll, resource
from tests.service_helpers import scope
from tests.test_meshticulum_private_carrier import carrier_pair
from tests.test_meshticulum_bench_runtime import Clock


@pytest.fixture
def large_pack(tmp_path):
    key = Ed25519PrivateKey.generate(); public = kp.public_key_hex(key)
    trust = dict(schema='sage.knowledge-pack.trust-root.v1',keys=[dict(key_id=kp.key_id_for(public),
        algorithm='ed25519',public_key_hex=public,status='active',created_at='2026-10-11T00:00:00Z')])
    # Existing text cap and the no-embedding public-store rule stay intact.
    # This signed synthetic metadata-heavy record tests the complete byte cap.
    base = kp.build_chunk('public32','Synthetic public text',licence='CC0-1.0',
                         source='synthetic',date='2026-10-11')
    title = 'x' * (32740-len(kp.chunk_bytes(base)))
    chunk = kp.build_chunk('public32','Synthetic public text',title=title,licence='CC0-1.0',
                          source='synthetic',date='2026-10-11')
    raw = kp.chunk_bytes(chunk); assert len(raw)==32740
    manifest = kp.build_manifest(pack_id='public32-source',version=1,pack_class='reference',title='Synthetic',
        licence='CC0-1.0',provenance={'source':'synthetic'},created_at='2026-10-11T00:00:00Z',chunks=[chunk],
        embedding_model='test',embedding_dimension=1)
    encoded = kp.manifest_bytes(manifest); directory = tmp_path/'signed-seed'
    kp.write_pack(directory,manifest,{chunk['sha256']:chunk},kp.sign_manifest(encoded,key))
    return directory,trust,ll.sha(encoded),manifest,raw


def service(clock,fmt='MR1.response',**extra):
    return dict(scope(fmt,clock),public_response_max_bytes=32768,**extra)


def response(pack):
    directory,trust,digest,manifest,raw=pack
    cache=ll.VerifiedCache(directory,trust,digest)
    request=resource.encode_request('/packs/'+digest+'/chunks/'+manifest['chunks'][0]['sha256']+'.json',
        0,32740,public32=True)
    reply=resource.BlockServer(cache.read,public32=True).handle(request)
    assert len(reply)==32768 and reply[28:]==raw
    return request,reply


def test_successful_full_signed_public32_uses_real_cipher_credit_and_durable_debt(tmp_path,large_pack):
    request,payload=response(large_pack);clock=Clock()
    with carrier_pair(tmp_path/'flow',payload,clock,'unit-c','unit-a',21,service=service(clock)) as pair:
        sender,receiver=ServiceTransfer(pair.sender),ServiceTransfer(pair.receiver)
        assert pair.sender.maximum_bytes==32768 and pair.sender.flow.limits.max_total_frames==256
        assert pair.sender.authority.profile.max_boot_attempts==256
        assert pair.sender.authority.profile.max_boot_estimated_airtime_us==60000000
        sender.send(payload)
        for _ in range(2200):
            pair.sender.step();pair.receiver.step();clock.advance(50000)
            if pair.sender.flow.complete and pair.receiver.flow.complete: break
        assert pair.sender.flow.complete and pair.receiver.flow.complete
        assert pair.sender.flow.acked_frames()==170
        recovered=receiver.received();assert recovered==payload
        magic,binding,status,total,offset=resource.RESPONSE.unpack(recovered[:28])
        assert binding==hashlib.sha256(request).digest()[:16] and (status,total,offset)==(0,32740,0)
        ll.chunk(recovered[28:],large_pack[3]['chunks'][0])
        kp.validate_pack_rules(large_pack[3],[ll.chunk(recovered[28:],large_pack[3]['chunks'][0])])
        journal=pair.sender.journal.snapshot()
        assert journal['attempts']==170 and journal['reserved_estimated_airtime_us']==34000000
        assert not journal['fenced'] and all(len(p)<=255 for p in pair.workers[0].sent)
        assert b'"synthetic"' not in b''.join(pair.workers[0].sent)
        with pytest.raises(WireError,match='service_send_not_fresh'):sender.send(payload)
        assert pair.sender.journal.snapshot()==journal


def test_large_codec_is_optin_and_rejects_arbitrary_larger_ranges(large_pack):
    request,_=response(large_pack)
    with pytest.raises(ValueError,match='invalid block range'):resource.decode_request(request)
    path,_,_=resource.decode_request(request,public32=True)
    for size in (32741,65535):
        with pytest.raises(ValueError,match='invalid block range'):resource.encode_request(path,0,size,public32=True)
    with pytest.raises(ValueError,match='invalid block range'):resource.encode_request(path,0,32740)


@pytest.mark.parametrize('marker',[None,False,'32768',16384,32769,65536])
def test_invalid_capability_never_opens_flow(tmp_path,marker):
    clock=Clock();s=scope('MR1.response',clock);s['public_response_max_bytes']=marker
    with pytest.raises(WireError,match='invalid_public32_service_capability'):
        with carrier_pair(tmp_path/'invalid',b'x'*32768,clock,'unit-c','unit-a',31,service=s):pass


@pytest.mark.parametrize('fmt',['LME.binary','MPF2.record'])
def test_private_size_and_marker_remain_refused(tmp_path,fmt):
    clock=Clock();s=scope(fmt,clock);s['kind']='protected_message';s['public_response_max_bytes']=32768
    with pytest.raises(WireError):
        with carrier_pair(tmp_path/'private',b'x'*32768,clock,'unit-c','unit-a',41,service=s):pass


def test_default_public_stays16k_and_rejects32k(tmp_path,large_pack):
    _,payload=response(large_pack);clock=Clock()
    with pytest.raises(WireError,match='invalid_bench_object_descriptor'):
        with carrier_pair(tmp_path/'default',payload,clock,'unit-c','unit-a',51,
                          service=scope('MR1.response',clock)):pass


@pytest.mark.parametrize('fault',['short','mismatch','expiry','revoked'])
def test_public32_refusal_precedes_rf_and_preserves_original_deadline(tmp_path,large_pack,fault):
    _,payload=response(large_pack);clock=Clock()
    with carrier_pair(tmp_path/'refusal',payload,clock,'unit-c','unit-a',61,service=service(clock)) as pair:
        sender=ServiceTransfer(pair.sender);deadline=pair.sender.authority.deadlines
        changed=payload
        if fault=='short':changed=payload[:-1]
        if fault=='mismatch':changed=payload[:-1]+b'!'
        if fault=='expiry':clock.advance(61000000)
        if fault=='revoked':revoke(pair.sender.authority.path)
        with pytest.raises(WireError):sender.send(changed)
        assert not pair.workers[0].sent and pair.sender.journal.snapshot()['attempts']==0
        assert pair.sender.authority.deadlines is deadline


def test_unknown_public32_attempt_is_charged_once_then_fenced(tmp_path,large_pack):
    _,payload=response(large_pack);clock=Clock()
    with carrier_pair(tmp_path/'unknown',payload,clock,'unit-c','unit-a',71,service=service(clock)) as pair:
        worker=pair.workers[0]
        def unknown(packet):
            request=worker.journal.reserve(packet,200000)
            worker.journal.settle(request,packet,'UNKNOWN','modeled_spi_unknown')
            worker.fenced=True
            return AttemptResult(request,'UNKNOWN',200000,None)
        worker.transmit=unknown;ServiceTransfer(pair.sender).send(payload)
        with pytest.raises(WireError):
            for _ in range(40):
                pair.receiver.step();pair.sender.step();clock.advance(50000)
        assert worker.journal.snapshot()['attempts']==1
        assert worker.journal.snapshot()['reserved_estimated_airtime_us']==200000
        assert worker.journal.snapshot()['fenced']
