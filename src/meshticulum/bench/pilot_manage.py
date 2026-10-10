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
