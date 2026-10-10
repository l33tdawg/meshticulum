"""Explicit native public-piece verification; opens no socket or radio."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

from ..ip.node import _facts, _read, _write, clock
from ..ip.journal import private
from .pilot_context import original_context
from .pilot_provision import identifier
from .pilot_resources import verify_chunk
from .wire import require

STATE = Path('/var/lib/meshticulum-bench')


def verify(unit, campaign_id, request_run_id, response_run_id):
    facts = _facts(unit)
    root = STATE
    campaign = root / 'campaigns' / identifier(campaign_id)
    private(campaign.lstat(), True)
    saved = json.loads(_read(campaign / 'context.json', 2048))
    context = original_context(campaign, unit=unit, host_boot_id=facts['host_boot_id'],
        resource_sha256=saved['resource_sha256'], expires_epoch_us=saved['application_expires_epoch_us'], clock=clock)
    digest = context['resource_sha256']
    public = root / 'public' / digest
    private(public.lstat(), True)
    trust = json.loads(_read(public / 'trust.json', 16384))
    request_directory = root / 'runs' / identifier(request_run_id)
    private(request_directory.lstat(), True)
    fd = os.open(request_directory / 'active', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        private(os.fstat(fd))
        identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        def current():
            info = (request_directory / 'active').lstat()
            private(info)
            require((info.st_dev, info.st_ino) == identity and os.pread(fd, 16, 0) == b'active\n',
                    'native_request_authority_retired_or_changed')
        current()
        receipt = json.loads(_read(request_directory / 'result.json', 100000))
        plan = json.loads(_read(request_directory / 'grant.json', 16384))
        require(receipt['schema'] in ('meshticulum.physical-service-run.v1', 'meshticulum.ip-public-node-run.v1')
                and receipt['unit'] == unit and receipt['host_boot_id'] == facts['host_boot_id']
                and receipt['sends'] is True and receipt['complete'] is True
                and receipt['service_format'] == 'MR1.request'
                and receipt['original_application_expires_epoch_us'] == context['application_expires_epoch_us'],
                'native_verification_requires_original_sent_request')
        def deadline_current():
            epoch, mono = clock()
            deadline = receipt['original_application_deadline_us']
            require(type(deadline) is int and mono < deadline <= context['application_deadline_monotonic_us']
                    and epoch < context['application_expires_epoch_us'], 'native_request_original_deadline_expired_or_extended')
        deadline_current()
        request = _read(request_directory / 'sender.bin', 1024)
        require(len(request) == plan['object']['bytes'] and hashlib.sha256(request).hexdigest() == plan['object']['sha256'],
                'native_request_changed')
        result = verify_chunk(root, identifier(response_run_id), facts=facts, clock=clock,
            request=request, campaign_id=campaign_id, manifest_sha256=digest,
            destination=public / 'received', trust=trust,
            request_deadline_us=receipt['original_application_deadline_us'])
        current()
        deadline_current()
        require(json.loads(_read(request_directory / 'result.json', 100000)) == receipt
                and json.loads(_read(request_directory / 'grant.json', 16384)) == plan
                and _read(request_directory / 'sender.bin', 1024) == request, 'native_request_changed_during_verification')
        _write(root / 'runs' / response_run_id / 'verified-piece.json', json.dumps(result, sort_keys=True).encode() + b'\n')
        return result
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unit', required=True, choices=('unit-a', 'unit-b', 'unit-c'))
    parser.add_argument('--campaign-id', required=True)
    parser.add_argument('--request-run-id', required=True)
    parser.add_argument('--response-run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.unit, args.campaign_id, args.request_run_id, args.response_run_id), sort_keys=True))


if __name__ == '__main__':
    main()
