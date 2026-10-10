"""Publisher verification after authenticated quarantine; source facts only."""
import hashlib
import json
import base64
import shutil

import pytest
from meshticulum.bench.pilot_context import original_context
from meshticulum.bench.pilot_resources import serve_chunk, verify_chunk
from meshticulum.bench.pilot_provision import provision
from meshticulum.bench.wire import WireError
from meshticulum.ip.node import _write, clock
from meshticulum.resources import lamplighter as ll
from meshticulum.resources import resource
from tests.test_ip_node import facts
from tests.test_ip_node import prepare
from tests.test_meshticulum_resources import pack


def capture(root, run_id, payload, format, expires, deadline):
    directory = root / 'runs' / run_id
    directory.mkdir(parents=True, mode=0o700)
    directory.parent.chmod(0o700)
    result = dict(schema='meshticulum.ip-public-node-run.v1', unit='unit-b', host_boot_id=facts('unit-b')['host_boot_id'],
        complete=True, sends=False, service_format=format, service_object_durable=True,
        service_object_sha256=hashlib.sha256(payload).hexdigest(),
        original_application_expires_epoch_us=expires, original_application_deadline_us=deadline)
    _write(directory / 'result.json', json.dumps(result).encode())
    _write(directory / 'receiver.bin', payload)
    _write(directory / 'active', b'active\n')
    return directory


def setup(tmp_path, pack):
    seed, trust, digest, manifest = pack
    root = tmp_path / 'state'; root.mkdir(mode=0o700)
    context = root / 'campaigns' / ('c' * 32); context.mkdir(parents=True, mode=0o700)
    context.parent.chmod(0o700)
    epoch, mono = clock(); expires = epoch + 20000000
    original = original_context(context, unit='unit-b', host_boot_id=facts('unit-b')['host_boot_id'],
        resource_sha256=digest, expires_epoch_us=expires, clock=clock)
    request = resource.encode_request('/packs/' + digest + '/chunks/' + manifest['chunks'][0]['sha256'] + '.json', 0, 16356)
    capture(root, '1' * 32, request, 'MR1.request', expires, original['application_deadline_monotonic_us'])
    destination = tmp_path / 'recipient'; destination.mkdir(mode=0o700)
    for name in ('manifest.json', 'manifest.sig.json'):
        _write(destination / name, (seed / name).read_bytes())
    return root, destination, request, original


def test_native_public_store_serves_then_recipient_independently_verifies_and_reuses(pack, tmp_path):
    root, destination, request, original = setup(tmp_path, pack)
    response = serve_chunk(root, '1' * 32, facts=facts('unit-b'), clock=clock,
        seed=pack[0], trust=pack[1], manifest_sha256=pack[2])
    capture(root, '2' * 32, response, 'MR1.response', original['application_expires_epoch_us'], original['application_deadline_monotonic_us'])
    args = dict(facts=facts('unit-b'), clock=clock, request=request, campaign_id='c' * 32,
                manifest_sha256=pack[2], destination=destination, trust=pack[1])
    result = verify_chunk(root, '2' * 32, **args)
    assert result['publisher_verified'] and not result['reused'] and not result['sage_imported']
    assert verify_chunk(root, '2' * 32, **args)['reused']
    cache = ll.VerifiedCache(destination, pack[1], pack[2])
    assert cache.piece(result['chunk_sha256']) == ll.VerifiedCache(pack[0], pack[1], pack[2]).piece(result['chunk_sha256'])


def test_response_grant_uses_native_verified_store_and_actual_received_request(pack, tmp_path):
    root, destination, request, original = setup(tmp_path, pack)
    response = serve_chunk(root, '1' * 32, facts=facts('unit-b'), clock=clock,
        seed=pack[0], trust=pack[1], manifest_sha256=pack[2])
    public = root / 'public' / pack[2]
    public.mkdir(parents=True, mode=0o700)
    public.parent.chmod(0o700)
    shutil.copytree(pack[0], public / 'pack')
    _write(public / 'trust.json', json.dumps(pack[1]).encode())
    fixture = tmp_path / 'reply-fixture'; fixture.mkdir()
    directories, grant = prepare(fixture, response, 'MR1.response')
    plan = json.loads((directories[0] / 'grant.json').read_text())
    plan['application_deadline_monotonic_us'] = None
    plan['application_expires_epoch_us'] = original['application_expires_epoch_us']
    plan['local_peer'] = plan['session']['initiator']
    package = dict(schema='meshticulum.public-pilot-package.v1', unit='unit-b', campaign_id='c' * 32,
        resource_sha256=pack[2], application_expires_epoch_us=original['application_expires_epoch_us'],
        transport='tcp', grant=plan, secret_base64=base64.b64encode(grant.secret).decode(),
        payload_base64=None, source_run_id={'kind':'served_public_chunk','request_run_id':'1' * 32})
    result = provision(package, state=root, facts=facts('unit-b'), clock=clock)
    assert result['source_basis'] == 'native_verified_public_store_response'
    assert (root / 'runs' / result['run_id'] / 'sender.bin').read_bytes() == response
    assert not (root / 'runs' / result['run_id'] / 'started').exists()


@pytest.mark.parametrize('fault', ['binding', 'payload', 'revoked_capture', 'revoked_publisher'])
def test_authenticated_shape_does_not_bypass_binding_revocation_or_publisher_checks(pack, tmp_path, fault):
    root, destination, request, original = setup(tmp_path, pack)
    response = serve_chunk(root, '1' * 32, facts=facts('unit-b'), clock=clock,
        seed=pack[0], trust=pack[1], manifest_sha256=pack[2])
    if fault == 'binding':
        header = resource.RESPONSE.unpack(response[:resource.RESPONSE.size])
        response = resource.RESPONSE.pack(header[0], b'z' * 16, *header[2:]) + response[resource.RESPONSE.size:]
    if fault == 'payload': response = response[:-1] + bytes([response[-1] ^ 1])
    directory = capture(root, '2' * 32, response, 'MR1.response', original['application_expires_epoch_us'], original['application_deadline_monotonic_us'])
    if fault == 'revoked_capture': (directory / 'active').write_bytes(b'retired\n')
    if fault == 'revoked_publisher': pack[1]['keys'][0]['status'] = 'revoked'
    with pytest.raises((ValueError, WireError)):
        verify_chunk(root, '2' * 32, facts=facts('unit-b'), clock=clock, request=request,
            campaign_id='c' * 32, manifest_sha256=pack[2], destination=destination, trust=pack[1])
    assert not list((destination / 'chunks').glob('*.json')) if (destination / 'chunks').exists() else True
