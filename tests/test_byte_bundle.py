"""Actual public fixture bytes/signatures/files; no native transport claims."""
import base64
from copy import deepcopy
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meshticulum.resources import knowledge_pack as kp,byte_bundle as bb,resource

SEED=b'Meshticulum SAGE Foundation public reproducible network payload 2026-10-11 v1'
PINS={100000:'dbc6cad927a8f49cfe38fd6a0616f0939d3e49b59fec91999ac09bf6e16fbfb8',
      1000000:'3bc41059ce9fc88e5b24557b8f1d916830e7eefa536beae508d553a66188ff36'}


@pytest.fixture
def signer():
    key=Ed25519PrivateKey.generate();public=kp.public_key_hex(key)
    trust=dict(schema='sage.knowledge-pack.trust-root.v1',keys=[dict(key_id=kp.key_id_for(public),
        algorithm='ed25519',public_key_hex=public,status='active',created_at='2026-10-11T00:00:00Z')])
    return key,trust


def payload(n=100000):
    raw=b''.join(hashlib.sha256(SEED+i.to_bytes(8,'big')).digest() for i in range((n+31)//32))[:n]
    assert hashlib.sha256(raw).hexdigest()==PINS[n]
    return raw


def package(signer,n=100000,exact32=False):
    key,trust=signer;manifest,chunks,sig=bb.build(payload(n),pack_id='public-byte-bundle',
        created_at='2026-10-11T00:00:00Z',private_key=key,exact32=exact32)
    raw=kp.manifest_bytes(manifest)
    return raw,kp.canonical_json(sig),trust,kp.sha256_hex(raw),manifest,chunks


class Clock:
    def __init__(self):self.epoch,self.mono=100000000,800000000
    def __call__(self):return self.epoch,self.mono


def receive(path,p,clock,expiry=2800000000,boot='test-host-boot'):
    path.mkdir(mode=0o700,exist_ok=True)
    return bb.Receiver(path,*p[:4],expires_epoch_us=expiry,clock=clock,boot_reader=lambda:boot)


def inputs(p):return [kp.chunk_bytes(p[5][entry['sha256']]) for entry in p[4]['chunks']]


@pytest.mark.parametrize('n',[100000,1000000])
@pytest.mark.parametrize('exact32',[False,True])
def test_pinned_file_roundtrip_signatures_whole_sha_and_honest_wire_counts(tmp_path,signer,n,exact32):
    p=package(signer,n,exact32);parts=inputs(p);assert len(parts)==(5 if n==100000 else 42)
    assert all(len(raw)<=32740 for raw in parts)
    if exact32:assert set(map(len,parts))=={32740}
    with receive(tmp_path/'receiver',p,Clock()) as receiver:
        for index,raw in enumerate(parts):receiver.accept(index,raw)
        result=receiver.finish()
    assert (tmp_path/'receiver/bundle.bin').read_bytes()==payload(n)
    assert result['complete'] and result['publisher_verified'] and result['whole_file_sha256']==PINS[n]
    assert result['useful_decoded_bytes']==n and result['native_transport_qualified'] is False
    assert result['serialized_chunk_bytes']==sum(map(len,parts))
    assert result['serialized_chunk_bytes']==result['base64_bytes']+result['json_overhead_bytes']+result['padding_bytes']
    assert result['base64_bytes']>=n*4//3 and (result['padding_bytes']>0)==exact32


def test_verified_only_resume_keeps_original_context_and_whole_hash(tmp_path,signer):
    p=package(signer);clock=Clock();path=tmp_path/'receiver';parts=inputs(p)
    with receive(path,p,clock) as receiver:receiver.accept(0,parts[0])
    original=(path/'context.json').read_bytes();clock.epoch+=1000000;clock.mono+=1000000
    with receive(path,p,clock) as receiver:
        assert receiver.reused==1
        for index,raw in enumerate(parts[1:],1):receiver.accept(index,raw)
        result=receiver.finish()
    assert (path/'context.json').read_bytes()==original
    assert result['reused_verified_parts']==1 and (path/'bundle.bin').read_bytes()==payload()


@pytest.mark.parametrize('fault',['missing','reordered','duplicate','expiry','monotonic_expiry','foreign_manifest','tampered_cache','parts_rebound'])
def test_failure_cannot_become_whole_file_success(tmp_path,signer,fault):
    p=package(signer);parts=inputs(p);clock=Clock();path=tmp_path/'receiver'
    with receive(path,p,clock) as receiver:
        if fault=='missing':
            receiver.accept(0,parts[0])
            with pytest.raises(FileNotFoundError):receiver.finish()
        elif fault=='reordered':
            with pytest.raises(ValueError):receiver.accept(0,parts[1])
        elif fault=='duplicate':
            receiver.accept(0,parts[0])
            with pytest.raises(ValueError):receiver.accept(0,parts[0])
        elif fault in ('expiry','monotonic_expiry'):
            for index,raw in enumerate(parts):receiver.accept(index,raw)
            if fault=='expiry':clock.epoch=2800000000
            else:clock.mono=3500000000
            with pytest.raises(ValueError):receiver.finish()
        elif fault=='foreign_manifest':
            foreign=package(signer,1000000)
            with pytest.raises(ValueError):receiver.accept(0,inputs(foreign)[0])
        elif fault=='tampered_cache':
            for index,raw in enumerate(parts):receiver.accept(index,raw)
            target=next((path/'parts').iterdir());target.write_bytes(b'{}')
            with pytest.raises(ValueError):receiver.finish()
        else:
            (path/'parts').rename(path/'retained-parts');(path/'parts').mkdir(mode=0o700)
            with pytest.raises(ValueError):receiver.accept(0,parts[0])
    assert not (path/'bundle.bin').exists()


@pytest.mark.parametrize('fault',['overlap','wrong_size','wrong_hash','bad_base64','extra_base64_padding','bad_count','oversized_part','embedding'])
def test_typed_bundle_part_validation_refuses_bad_content(signer,fault):
    p=package(signer);chunk=deepcopy(next(iter(p[5].values())));part=chunk['bundle_part']
    if fault=='overlap':part['offset']+=1
    elif fault=='wrong_size':part['raw_bytes']-=1
    elif fault=='wrong_hash':part['raw_sha256']='0'*64
    elif fault=='bad_base64':part['payload_base64']='!'
    elif fault=='extra_base64_padding':part['payload_base64']+='===='
    elif fault=='bad_count':part['parts']+=1
    elif fault=='oversized_part':part['part_bytes']=24001
    else:chunk['embedding']=dict(model='x',dimension=1,vector_f32_le=base64.b64encode(b'\0'*4).decode())
    chunk['sha256']=kp.content_address(chunk)
    with pytest.raises(ValueError):kp.validate_chunk(chunk)


@pytest.mark.parametrize('fault',['extend_expiry','different_boot','unverified_resume','corrupt_resume','revoked_signer','overlap_manifest'])
def test_resume_and_signed_descriptor_refusals(tmp_path,signer,fault):
    p=package(signer);parts=inputs(p);clock=Clock();path=tmp_path/'receiver'
    if fault=='unverified_resume':
        path.mkdir(mode=0o700);(path/'parts').mkdir(mode=0o700)
        (path/'parts'/(p[4]['chunks'][0]['sha256']+'.json')).write_bytes(parts[0])
    else:
        with receive(path,p,clock) as receiver:receiver.accept(0,parts[0])
    kwargs={}
    if fault=='extend_expiry':kwargs['expiry']=2800000001
    elif fault=='different_boot':kwargs['boot']='other-host-boot'
    elif fault=='corrupt_resume':next((path/'parts').iterdir()).write_bytes(b'{}')
    elif fault=='revoked_signer':p[2]['keys'][0]['status']='revoked'
    elif fault=='overlap_manifest':
        manifest=deepcopy(p[4]);desc=json.loads(manifest['pack']['provenance']['note']);desc['parts'][1]['offset']=0
        manifest['pack']['provenance']['note']=kp.canonical_json(desc).decode();raw=kp.manifest_bytes(manifest)
        p=(raw,kp.canonical_json(kp.sign_manifest(raw,signer[0])),p[2],kp.sha256_hex(raw),manifest,p[5])
    with pytest.raises(ValueError):receive(path,p,clock,**kwargs)
    assert not (path/'bundle.bin').exists()


def test_signed_per_part_success_is_insufficient_if_whole_file_hash_is_wrong(tmp_path,signer):
    p=package(signer);manifest=deepcopy(p[4]);desc=json.loads(manifest['pack']['provenance']['note']);desc['sha256']='0'*64
    chunks={}
    for index,entry in enumerate(manifest['chunks']):
        chunk=deepcopy(p[5][entry['sha256']]);chunk['bundle_part']['bundle_sha256']='0'*64
        chunk['sha256']=kp.content_address(chunk);entry['sha256']=chunk['sha256'];chunks[chunk['sha256']]=chunk
        desc['parts'][index]['chunk_sha256']=chunk['sha256']
    manifest['pack']['provenance']['note']=kp.canonical_json(desc).decode();raw=kp.manifest_bytes(manifest)
    q=(raw,kp.canonical_json(kp.sign_manifest(raw,signer[0])),p[2],kp.sha256_hex(raw),manifest,chunks)
    with receive(tmp_path/'receiver',q,Clock()) as receiver:
        for index,data in enumerate(inputs(q)):receiver.accept(index,data)
        with pytest.raises(ValueError,match='whole public bundle mismatch'):receiver.finish()
    assert not (tmp_path/'receiver/bundle.bin').exists()


def test_legacy_none_field_hash_and_private_default_limits_stay_unchanged():
    old=kp.build_chunk('legacy','Reference bytes',licence='CC0-1.0',source='public',date='2026-10-11')
    same=kp.build_chunk('legacy','Reference bytes',licence='CC0-1.0',source='public',date='2026-10-11',bundle_part=None)
    assert old==same and 'bundle_part' not in old
    assert old['sha256']=='7b1aa72406895fd29472304098996537cfd19958ec83aebe2d13fde73d956d9d'
    assert kp.content_object(old)=={key:old[key] for key in kp.CHUNK_CONTENT_FIELDS}
    assert resource.MAX_BLOCK==16384
    with pytest.raises(ValueError):resource.encode_request('/packs/'+'a'*64+'/chunks/'+'b'*64+'.json',0,32740)

@pytest.mark.parametrize('target',['part','whole'])
def test_publication_uses_held_directory_even_if_name_replaced_at_link(tmp_path,signer,monkeypatch,target):
    import os
    p=package(signer);clock=Clock();path=tmp_path/'receiver';parts=inputs(p)
    with receive(path,p,clock) as receiver:
        if target=='whole':
            for index,raw in enumerate(parts):receiver.accept(index,raw)
        original=os.link;observed={}
        def replace(source,name,**kwargs):
            if name==('bundle.bin' if target=='whole' else p[4]['chunks'][0]['sha256']+'.json'):
                directory=path if target=='whole' else path/'parts'
                retained=tmp_path/'held-original';directory.rename(retained);directory.mkdir(mode=0o700)
                observed['retained']=retained
                assert kwargs['src_dir_fd']==kwargs['dst_dir_fd']==(receiver.fd if target=='whole' else receiver.parts_fd)
            return original(source,name,**kwargs)
        monkeypatch.setattr(os,'link',replace)
        with pytest.raises(ValueError):
            if target=='whole':receiver.finish()
            else:receiver.accept(0,parts[0])
        wrong=path/'bundle.bin' if target=='whole' else path/'parts'/(p[4]['chunks'][0]['sha256']+'.json')
        assert not wrong.exists()
        owned=observed['retained']/('bundle.bin' if target=='whole' else p[4]['chunks'][0]['sha256']+'.json')
        assert owned.is_file()
        assert not list(observed['retained'].glob('.bundle-*'))

@pytest.mark.parametrize('where',['staging','published'])
def test_same_inode_content_mutation_cannot_produce_complete_true(tmp_path,signer,monkeypatch,where):
    import os
    p=package(signer);clock=Clock();path=tmp_path/'receiver';parts=inputs(p)
    with receive(path,p,clock) as receiver:
        for index,raw in enumerate(parts):receiver.accept(index,raw)
        if where=='staging':
            original=receiver._live
            def tamper():
                original()
                for target in path.glob('.bundle-*'):
                    before=target.stat();target.write_bytes(b'x'*100000)
                    assert target.stat().st_ino==before.st_ino
            monkeypatch.setattr(receiver,'_live',tamper)
        else:
            original=os.link
            def tamper(source,name,**kwargs):
                result=original(source,name,**kwargs)
                if name=='bundle.bin':
                    fd=os.open(name,os.O_WRONLY|os.O_NOFOLLOW,dir_fd=kwargs['dst_dir_fd'])
                    try:os.write(fd,b'x')
                    finally:os.close(fd)
                return result
            monkeypatch.setattr(os,'link',tamper)
        with pytest.raises(ValueError):receiver.finish()

@pytest.mark.parametrize('target',['parsed','retained_file'])
def test_clock_callback_cannot_extend_or_mutate_original_context(tmp_path,signer,target):
    p=package(signer);path=tmp_path/'receiver';clock=Clock();holder={}
    def observed():
        receiver=holder.get('receiver')
        if receiver is not None:
            if target=='parsed':
                receiver.context['expires_epoch_us']+=1000000000
                receiver.context['deadline_monotonic_us']+=1000000000
            else:
                changed=dict(receiver.context,expires_epoch_us=3800000000,deadline_monotonic_us=4500000000)
                (path/'context.json').write_bytes(kp.canonical_json(changed))
        return clock()
    with receive(path,p,observed) as receiver:
        for index,raw in enumerate(inputs(p)):receiver.accept(index,raw)
        holder['receiver']=receiver;clock.epoch=2800000001;clock.mono=3500000001
        with pytest.raises(ValueError):receiver.finish()
    assert not (path/'bundle.bin').exists()
