"""Real grant/AEAD/flow composition with modeled RF; explicit MB1 opt-in only."""
import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meshticulum.bench.authority import revoke
from meshticulum.bench.public_block import (REPRESENTATION_MARKER,TRANSPORT_DESCRIPTOR_MARKER,public_mb1_service)
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.wire import FrameKind,WireError
from meshticulum.resources import byte_bundle,bundle_wire,knowledge_pack as kp,resource
from tests.service_helpers import scope
from tests.test_meshticulum_bench_runtime import Clock
from tests.test_meshticulum_private_carrier import carrier_pair


@pytest.fixture
def compact():
    manifest,chunks,_=byte_bundle.build(bytes(range(256))*390+bytes(range(160)),pack_id='mb1-service',
        created_at='2026-10-13T00:00:00Z',private_key=Ed25519PrivateKey.generate(),exact32=True)
    entry=manifest['chunks'][0];raw=kp.chunk_bytes(chunks[entry['sha256']]);wire=bundle_wire.encode_chunk(raw,entry)
    request=resource.encode_request('/packs/'+kp.sha256_hex(kp.manifest_bytes(manifest))+'/chunks/'+entry['sha256']+'.json',0,len(wire),public32=True)
    reply=resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(wire),0)+wire
    digest=kp.sha256_hex(kp.canonical_json(dict(chunk_content_address=entry['sha256'],response_bytes=len(reply),response_sha256=kp.sha256_hex(reply))))
    return reply,digest,raw,entry


def service(clock,digest):
    return dict(scope('MR1.response',clock),public_response_max_bytes=32768,
        **{REPRESENTATION_MARKER:'MB1',TRANSPORT_DESCRIPTOR_MARKER:digest})


def test_explicit_descriptor_bound_MB1_uses_original_cipher_credit_and_full_caps(tmp_path,compact):
    reply,digest,raw,entry=compact;clock=Clock()
    with carrier_pair(tmp_path/'explicit',reply,clock,'unit-c','unit-a',101,service=service(clock,digest)) as pair:
        sender=ServiceTransfer(pair.sender);receiver=ServiceTransfer(pair.receiver)
        assert sender.mb1_descriptor_sha256==receiver.mb1_descriptor_sha256==digest
        assert pair.sender.authority.route_record['service'][TRANSPORT_DESCRIPTOR_MARKER]==digest
        assert pair.sender.maximum_bytes==32768 and pair.sender.flow.limits.max_total_frames==256
        assert pair.sender.authority.profile.max_boot_attempts==256
        assert pair.sender.authority.profile.max_boot_estimated_airtime_us==60000000
        sender.send(reply)
        for _ in range(1800):
            pair.sender.step();pair.receiver.step();clock.advance(50000)
            if pair.sender.flow.complete and pair.receiver.flow.complete:break
        assert pair.sender.flow.complete and pair.receiver.flow.complete
        assert receiver.received()==reply and len(reply)<32768
        assert bundle_wire.decode_chunk(reply[resource.RESPONSE.size:],entry)==raw
        assert not pair.sender.journal.snapshot()['fenced']


@pytest.mark.parametrize('fault',['missing_representation','missing_descriptor','wrong_representation',
    'wrong_pin_type','uppercase_pin','missing_maximum','private'])
def test_invalid_or_partial_capability_never_opens_a_flow(tmp_path,compact,fault):
    reply,digest,_,_=compact;clock=Clock();value=service(clock,digest)
    if fault=='missing_representation':value.pop(REPRESENTATION_MARKER)
    elif fault=='missing_descriptor':value.pop(TRANSPORT_DESCRIPTOR_MARKER)
    elif fault=='wrong_representation':value[REPRESENTATION_MARKER]='JSON'
    elif fault=='wrong_pin_type':value[TRANSPORT_DESCRIPTOR_MARKER]=True
    elif fault=='uppercase_pin':value[TRANSPORT_DESCRIPTOR_MARKER]='F'*64
    elif fault=='missing_maximum':value.pop('public_response_max_bytes')
    else:value.update(kind='protected_message',format='LME.binary')
    with pytest.raises(WireError):
        with carrier_pair(tmp_path/'invalid',reply,clock,'unit-c','unit-a',102,service=value):pass
    assert not (tmp_path/'invalid/unit-c/started').exists()


def test_legacy_public32_without_MB1_capability_still_rejects_variable_length(tmp_path,compact):
    reply,_,_,_=compact;clock=Clock()
    assert public_mb1_service(dict(scope('MR1.response',clock),public_response_max_bytes=32768)) is None
    with pytest.raises(WireError,match='invalid_bench_object_descriptor'):
        with carrier_pair(tmp_path/'legacy',reply,clock,'unit-c','unit-a',103,
            service=dict(scope('MR1.response',clock),public_response_max_bytes=32768)):pass


@pytest.mark.parametrize('fault',['length','hash','expired','revoked','mutable_descriptor'])
def test_fresh_object_original_deadline_and_revocation_checks_precede_RF(tmp_path,compact,fault):
    reply,digest,_,_=compact;clock=Clock()
    with carrier_pair(tmp_path/'refusal',reply,clock,'unit-c','unit-a',104,service=service(clock,digest)) as pair:
        transfer=ServiceTransfer(pair.sender);deadlines=pair.sender.authority.deadlines;changed=reply
        if fault=='length':changed=reply[:-1]
        elif fault=='hash':changed=reply[:-1]+bytes([reply[-1]^1])
        elif fault=='expired':clock.advance(61000000)
        elif fault=='revoked':revoke(pair.sender.authority.path)
        else:transfer.mb1_descriptor_sha256='f'*64
        with pytest.raises(WireError):transfer.send(changed)
        assert not pair.workers[0].sent and pair.sender.journal.snapshot()['attempts']==0
        assert pair.sender.authority.deadlines is deadlines


def test_descriptor_difference_changes_authenticated_route_and_refuses_forged_frame(tmp_path,compact):
    reply,digest,_,_=compact;clock=Clock()
    with carrier_pair(tmp_path/'original',reply,clock,'unit-c','unit-a',105,service=service(clock,digest)) as original:
        with carrier_pair(tmp_path/'different',reply,clock,'unit-c','unit-a',105,service=service(clock,'f'*64)) as different:
            assert original.sender.authority.grant.digest!=different.sender.authority.grant.digest
            assert original.sender.authority.grant.route_binding_digest!=different.sender.authority.grant.route_binding_digest
            packet=original.sender.cipher.seal(b'data',FrameKind.DATA,authorize=original.sender.authority.live)
            with pytest.raises(WireError,match='authentication_failed'):
                different.receiver.cipher.open(packet,authorize=different.receiver.authority.live)
