"""Offline profiler reports real journal costs without inventing RF evidence."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'attempt_journal_profile', ROOT / 'tools/benchmarks/profile_attempt_journal.py')
PROFILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILE)


def test_profile_reports_full_wal_and_retains_all_synthetic_and_sampled_debt():
    report = PROFILE.profile([0, 17], samples=3)
    assert report['schema'] == 'meshticulum.attempt-journal-profile.v1'
    assert report['fixture']['history_seed'] == 'synthetic_NOT_SENT_rows_in_one_FULL_transaction'
    assert report['fixture']['latency_progress_handler_enabled'] is False
    assert 'No RF' in report['scope']
    assert len(report['source']['journal_source_sha256']) == 64
    assert report['environment']['clock'] == 'time.perf_counter_ns'
    for history, case in zip((0, 17), report['cases']):
        assert case['synthetic_history_attempts'] == history
        assert case['durability'] == {'journal_mode': 'wal', 'synchronous': 2}
        assert case['initial_state'] == dict(attempts=history,
            reserved_estimated_airtime_us=history * PROFILE.ESTIMATE_US,
            fenced=False, recovered_unknown=0)
        assert case['actual_reservations'] == 4
        assert case['final_state'] == dict(attempts=history + 4,
            reserved_estimated_airtime_us=(history + 4) * PROFILE.ESTIMATE_US,
            fenced=False, recovered_unknown=0)
        assert case['sql_totals_match'] is True
        assert case['pending_attempts'] == case['unreleased_queue_records'] == 0
        assert case['timing_history_range']['snapshot'] == [history, history]
        assert case['timing_history_range']['reserve_queued'] == [history, history + 2]
        assert case['vm_diagnostic']['history_before_snapshot_and_reserve'] == history + 3
        assert case['vm_diagnostic']['timings_excluded'] is True
        for name in ('snapshot', 'reserve_queued', 'settle_not_sent'):
            stats = case['timings_ns'][name]
            assert stats['count'] == 3
            assert 0 <= stats['minimum'] <= stats['median'] <= stats['maximum']
            assert stats['minimum'] <= stats['p95'] <= stats['maximum']
            assert stats['p95_method'] == 'nearest_rank'
            assert case['vm_diagnostic'][name + '_instructions'] > 0


def test_profile_reservations_are_committed_before_settlement(monkeypatch):
    original = PROFILE.AttemptJournal
    observed = []
    class InspectJournal(original):
        def __init__(self, path, **kwargs):
            self.path = path
            super().__init__(path, **kwargs)

        def reserve(self, packet, estimate, **kwargs):
            request = super().reserve(packet, estimate, **kwargs)
            # A second connection sees the actual committed reservation.
            with sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True) as reader:
                row = reader.execute('SELECT a.state,a.estimated_us,q.released '
                    'FROM attempts a JOIN queued_attempts q USING(request) '
                    'WHERE request=?', (request,)).fetchone()
            observed.append(row)
            return request
    monkeypatch.setattr(PROFILE, 'AttemptJournal', InspectJournal)
    PROFILE.profile([2], samples=2)
    assert observed == [('RESERVED', PROFILE.ESTIMATE_US, 0)] * 3


@pytest.mark.parametrize('histories,samples', [
    ([], 1), ([0] * 9, 1), ([0, 0], 1), ([-1], 1), ([50_001], 1),
    ([False], 1), ([1.5], 1), ('0', 1), ([0], 0), ([0], 129),
    ([0], True), ([0], 1.5),
])
def test_invalid_limits_are_rejected_before_creating_journals(monkeypatch, histories, samples):
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid arguments opened a journal')
    monkeypatch.setattr(PROFILE, 'AttemptJournal', forbidden)
    with pytest.raises(ValueError):
        PROFILE.profile(histories, samples)


def test_cli_rejects_out_of_range_sample_count(capsys):
    with pytest.raises(SystemExit) as error:
        PROFILE.main(['--histories', '0', '--samples', '0'])
    assert error.value.code == 2
    assert '1..128' in capsys.readouterr().err


def test_durability_mismatch_is_not_reported_as_a_valid_profile(monkeypatch):
    monkeypatch.setattr(PROFILE, 'settings', lambda journal:
                        {'journal_mode': 'wal', 'synchronous': 1})
    with pytest.raises(RuntimeError, match='durability settings changed'):
        PROFILE.profile([0], samples=1)


def test_percentiles_use_nearest_rank_without_timing_thresholds():
    assert PROFILE.distribution(list(range(1, 21))) == {
        'count': 20, 'minimum': 1, 'maximum': 20, 'median': 10.5,
        'p95': 19, 'p95_method': 'nearest_rank'}


def test_vm_progress_handler_is_removed_even_on_failure():
    with sqlite3.connect(':memory:') as db:
        def fail():
            raise LookupError('sample failed')
        with pytest.raises(LookupError, match='sample failed'):
            PROFILE.vm_instructions(db, fail)
        # Install our own callback after failure; the diagnostic helper has
        # no retained sample closure or handler ownership after returning.
        calls = []
        db.set_progress_handler(lambda: calls.append(1) or 0, 1)
        db.execute('SELECT 1').fetchone()
        db.set_progress_handler(None, 0)
        assert calls
