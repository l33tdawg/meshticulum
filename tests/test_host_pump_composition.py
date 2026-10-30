"""Paired independent-host radio model, never physical throughput evidence."""
import pytest

from tests.test_burst_sf5_recovery import assert_complete, exercise


@pytest.mark.parametrize('parameters', [
    {},
    {'drop_final_ack': True},
    {'drop_data': {0: 1}},
    {'drop_data': {0: 1, 2: 1, 5: 2}},
    {'host_work_us': (40_000, 10_000)},
    {'host_work_us': (120_000, 40_000)},
], ids=['clean', 'lost-final-ack', 'lost-data', 'four-data-losses', 'host40-10', 'host120-40'])
def test_paired_host_pump_preserves_delivery_debt_and_physical_cleanup(tmp_path, parameters):
    pairs = []
    for enabled in (False, True):
        results, air = exercise(tmp_path / str(enabled), host_pump=enabled,
                                receipt_confirmation=True, **parameters)
        assert_complete(results, air)
        for result in results:
            journal = result['snapshot']['attempt_journal']
            assert journal['recovered_unknown'] == 0
            # Every charged operation remains retained at its actual outcome.
            assert sum(count for _, count in result['settled_states']) == journal['attempts']
            assert journal['attempts'] > 0 and journal['reserved_estimated_airtime_us'] > 0
        pairs.append((results, air))
    # Input faults and payload remain identical. This checks the improvement's
    # mechanism under a controlled clean schedule, not an RF rate threshold.
    if parameters == {}:
        assert pairs[1][1].timeline.now < pairs[0][1].timeline.now
        assert not any(frame.collision for _, air in pairs for frame in air.frames)
        assert [r['snapshot']['attempt_journal'] for r in pairs[0][0]] == [
            r['snapshot']['attempt_journal'] for r in pairs[1][0]]
