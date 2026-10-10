"""Fixed native public-pilot metadata operations; no transport activation."""
import hashlib
import json
from pathlib import Path

from ..ip.node import _facts, _read, clock
from ..ip.journal import private
from .pilot_provision import identifier
from .wire import require

STATE = Path('/var/lib/meshticulum-bench')


def facts(unit):
    value = _facts(unit)
    epoch, mono = clock()
    return dict(schema='meshticulum.native-pilot-facts.v1', **value,
                epoch_us=epoch, monotonic_us=mono, radio_opened=False, socket_opened=False)


def result(unit, run_id):
    """Read completed public metadata only; never grants, keys or payloads."""
    directory = STATE / 'runs' / identifier(run_id)
    private(directory.lstat(), True)
    raw = _read(directory / 'result.json', 300000)
    value = json.loads(raw)
    require(value['schema'] in ('meshticulum.physical-service-run.v1', 'meshticulum.ip-public-node-run.v1')
            and value['unit'] == unit and value['session_id'] == run_id
            and value['service_format'] in ('MR1.request', 'MR1.response'), 'wrong_public_pilot_result')
    return dict(schema='meshticulum.public-pilot-result-record.v1', unit=unit, run_id=run_id,
                result_sha256=hashlib.sha256(raw).hexdigest(), result=value)


def cache_status(unit,campaign_id):
    """Recheck only the signed public cache for this original campaign."""
    from .pilot_context import original_context
    from ..resources import lamplighter as ll
    native=_facts(unit)
    campaign=STATE/'campaigns'/identifier(campaign_id);private(campaign.lstat(),True)
    saved=json.loads(_read(campaign/'context.json',2048))
    context=original_context(campaign,unit=unit,host_boot_id=native['host_boot_id'],
        resource_sha256=saved['resource_sha256'],expires_epoch_us=saved['application_expires_epoch_us'],clock=clock)
    public=STATE/'public'/context['resource_sha256'];private(public.lstat(),True)
    trust=json.loads(_read(public/'trust.json',16384))
    cache=ll.VerifiedCache(public/'received',trust,context['resource_sha256'])
    have=[];sizes={}
    for entry in cache.value['chunks']:
        try:raw=cache.piece(entry['sha256'])
        except (OSError,ValueError):continue
        have.append(entry['sha256']);sizes[entry['sha256']]=len(raw)
    return dict(schema='meshticulum.public-pilot-cache-status.v1',unit=unit,campaign_id=campaign_id,
        host_boot_id=native['host_boot_id'],manifest_sha256=context['resource_sha256'],
        verified_chunks=sorted(have),useful_verified_bytes=sum(sizes.values()),chunk_bytes=sizes,
        original_application_expires_epoch_us=context['application_expires_epoch_us'],
        original_application_deadline_us=context['application_deadline_monotonic_us'])
