"""Debt accounting stays exact across migration, rollback and retained history."""
import hashlib
import sqlite3

import pytest

from meshticulum.bench.attempts import AttemptError, AttemptJournal


OPTIONS = dict(binding=b'a' * 32, max_attempts=10_000, max_airtime_us=3_000_000)


def open_journal(path, **options):
    return AttemptJournal(path, request_clock=lambda: 100, **(OPTIONS | options))


def assert_debt(journal, count, reserved):
    state = journal.snapshot()
    assert (state['attempts'], state['reserved_estimated_airtime_us']) == (count, reserved)
    assert journal.db.execute('SELECT count(*),coalesce(sum(estimated_us),0) '
                              'FROM attempts').fetchone() == (count, reserved)


@pytest.mark.parametrize('pending', [False, True])
def test_legacy_journal_rebuild_preserves_debt_fence_and_request_sequence(tmp_path, pending):
    tmp_path.chmod(0o700)
    path = tmp_path / 'legacy.db'
    # The schema before aggregate accounting, including consumed refusals.
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE owner (id INTEGER PRIMARY KEY CHECK(id=1), '
            'binding BLOB, max_attempts INTEGER, max_airtime INTEGER, request INTEGER, fenced INTEGER);'
            'CREATE TABLE attempts (request INTEGER PRIMARY KEY, packet_sha BLOB NOT NULL, '
            'bytes INTEGER NOT NULL, estimated_us INTEGER NOT NULL, state TEXT NOT NULL, evidence TEXT);')
        db.execute('INSERT INTO owner VALUES (1,?,?,?,?,0)',
                   (OPTIONS['binding'], 4, 1000, 300))
        db.executemany('INSERT INTO attempts VALUES (?,?,?,?,?,NULL)', [
            (100, hashlib.sha256(b'a').digest(), 1, 100, 'REFUSED'),
            (200, hashlib.sha256(b'b').digest(), 1, 200, 'NOT_SENT'),
            (300, hashlib.sha256(b'c').digest(), 1, 300, 'RESERVED' if pending else 'RF_DONE')])
    path.chmod(0o600)
    journal = open_journal(path, max_attempts=4, max_airtime_us=1000)
    try:
        assert_debt(journal, 3, 600)
        assert journal.snapshot()['fenced'] is pending
        assert journal.snapshot()['recovered_unknown'] == int(pending)
        assert journal.db.execute('PRAGMA synchronous').fetchone() == (2,)
        assert journal.db.execute('PRAGMA journal_mode').fetchone() == ('wal',)
        if pending:
            with pytest.raises(AttemptError, match='attempt_run_fenced'):
                journal.reserve(b'd', 400)
        else:
            assert journal.reserve(b'd', 400) == 301
            with pytest.raises(AttemptError, match='attempt_budget_exhausted'):
                journal.reserve(b'e', 1)
            assert_debt(journal, 4, 1000)
    finally:
        journal.close()


def test_failed_queue_insert_rolls_back_request_and_all_debt(tmp_path):
    tmp_path.chmod(0o700)
    journal = open_journal(tmp_path / 'rollback.db', create=True)
    try:
        first = journal.reserve(b'a', 100)
        journal.settle(first, b'a', 'REFUSED', 'synthetic refusal')
        journal.db.execute("CREATE TRIGGER fail_queue BEFORE INSERT ON queued_attempts "
                           "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match='injected failure'):
            journal.reserve(b'b', 200, queue_metadata=b'synthetic')
        assert_debt(journal, 1, 100)
        journal.db.execute('DROP TRIGGER fail_queue')
        assert journal.reserve(b'b', 200, queue_metadata=b'synthetic') == first + 1
        assert_debt(journal, 2, 300)
    finally:
        journal.close()


def test_settlement_release_replay_and_recovery_never_refund(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'states.db'
    journal = open_journal(path, create=True)
    try:
        for outcome in ('RF_DONE', 'REFUSED', 'NOT_SENT', 'UNKNOWN'):
            request = journal.reserve(b'packet', 100, queue_metadata=b'synthetic')
            event = b'event' if outcome in ('RF_DONE', 'UNKNOWN') else None
            journal.settle_queued(request, b'packet', outcome, 'synthetic test', event)
            assert not journal.settle_queued(request, b'packet', outcome, 'synthetic test', event)
            if event is not None:
                assert journal.release_queued(request, event)
                assert not journal.release_queued(request, event)
        assert_debt(journal, 4, 400)
    finally:
        journal.close()
    reopened = open_journal(path)
    try:
        assert_debt(reopened, 4, 400)
        assert reopened.snapshot()['fenced']
        assert reopened.command_request(containment=True) > request
        assert_debt(reopened, 4, 400)
        with pytest.raises(AttemptError, match='attempt_run_fenced'):
            reopened.reserve(b'new', 100)
    finally:
        reopened.close()


def test_accounting_read_work_does_not_grow_with_retained_attempts(tmp_path):
    tmp_path.chmod(0o700)
    journal = open_journal(tmp_path / 'scale.db', create=True)
    try:
        def steps():
            count = 0
            def progress():
                nonlocal count
                count += 1
                return 0
            journal.db.set_progress_handler(progress, 1)
            try:
                journal.snapshot()
            finally:
                journal.db.set_progress_handler(None, 0)
            return count
        initial = steps()
        # Synthetic settled history in one transaction: no hardware claims.
        with journal.transaction():
            journal.db.executemany('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                ((i + 1, b'a' * 32, 1, 100, 'NOT_SENT', 'synthetic fixture') for i in range(5000)))
        assert_debt(journal, 5000, 500_000)
        assert steps() <= initial * 2 + 10
    finally:
        journal.close()


def test_debt_and_queue_metadata_are_visible_in_same_durable_commit(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'visibility.db'
    journal = open_journal(path, create=True)
    try:
        request = journal.reserve(b'packet', 123, queue_metadata=b'synthetic')
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as observer:
            assert observer.execute('SELECT a.request,a.estimated_us,q.metadata,t.attempts,t.reserved_us '
                'FROM attempts a JOIN queued_attempts q USING(request) CROSS JOIN attempt_totals t '
                'WHERE t.id=1').fetchone() == (request, 123, b'synthetic', 1, 123)
    finally:
        journal.close()


@pytest.mark.parametrize('damage', ['missing', 'malformed'])
def test_live_invalid_totals_refuse_then_reopen_reconstructs_debt(tmp_path, damage):
    tmp_path.chmod(0o700)
    path = tmp_path / 'rebuild.db'
    journal = open_journal(path, create=True)
    try:
        request = journal.reserve(b'packet', 123)
        journal.settle(request, b'packet', 'NOT_SENT', 'synthetic')
        if damage == 'missing':
            journal.db.execute('DELETE FROM attempt_totals')
        else:
            journal.db.execute('PRAGMA ignore_check_constraints=ON')
            journal.db.execute("UPDATE attempt_totals SET reserved_us='corrupt'")
        with pytest.raises(AttemptError, match='invalid_attempt_totals'):
            journal.snapshot()
        with pytest.raises(AttemptError, match='invalid_attempt_totals'):
            journal.reserve(b'new', 123)
        assert journal.db.execute('SELECT count(*) FROM attempts').fetchone() == (1,)
    finally:
        journal.close()
    reopened = open_journal(path)
    try:
        assert_debt(reopened, 1, 123)
        assert reopened.reserve(b'new', 123) == request + 1
        assert_debt(reopened, 2, 246)
    finally:
        reopened.close()


def test_wrong_owner_cannot_rebuild_projection(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'owner.db'
    journal = open_journal(path, create=True)
    journal.db.execute('DELETE FROM attempt_totals')
    journal.close()
    with pytest.raises(AttemptError, match='attempt_state_binding_mismatch'):
        open_journal(path, binding=b'wrong'.ljust(32, b'!'))
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as observer:
        assert observer.execute('SELECT * FROM attempt_totals').fetchall() == []
