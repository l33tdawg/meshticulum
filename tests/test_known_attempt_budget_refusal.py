"""Known pre-admission refusal with two physical attempts still pending."""
import pytest
from tests.test_burst_runtime import composition
from meshticulum.bench.tx_burst import Stage
from meshticulum.bench.wire import WireError

@pytest.mark.parametrize('marker', [False, True])
def test_exhaustion_with_two_pending_is_known_refusal_not_unknown(composition, marker):
    case = composition(b'x' * 2000, train_depth=8, train32=True,
                       turn_end=marker, host_attempts=3)
    case.sender.send(case.data)
    for _ in range(8):
        case.receiver.step(); case.sender.step()
        if case.journals[0].snapshot()['attempts'] == 3:
            break
    assert len(case.workers[0].pending_physical) == 3
    request = next(iter(case.channels[0].packets))
    case.channels[0].event(request, Stage.RF_DONE)
    with pytest.raises(WireError, match='^bench_host_attempt_budget_exhausted$'):
        case.sender.step()
    snapshot = case.sender.snapshot()
    assert snapshot['attempt_journal']['attempts'] == 3
    assert snapshot['counts']['tx_QUEUED'] == 3
    assert snapshot['counts']['tx_RF_DONE'] == 1
    assert snapshot['scheduler']['events'].get('unknown', 0) == 0
    assert snapshot['scheduler']['events']['refused'] == 1
    assert len(case.workers[0].pending_physical) == 2
    assert case.sender.flow.failure is None and not case.sender.flow.complete
    assert not snapshot['attempt_journal']['fenced']
    # Terminal diagnostic cannot become another retry/reservation on next step.
    with pytest.raises(WireError, match='^bench_host_attempt_budget_exhausted$'):
        case.sender.step()
    assert case.journals[0].snapshot()['attempts'] == 3
    original_deadline = case.workers[0]._burst_deadline
    for pending in case.workers[0].pending_physical:
        case.channels[0].event(pending, Stage.RF_DONE)
    case.sender.close()
    assert case.sender.counts['tx_RF_DONE'] == 3
    assert not case.workers[0].fenced and not case.workers[0].pending_physical
    assert case.workers[0]._burst_deadline == original_deadline
    assert case.journals[0].snapshot()['attempts'] == 3


def test_marked_tail_budget_refusal_keeps_seal_and_pending_debt(composition):
    case = composition(b'x' * 772, train_depth=8, train32=True,
                       turn_end=True, host_attempts=3)
    case.sender.send(case.data)
    for _ in range(8):
        case.receiver.step(); case.sender.step()
        if case.journals[0].snapshot()['attempts'] == 3:
            break
    request = next(iter(case.channels[0].packets))
    case.channels[0].event(request, Stage.RF_DONE)
    with pytest.raises(WireError, match='^bench_host_attempt_budget_exhausted$'):
        case.sender.step()
    worker = case.workers[0]
    assert worker._turn_end_seal is not None and worker._turn_end_failed
    assert not worker.has_capacity and len(worker.pending_physical) == 2
    assert case.journals[0].snapshot()['attempts'] == 3
    assert case.sender.counts['tx_DATA_turn_end'] == 0
    from meshticulum.bench.linux_hat import HardwareError
    deadline = worker._burst_deadline
    started = case.clock()[1]
    with pytest.raises(HardwareError, match='burst_drain_deadline'):
        case.sender.close()
    assert case.clock()[1] - started <= 3_000_000
    assert worker.fenced and worker._cancelled and worker._burst_deadline == deadline
    # Normal containment disarm subsequently drains cancelled physical evidence.
    worker.disarm()
    assert not worker.pending_physical
    assert case.journals[0].snapshot()['attempts'] == 3
    assert case.journals[0].snapshot()['fenced']
    assert not case.sender.flow.complete


def test_post_admission_typed_error_remains_unknown(composition):
    from meshticulum.bench.attempts import AttemptBudgetExhausted
    case = composition(b'x' * 2000, train_depth=8, train32=True, host_attempts=3)
    original = case.workers[0].queue_data
    def after_admission(*args, **kwargs):
        original(*args, **kwargs)
        raise AttemptBudgetExhausted(attempts=3, reserved_us=1,
            max_attempts=3, max_airtime_us=100000, required_us=1)
    case.workers[0].queue_data = after_admission
    case.sender.send(case.data); case.receiver.step()
    with pytest.raises(WireError, match='reliable_flow_uncertain_carrier_handover'):
        case.sender.step()
    assert case.journals[0].snapshot()['attempts'] == 1
    assert case.sender.host_attempt_budget_failure is None
    assert case.sender.scheduler.snapshot()['events']['unknown'] == 1
    # The injected post-admission fault hides accepted attribution. Preserve
    # the existing cleanup error instead of pretending this was known-unsent.
    with pytest.raises(WireError, match='foreign_physical_result'):
        case.sender.close()
    assert case.workers[0].fenced
    assert not case.workers[0].pending_physical
    assert case.journals[0].snapshot()['attempts'] == 1



def test_exact_reservation_rollback_failure_is_not_known_refusal(composition):
    import sqlite3
    case = composition(b'x' * 2000, train_depth=8, train32=True, host_attempts=3)
    case.sender.send(case.data)
    for _ in range(8):
        case.receiver.step(); case.sender.step()
        if case.journals[0].snapshot()['attempts'] == 3:
            break
    case.channels[0].event(next(iter(case.channels[0].packets)), Stage.RF_DONE)
    journal = case.journals[0]; original = journal.db
    class FailRollback:
        def execute(self, statement, *args):
            if statement == 'ROLLBACK':
                raise sqlite3.OperationalError('injected rollback failure')
            return original.execute(statement, *args)
    journal.db = FailRollback()
    try:
        with pytest.raises(WireError, match='reliable_flow_uncertain_carrier_handover'):
            case.sender.step()
        assert case.sender.host_attempt_budget_failure is None
        assert case.sender.scheduler.snapshot()['events']['unknown'] == 1
    finally:
        journal.db = original
        if original.in_transaction:
            original.rollback()
    assert journal.snapshot()['attempts'] == 3

@pytest.mark.parametrize('limit', ['attempts', 'airtime'])
def test_budget_signal_requires_no_new_request_or_debt(tmp_path, limit):
    from meshticulum.bench.attempts import AttemptJournal, AttemptBudgetExhausted
    tmp_path.chmod(0o700)
    journal = AttemptJournal(tmp_path / 'attempts.db', binding=b'x' * 32,
        max_attempts=1 if limit == 'attempts' else 3, max_airtime_us=10,
        create=True, request_clock=lambda: 100)
    try:
        journal.reserve(b'paid', 6)
        before = journal.snapshot()
        request_before = journal.db.execute('SELECT request FROM owner').fetchone()
        with pytest.raises(AttemptBudgetExhausted) as raised:
            journal.reserve(b'unadmitted', 6)
        assert str(raised.value) == 'attempt_budget_exhausted'
        assert journal.snapshot() == before
        assert journal.db.execute('SELECT request FROM owner').fetchone() == request_before
        assert raised.value.details['attempts'] == 1
        assert raised.value.details['reserved_estimated_airtime_us'] == 6
    finally:
        journal.close()
