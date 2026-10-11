"""Real optional backend controls; never native/PFS qualification."""
from dataclasses import replace
import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from meshticulum.privacy import pq_prototype as pq

try:
    import cryptography
    _available = cryptography.__version__ == pq.BACKEND_VERSION
except ImportError:
    _available = False
if not _available:
    if os.environ.get('MESHTICULUM_REQUIRE_PQ') == '1':
        raise RuntimeError('mandatory_PQ_candidate_backend_unavailable')
pytestmark = pytest.mark.skipif(not _available,
    reason='optional PQ prototype requires cryptography50.0.2')


@pytest.fixture
def exchange():
    alice, bob = pq.DualIdentity(), pq.DualIdentity()
    context = pq.Context(alice.public.identifier, bob.public.identifier, b'MR1.request',
        hashlib.sha256(b'public signed manifest').digest(), 100, 100_000_100, b'n'*32)
    offer = pq.OfferState(alice, context, 100)
    replay = pq.ReplayWindow(bob.public.identifier)
    reply = pq.ReplyState(bob, offer.packet, context, alice.public, 101, replay)
    return alice, bob, context, offer, reply, replay


def _verify_vectors(vectors):
    for vector in vectors:
        key = pq.XWingPrivateKey(bytes.fromhex(vector['seed']))
        assert key.public_bytes.hex() == vector['pk']
        assert key.decapsulate(bytes.fromhex(vector['ct'])).hex() == vector['ss']


def test_licensed_primary_vector_is_also_exact_draft11_vector_one():
    value = json.loads((Path(__file__).parent/'vectors/xwing-draft11.json').read_bytes())
    assert value['source_sha256'] == '2ac1085a84d96bf8112855e1ecc82057a71d7c9c404091b65561605a26ada1a1'
    assert value['license'] == 'Apache-2.0' and len(value['vectors']) == 1
    _verify_vectors(value['vectors'])


def _draft_vectors(raw):
    assert hashlib.sha256(raw).hexdigest() == '1353f61f0d706199d86b6802eab83cabbb5195b2d99fb35bca20f5e4f83db786'
    text=raw.decode().split('Appendix C.  Test vectors',2)[-1].split('Appendix D.',1)[0]
    rows=[];row=None;key=None
    for line in text.splitlines():
        match=re.fullmatch(r'(seed|sk|pk|eseed|ct|ss)(?:\s+([a-f0-9]+))?',line.strip())
        if match:
            key=match[1]
            if key=='seed':row={};rows.append(row)
            if row is not None:row[key]=match[2] or ''
        elif row is not None and key and re.fullmatch(r'[a-f0-9]+',line.strip()):row[key]+=line.strip()
    assert len(rows)==3
    return rows


def test_exact_three_published_vectors_from_verified_unmodified_draft():
    path=os.environ.get('MESHTICULUM_XWING_DRAFT_PATH')
    if not path:
        if os.environ.get('MESHTICULUM_REQUIRE_PQ')=='1':
            pytest.fail('mandatory verified unmodified XWing draft input missing')
        pytest.skip('set MESHTICULUM_XWING_DRAFT_PATH for full-draft conformance')
    with open(path,'rb') as stream:raw=stream.read(100001)
    assert len(raw)<=100000
    rows=_draft_vectors(raw)
    licensed=json.loads((Path(__file__).parent/'vectors/xwing-draft11.json').read_bytes())['vectors'][0]
    assert all(rows[1][key]==value for key,value in licensed.items())
    _verify_vectors(rows)


def test_real_key_generation_and_both_independent_secrets():
    key = pq.XWingPrivateKey.generate()
    secret, ciphertext = pq.encapsulate(key.public_bytes)
    assert len(key.public_bytes) == 1216 and len(ciphertext) == 1120
    assert key.decapsulate(ciphertext) == secret
    parts = [b'1'*32, b'2'*32, b'3'*32, b'4'*32]
    original = pq._combine(*parts)
    for index in (0, 1):
        altered = list(parts); altered[index] = b'x'*32
        assert pq._combine(*altered) != original


@pytest.mark.parametrize('material',['short_public','overlong_public','bad_mlkem','zero_x25519','short_ct','zero_ct_x'])
def test_backend_and_size_validation_refuse_invalid_keys_and_ciphertexts(material):
    key = pq.XWingPrivateKey.generate()
    with pytest.raises(pq.PQError):
        if material == 'short_public': pq.encapsulate(key.public_bytes[:-1])
        elif material == 'overlong_public': pq.encapsulate(key.public_bytes+b'0')
        elif material == 'bad_mlkem': pq.encapsulate(b'\xff'*1184+key.public_bytes[1184:])
        elif material == 'zero_x25519': pq.encapsulate(key.public_bytes[:1184]+b'\0'*32)
        elif material == 'short_ct': key.decapsulate(b'0'*1119)
        else: key.decapsulate(b'0'*1088+b'\0'*32)


def test_real_mutual_confirmation_and_direction_keys(exchange):
    alice, bob, context, offer, reply, replay = exchange
    sender, confirmation = offer.finish(reply.packet, bob.public, 102)
    recipient = reply.finish(confirmation, 103)
    assert sender.outgoing == recipient.incoming and sender.incoming == recipient.outgoing
    assert sender.outgoing != sender.incoming
    assert sender.transcript_sha256 == recipient.transcript_sha256
    assert sender.context == recipient.context == context
    assert [len(offer.packet), len(reply.packet), len(confirmation)] == [4751, 4547, 54]
    assert sum((len(p)+192)//193 for p in (offer.packet, reply.packet, confirmation)) == 50
    assert sum(map(len, (offer.packet, reply.packet, confirmation))) == 9352


@pytest.mark.parametrize('part',['ed25519','mldsa65','missing_ml','wrong_purpose','wrong_message','wrong_peer'])
def test_dual_manifest_authentication_requires_both_pinned_signatures(part):
    identity = pq.DualIdentity(); message = b'public canonical whole-bundle manifest'
    signature = identity.sign(b'manifest', message); public = identity.public
    public.verify(b'manifest', message, signature)
    if part == 'ed25519': signature = bytes((signature[0]^1,))+signature[1:]
    elif part == 'mldsa65': signature = signature[:64]+bytes((signature[64]^1,))+signature[65:]
    elif part == 'missing_ml': signature = signature[:64]
    elif part == 'wrong_peer': public = pq.DualIdentity().public
    with pytest.raises(pq.PQError):
        public.verify(b'other' if part == 'wrong_purpose' else b'manifest',
            message+b'x' if part == 'wrong_message' else message, signature)


@pytest.mark.parametrize('component',['ed25519','mldsa65'])
def test_each_independent_signature_binds_the_same_canonical_purpose(component):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519,mldsa
    identity=pq.DualIdentity();message=b'public canonical manifest'
    signature=identity.sign(b'manifest',message)
    context,bound=pq._signed(b'manifest',message)
    wrong_context,wrong_bound=pq._signed(b'offer',message)
    assert bound!=wrong_bound
    if component=='ed25519':
        key=ed25519.Ed25519PublicKey.from_public_bytes(identity.public.public_bytes[:32])
        key.verify(signature[:64],bound)
        with pytest.raises(InvalidSignature):key.verify(signature[:64],wrong_bound)
    else:
        key=mldsa.MLDSA65PublicKey.from_public_bytes(identity.public.public_bytes[32:])
        key.verify(signature[64:],bound,context=context)
        with pytest.raises(InvalidSignature):key.verify(signature[64:],wrong_bound,context=wrong_context)


@pytest.mark.parametrize('field',['source','destination','service','object_sha256','issued_epoch_us','expires_epoch_us','nonce'])
def test_offer_is_bound_to_expected_peer_service_object_nonce_and_original_expiry(exchange,field):
    alice, bob, context, offer, reply, replay = exchange
    value = {'source':b's'*32, 'destination':b'd'*32, 'service':b'MR1.response',
        'object_sha256':b'h'*32, 'issued_epoch_us':101, 'expires_epoch_us':99_000_100,
        'nonce':b'z'*32}[field]
    wrong = replace(context, **{field:value})
    with pytest.raises(pq.PQError):
        pq.ReplyState(bob, offer.packet, wrong, alice.public, 102, pq.ReplayWindow(bob.public.identifier))


@pytest.mark.parametrize('packet_kind',['offer','reply'])
@pytest.mark.parametrize('mutation',['draft','kind','trailing','signature'])
def test_wire_tampering_and_algorithm_downgrade_have_no_classical_fallback(exchange,packet_kind,mutation):
    alice, bob, context, offer, reply, replay = exchange
    packet = offer.packet if packet_kind == 'offer' else reply.packet
    if mutation == 'draft': packet = packet[:4]+b'\x0a'+packet[5:]
    elif mutation == 'kind': packet = packet[:5]+b'\x03'+packet[6:]
    elif mutation == 'trailing': packet += b'x'
    else: packet = packet[:-1]+bytes((packet[-1]^1,))
    with pytest.raises(pq.PQError):
        if packet_kind == 'offer':
            pq.ReplyState(bob, packet, context, alice.public, 102, pq.ReplayWindow(bob.public.identifier))
        else: offer.finish(packet, bob.public, 102)


def test_full_length_mlkem_ciphertext_implicit_rejection_fails_real_aead_confirmation(exchange):
    alice, bob, context, offer, reply, replay = exchange
    body = bytearray(reply.packet[:-pq.SIGNATURE_BYTES]); body[38] ^= 1
    body = bytes(body)
    # Authenticate the corrupted full-length CT with the actual peer key, so
    # refusal tests the backend's implicit-rejection secret + AEAD confirmation.
    packet = body+bob.sign(b'reply',body)
    with pytest.raises(pq.PQError, match='hybrid_secret_not_confirmed'):
        offer.finish(packet,bob.public,102)


@pytest.mark.parametrize('step',['offer','reply','initiator_finish','responder_finish'])
def test_original_expiry_refuses_at_every_exchange_step(exchange,step):
    alice, bob, context, offer, reply, replay = exchange
    end = context.expires_epoch_us
    with pytest.raises(pq.PQError):
        if step == 'offer': pq.OfferState(alice,context,end)
        elif step == 'reply': pq.ReplyState(bob,offer.packet,context,alice.public,end,pq.ReplayWindow(bob.public.identifier))
        elif step == 'initiator_finish': offer.finish(reply.packet,bob.public,end)
        else:
            _,confirmation = offer.finish(reply.packet,bob.public,102)
            reply.finish(confirmation,end)


def test_replays_and_role_swaps_refuse(exchange):
    alice, bob, context, offer, reply, replay = exchange
    with pytest.raises(pq.PQError): pq.ReplyState(bob,offer.packet,context,alice.public,102,replay)
    sender,confirmation = offer.finish(reply.packet,bob.public,102)
    reply.finish(confirmation,103)
    with pytest.raises(pq.PQError): offer.finish(reply.packet,bob.public,104)
    with pytest.raises(pq.PQError): reply.finish(confirmation,104)
    with pytest.raises(pq.PQError): pq.OfferState(bob,context,104)


def test_confirmation_tampering_refuses(exchange):
    alice,bob,context,offer,reply,replay = exchange
    _,confirmation = offer.finish(reply.packet,bob.public,102)
    bad = confirmation[:-1]+bytes((confirmation[-1]^1,))
    with pytest.raises(pq.PQError): reply.finish(bad,103)


def test_optional_provider_requires_exact_tested_backend_without_fallback(monkeypatch):
    import cryptography
    monkeypatch.setattr(cryptography,'__version__','46.0.3')
    with pytest.raises(pq.PQUnavailable): pq.XWingPrivateKey.generate()


def test_replay_window_saturates_without_deleting_observed_nonce_entries(exchange):
    alice,bob,context,offer,reply,replay=exchange
    window=pq.ReplayWindow(context.destination)
    for i in range(256):window.consume(replace(context,nonce=i.to_bytes(32,'big')))
    with pytest.raises(pq.PQError):window.consume(replace(context,nonce=(256).to_bytes(32,'big')))
    with pytest.raises(pq.PQError):window.consume(replace(context,nonce=b'\0'*32))
    assert len(window._seen)==256


def _pending_operation(exchange,which):
    alice,bob,context,offer,reply,replay=exchange
    if which=='replay':return lambda:replay.consume(replace(context,nonce=b'z'*32))
    if which=='offer':return lambda:offer.finish(reply.packet,bob.public,102)
    _,confirmation=offer.finish(reply.packet,bob.public,102)
    return lambda:reply.finish(confirmation,103)


@pytest.mark.parametrize('which',['replay','offer','reply'])
def test_two_real_foreign_threads_cannot_race_one_use_state(exchange,which):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    operation=_pending_operation(exchange,which);barrier=threading.Barrier(2)
    def foreign():
        barrier.wait(timeout=5)
        try:operation()
        except pq.PQError:return False
        return True
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=pool.submit(foreign),pool.submit(foreign)
        assert a.result(timeout=5) is b.result(timeout=5) is False
    operation()  # Foreign attempts did not consume the original owner's use.
    with pytest.raises(pq.PQError):operation()


@pytest.mark.skipif(not hasattr(os,'fork'),reason='requires real forked-process ownership control')
@pytest.mark.parametrize('which',['replay','offer','reply'])
def test_forked_process_cannot_consume_parent_exchange_or_nonce_state(exchange,which):
    operation=_pending_operation(exchange,which);read_fd,write_fd=os.pipe();pid=os.fork()
    if pid==0:
        os.close(read_fd)
        try:operation()
        except pq.PQError:os.write(write_fd,b'R');os._exit(0)
        except BaseException:os._exit(1)
        else:os.write(write_fd,b'A');os._exit(0)
    os.close(write_fd)
    try:
        assert os.read(read_fd,1)==b'R'
        _,status=os.waitpid(pid,0);assert os.waitstatus_to_exitcode(status)==0
    finally:os.close(read_fd)
    operation()


def test_modified_draft_cannot_supply_accepted_test_vectors():
    with pytest.raises(AssertionError):_draft_vectors(b'rewritten or incomplete draft')
