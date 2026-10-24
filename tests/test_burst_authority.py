"""Opt-in authority is immutable, route-bound and checked before radio use."""
import json

import pytest

from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.burst_protocol import POLICY
from meshticulum.bench.wire import WireError
from tests.radio_profiles import SOURCE_ADMISSION, profile_for
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision


def authority(path, clock):
    return RunAuthority(path, expected_unit='unit-a', host_boot_id=BOOT, clock=clock,
                        owner_probe=lambda: True, radio_admission=SOURCE_ADMISSION)


def grant(path, clock, policy=None, profile='sf7-500'):
    plan = provision(path, 'unit-a', b'bytes', clock, profile_for(profile))
    if policy is not None:
        plan['data_burst'] = policy
        (path / 'grant.json').write_text(json.dumps(plan))
    return plan


def test_policy_changes_shared_session_binding_and_copies_cannot_change_admission(tmp_path):
    clock = Clock()
    grant(tmp_path / 'legacy', clock)
    grant(tmp_path / 'queued', clock, dict(POLICY))
    legacy, queued = authority(tmp_path / 'legacy', clock), authority(tmp_path / 'queued', clock)
    try:
        assert legacy.data_burst_policy is None
        assert legacy.grant.digest != queued.grant.digest
        assert queued.route_record['data_burst'] == POLICY
        policy = queued.data_burst_policy
        policy['max_hold_us'] = 99999999
        assert queued.data_burst_policy == POLICY and queued.live()
        queued.plan['data_burst']['max_hold_us'] = 99999999
        assert not queued.live()
        with pytest.raises(WireError, match='plan_changed'):
            _ = queued.data_burst_policy
    finally:
        legacy.close(); queued.close()


@pytest.mark.parametrize('policy', [dict(POLICY, max_depth=True),
    dict(POLICY, max_hold_us=2000001), dict(POLICY, handover='live-at-RF-start'),
    dict(POLICY, extra='permission')])
def test_unreviewed_policy_refused_before_single_use_claim(tmp_path, policy):
    clock = Clock()
    path = tmp_path / 'unit'
    grant(path, clock, policy)
    with pytest.raises(ValueError, match='invalid_data_burst_policy'):
        authority(path, clock)
    assert not (path / 'started').exists()


def test_slow_waveform_cannot_consume_attempts_trying_to_fill_two_second_queue(tmp_path):
    clock = Clock()
    path = tmp_path / 'unit'
    grant(path, clock, dict(POLICY), profile='baseline')
    with pytest.raises(WireError, match='profile_cannot_fit'):
        authority(path, clock)
    assert not (path / 'started').exists()
