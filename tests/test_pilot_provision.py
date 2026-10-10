"""Native provisioner controls with synthetic facts; no device or activation."""
import base64
import hashlib
import json
from pathlib import Path

import pytest
from meshticulum.bench.pilot_provision import provision
from meshticulum.bench.services import resource
from meshticulum.bench.wire import WireError
from meshticulum.ip.node import _write
from tests.test_ip_node import prepare, facts


def package(tmp_path, payload, *, receiver=False, source_run=None):
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    directories, grant = prepare(fixture, payload)
    index = 1 if receiver else 0
    plan = json.loads((directories[index] / 'grant.json').read_text())
    plan['application_deadline_monotonic_us'] = None
    return dict(schema='meshticulum.public-pilot-package.v1', unit='unit-b' if receiver else 'unit-a',
        campaign_id='c' * 32, resource_sha256='a' * 64,
        application_expires_epoch_us=grant.application_expires_epoch_us, transport='tcp', grant=plan,
        secret_base64=base64.b64encode(grant.secret).decode(),
        payload_base64=None if receiver or source_run else base64.b64encode(payload).decode(), source_run_id=source_run)


def state(tmp_path):
    result = tmp_path / 'state'
    result.mkdir(mode=0o700)
    return result


def request():
    return resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)


def test_provision_sender_sets_native_original_deadline_without_claim_or_socket(tmp_path):
    value = package(tmp_path, request())
    root = state(tmp_path)
    from meshticulum.ip.node import clock
    result = provision(value, state=root, facts=facts('unit-a'), clock=clock)
    directory = root / 'runs' / result['run_id']
    context = json.loads((root / 'campaigns' / value['campaign_id'] / 'context.json').read_text())
    plan = json.loads((directory / 'grant.json').read_text())
    assert plan['application_deadline_monotonic_us'] == context['application_deadline_monotonic_us']
    assert (directory / 'sender.bin').read_bytes() == request()
    assert not (directory / 'started').exists() and not (directory / 'bytes.sqlite').exists()
    assert not result['socket_opened'] and not result['radio_opened']
    with pytest.raises(WireError, match='pilot_run_already_provisioned'):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)


def test_receiver_never_receives_management_payload(tmp_path):
    value = package(tmp_path, request(), receiver=True)
    root = state(tmp_path)
    from meshticulum.ip.node import clock
    value['payload_base64'] = base64.b64encode(request()).decode()
    with pytest.raises(WireError, match='receiver_must_not_have_management_payload'):
        provision(value, state=root, facts=facts('unit-b'), clock=clock)
    assert not (root / 'runs').exists()
    value['payload_base64'] = None
    result = provision(value, state=root, facts=facts('unit-b'), clock=clock)
    assert not (root / 'runs' / result['run_id'] / 'sender.bin').exists()


def test_operator_cannot_supply_response_without_native_received_request(tmp_path):
    response = resource.BlockServer(lambda _: b'public fixture').handle(request())
    value = package(tmp_path, response)
    value['grant']['format'] = 'MR1.response'
    root = state(tmp_path)
    from meshticulum.ip.node import clock
    with pytest.raises(WireError, match='pilot_response_requires_native_received_request'):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert not (root / 'runs').exists()


def prior(root, value, payload, clock, **changes):
    run = root / 'runs' / value['source_run_id']
    run.mkdir(parents=True, mode=0o700)
    run.parent.chmod(0o700)
    result = dict(schema='meshticulum.physical-service-run.v1', unit=value['unit'],
        host_boot_id=value['grant']['host_boot_id'], complete=True, sends=False,
        service_format=value['grant']['format'], service_object_durable=True,
        service_object_sha256=hashlib.sha256(payload).hexdigest(),
        original_application_expires_epoch_us=value['application_expires_epoch_us'],
        original_application_deadline_us=clock()[1] + 10000000)
    result.update(changes)
    _write(run / 'result.json', json.dumps(result).encode())
    _write(run / 'receiver.bin', payload)
    _write(run / 'active', b'active\n')
    return result


def test_relay_uses_exact_local_capture_and_tightens_to_its_original_deadline(tmp_path):
    payload = request(); value = package(tmp_path, payload, source_run='d' * 32)
    root = state(tmp_path)
    from meshticulum.ip.node import clock
    original = prior(root, value, payload, clock)
    result = provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert result['source_basis'] == 'prior_local_authenticated_public_quarantine'
    assert result['original_application_deadline_us'] == original['original_application_deadline_us']
    assert (root / 'runs' / result['run_id'] / 'sender.bin').read_bytes() == payload


@pytest.mark.parametrize('change', [{'complete': False}, {'sends': True}, {'service_object_durable': False},
    {'service_format': 'LME.binary'}, {'service_object_sha256': 'b' * 64},
    {'host_boot_id': '31f9c178-6090-40d9-9359-ecf1c7b950b1'}, {'original_application_expires_epoch_us': 1}])
def test_relay_refuses_incomplete_private_or_rebound_source(tmp_path, change):
    payload = request(); value = package(tmp_path, payload, source_run='d' * 32)
    root = state(tmp_path)
    from meshticulum.ip.node import clock
    prior(root, value, payload, clock, **change)
    with pytest.raises(WireError, match='(relay_requires_local_authenticated_public_capture|pilot_resource_requires_local_authenticated_capture|pilot_resource_original_deadline_expired|pilot_capture_changed)'):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert not (root / 'runs' / value['grant']['session']['session_id']).exists()


def test_relay_refuses_operator_substitution_even_when_local_capture_is_valid(tmp_path):
    payload = request(); value = package(tmp_path, payload, source_run='d' * 32)
    root = state(tmp_path)
    from meshticulum.ip.node import clock
    prior(root, value, payload, clock)
    value['payload_base64'] = base64.b64encode(payload).decode()
    with pytest.raises(WireError, match='relay_cannot_use_operator_payload'):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)


@pytest.mark.parametrize('change', ['private', 'wrong_boot', 'operator_deadline'])
def test_bad_scope_is_rejected_without_installing_a_run(tmp_path, change):
    value = package(tmp_path, request()); root = state(tmp_path)
    from meshticulum.ip.node import clock
    if change == 'private': value['grant']['format'] = 'LME.binary'
    if change == 'wrong_boot': value['grant']['host_boot_id'] = '31f9c178-6090-40d9-9359-ecf1c7b950b1'
    if change == 'operator_deadline': value['grant']['application_deadline_monotonic_us'] = clock()[1] + 100000000
    with pytest.raises(WireError):
        provision(value, state=root, facts=facts('unit-a'), clock=clock)
    assert not (root / 'runs').exists()
