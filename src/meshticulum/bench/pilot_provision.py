"""Native preparation for finite public LoRa/TCP children, never activation.

The caller supplies the independently checked host facts and clocks. Linux-root
entry points acquire those themselves. Relay bytes come from this host's prior
authenticated public quarantine, never an operator-provided replacement body.
"""
from contextlib import ExitStack
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from ..contracts.identity import PeerID
from ..ip.authority import grant_from_plan
from ..ip.journal import private
from ..ip.node import _read, _write
from .pilot_context import original_context
from .services import _shape
from .wire import require


def identifier(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{32}', value) and int(value, 16) != 0,
            'invalid_pilot_identifier')
    return value


def _directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    private(path.lstat(), True)
    return path


def provision(package, *, state, facts, clock):
    with ExitStack() as leases:
        return _provision(package, state=state, facts=facts, clock=clock, leases=leases)


def _provision(package, *, state, facts, clock, leases):
    fields = {'schema', 'unit', 'campaign_id', 'resource_sha256', 'application_expires_epoch_us',
              'transport', 'grant', 'secret_base64', 'payload_base64', 'source_run_id'}
    require(type(package) is dict and set(package) == fields
            and package['schema'] == 'meshticulum.public-pilot-package.v1', 'invalid_pilot_package')
    require(package['unit'] == facts['unit'] and package['transport'] in ('lora', 'tcp'), 'wrong_pilot_unit_or_transport')
    identifier(package['campaign_id'])
    state = Path(state).absolute()
    private(state.lstat(), True)
    contexts = _directory(state / 'campaigns')
    directory = _directory(contexts / package['campaign_id'])
    original = original_context(directory, unit=facts['unit'], host_boot_id=facts['host_boot_id'],
        resource_sha256=package['resource_sha256'], expires_epoch_us=package['application_expires_epoch_us'], clock=clock)
    plan = package['grant']
    require(type(plan) is dict and plan['host_boot_id'] == facts['host_boot_id'], 'pilot_wrong_boot')
    plan = dict(plan)
    require(plan.get('application_deadline_monotonic_us') is None, 'operator_cannot_replace_native_deadline')
    plan['application_deadline_monotonic_us'] = original['application_deadline_monotonic_us']
    secret = base64.b64decode(package['secret_base64'], validate=True)
    require(len(secret) == 32, 'invalid_pilot_secret')
    if package['transport'] == 'tcp':
        local_peer = PeerID(**plan['local_peer'])
        grant = grant_from_plan(plan, secret, local_peer=local_peer, host_boot_id=facts['host_boot_id'])
        format = grant.format
        sends = grant.direction(local_peer) == 0
        run_id = identifier(grant.session.session_id.hex())
        descriptor = dict(bytes=grant.object_bytes, sha256=grant.object_sha256.hex())
        expires = grant.application_expires_epoch_us
    else:
        require(plan.get('schema') == 'meshticulum.service-run.v1' and plan.get('unit') == facts['unit']
                and plan.get('country') == 'MY' and plan.get('antenna_confirmed') is True, 'invalid_public_radio_pilot')
        service = plan['service']
        require(type(service) is dict and set(service) == {'kind', 'format', 'application_expires_epoch_us'}
                and service['kind'] == 'knowledge', 'invalid_public_radio_service')
        format = service['format']
        sends = plan['sender_unit'] == facts['unit']
        run_id = identifier(plan['session_id'])
        descriptor = plan['object']
        expires = service['application_expires_epoch_us']
    require(format in ('MR1.request', 'MR1.response') and expires == original['application_expires_epoch_us'],
            'pilot_requires_original_public_format_expiry')
    epoch, mono = clock()
    require(type(plan['issued_epoch_us']) is int and type(plan['expires_epoch_us']) is int
            and plan['issued_epoch_us'] <= epoch < expires <= plan['expires_epoch_us']
            and plan['expires_epoch_us'] - plan['issued_epoch_us'] <= 240000000
            and mono < plan['application_deadline_monotonic_us'], 'pilot_grant_not_current')
    require(type(descriptor) is dict and type(descriptor['bytes']) is int
            and 1 <= descriptor['bytes'] <= (16384 if package['transport'] == 'lora' else 65536)
            and re.fullmatch('[0-9a-f]{64}', descriptor['sha256']), 'invalid_pilot_object')
    source_run = package['source_run_id']
    payload = None
    source_basis = 'none_receiver'
    if not sends:
        require(package['payload_base64'] is None and source_run is None, 'receiver_must_not_have_management_payload')
    elif source_run is not None:
        require(package['payload_base64'] is None, 'relay_cannot_use_operator_payload')
        from .pilot_resources import captured, serve_chunk
        if type(source_run) is dict:
            require(set(source_run) == {'kind', 'request_run_id'}
                    and source_run['kind'] == 'served_public_chunk' and format == 'MR1.response',
                    'invalid_pilot_native_response_source')
            identifier(source_run['request_run_id'])
            _, receipt = leases.enter_context(captured(state, source_run['request_run_id'],
                facts=facts, format='MR1.request', clock=clock))
            public = state / 'public' / package['resource_sha256']
            private(public.lstat(), True)
            trust = json.loads(_read(public / 'trust.json', 16384))
            payload = serve_chunk(state, source_run['request_run_id'], facts=facts, clock=clock,
                seed=public / 'pack', trust=trust, manifest_sha256=package['resource_sha256'])
            source_basis = 'native_verified_public_store_response'
        else:
            identifier(source_run)
            require(source_run != run_id, 'relay_cannot_use_operator_payload')
            payload, receipt = leases.enter_context(captured(state, source_run, facts=facts, format=format, clock=clock))
            require(receipt.get('service_object_sha256') == descriptor['sha256'],
                    'relay_requires_local_authenticated_public_capture')
            source_basis = 'prior_local_authenticated_public_quarantine'
        require(receipt.get('original_application_expires_epoch_us') == expires,
                'relay_requires_local_authenticated_public_capture')
        prior_deadline = receipt['original_application_deadline_us']
        plan['application_deadline_monotonic_us'] = min(plan['application_deadline_monotonic_us'], prior_deadline)
    else:
        require(format == 'MR1.request', 'pilot_response_requires_native_received_request')
        require(type(package['payload_base64']) is str, 'pilot_sender_payload_missing')
        payload = base64.b64decode(package['payload_base64'], validate=True)
        source_basis = 'operator_public_fixture'
    if payload is not None:
        require(len(payload) == descriptor['bytes'] and hashlib.sha256(payload).hexdigest() == descriptor['sha256'],
                'pilot_sender_object_mismatch')
        _shape(payload, format)
    runs = _directory(state / 'runs')
    target = runs / run_id
    require(not target.exists() and not target.is_symlink(), 'pilot_run_already_provisioned')
    staging = Path(tempfile.mkdtemp(prefix='.pilot-', dir=runs))
    files = {'grant.json': json.dumps(plan, sort_keys=True, separators=(',', ':')).encode() + b'\n',
             'secret.bin': secret, 'active': b'active\n'}
    if payload is not None:
        files['sender.bin'] = payload
    receipt = dict(schema='meshticulum.provisioned-public-pilot.v1', unit=facts['unit'],
        run_id=run_id, campaign_id=package['campaign_id'], transport=package['transport'],
        object=descriptor, source_basis=source_basis, source_run_id=source_run,
        original_application_deadline_us=plan['application_deadline_monotonic_us'],
        original_application_expires_epoch_us=expires, radio_opened=False, socket_opened=False,
        receiver_payload_installed=False)
    files['provision.json'] = json.dumps(receipt, sort_keys=True).encode() + b'\n'
    for name, raw in files.items():
        _write(staging / name, raw)
    os.rename(staging, target)
    fd = os.open(runs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return receipt
