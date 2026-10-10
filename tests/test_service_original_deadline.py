"""Fresh radio admission keeps a host's original resource deadline."""
import json

import pytest
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.wire import WireError
from tests.service_helpers import scope
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision


def admitted(path, clock, *, deadline=None):
    plan = provision(path, 'unit-a', b'public fixture', clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('MR1.response', clock)
    if deadline is not None:
        plan['application_deadline_monotonic_us'] = deadline
    (path / 'grant.json').write_text(json.dumps(plan))
    return RunAuthority(path, expected_unit='unit-a', host_boot_id=BOOT, clock=clock, owner_probe=lambda: True)


def test_host_local_deadline_tightens_without_changing_the_peer_wire_context(tmp_path):
    clock = Clock()
    with_first = admitted(tmp_path / 'first', clock, deadline=clock()[1] + 2000000)
    legacy = admitted(tmp_path / 'legacy', clock)
    try:
        assert with_first.grant.digest == legacy.grant.digest
        assert with_first.application_deadline_us == clock()[1] + 2000000
        assert with_first.deadlines.application_deadline_monotonic < legacy.deadlines.application_deadline_monotonic
        clock.advance(2000000)
        assert not with_first.application_live() and legacy.application_live()
    finally:
        with_first.close(); legacy.close()


def test_declared_host_deadline_cannot_extend_the_epoch_limit(tmp_path):
    clock = Clock()
    authority = admitted(tmp_path / 'large-declaration', clock, deadline=clock()[1] + 120000000)
    try:
        assert authority.application_deadline_us == clock()[1] + 60000000
    finally:
        authority.close()


@pytest.mark.parametrize('deadline', [True, 1.5, None, -1, 2**63])
def test_invalid_or_expired_local_deadline_is_refused_before_single_use_claim(tmp_path, deadline):
    clock = Clock()
    directory = tmp_path / 'invalid'
    plan = provision(directory, 'unit-a', b'public fixture', clock)
    plan['schema'] = 'meshticulum.service-run.v1'
    plan['service'] = scope('MR1.response', clock)
    plan['application_deadline_monotonic_us'] = deadline
    (directory / 'grant.json').write_text(json.dumps(plan))
    with pytest.raises(WireError, match='invalid_original_service_deadline'):
        RunAuthority(directory, expected_unit='unit-a', host_boot_id=BOOT, clock=clock, owner_probe=lambda: True)
    assert not (directory / 'started').exists()
