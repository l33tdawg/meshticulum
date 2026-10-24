"""Production LoRa authority/worker/journal/flow composition; radio is modeled.

The finite worker and doubled frozen ledger retain their independent limits.
These are accounting regressions, not RF or throughput measurements.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.objects import VerifiedChunks
from meshticulum.bench.serial_worker import SerialWorker, journal_binding
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher, WireError
from meshticulum.scheduler.budget import BudgetPolicy, Ledger
from tests.radio_profiles import SOURCE_ADMISSION, profile_for
from tests.test_burst_runtime import RadioChannel
from tests.test_burst_worker import VERSION
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision
from tests.test_radio_file_example import example


@pytest.fixture
def composition(tmp_path):
    clock = Clock()
    data = b'x' * (45 * 193 + 1)
    descriptor = example.manifest(data)['chunks'][0]
    profile = replace(profile_for('sf7-500'), lease_ms=90000,
                      max_boot_attempts=240, max_boot_estimated_airtime_us=10000000)
    channels = [RadioChannel(clock), RadioChannel(clock)]
    channels[0].peer, channels[1].peer = channels[1], channels[0]
    opened, transfers, workers = [], [], []
    # Provision both endpoints before serial negotiation advances either clock.
    for unit in ('unit-a', 'unit-b'):
        plan = provision(tmp_path / unit, unit, data, clock, profile)
        assert plan['object'] == descriptor
    for index, unit in enumerate(('unit-a', 'unit-b')):
        path = tmp_path / unit
        authority = RunAuthority(path, expected_unit=unit, host_boot_id=BOOT,
            clock=clock, owner_probe=lambda: True, radio_admission=SOURCE_ADMISSION)
        worker = SerialWorker(channels[index])
        identity = worker.discover(expected_board_id=1, expected_version=VERSION)
        journal = AttemptJournal(path / 'attempts.db',
            binding=journal_binding(authority.grant.digest, identity, profile),
            max_attempts=profile.max_boot_attempts,
            max_airtime_us=profile.max_boot_estimated_airtime_us,
            create=True, request_clock=lambda: clock()[0])
        worker.arm(journal=journal, profile=profile, context_digest=authority.grant.digest,
            authorize=authority.application_live,
            authority_deadline=authority.deadlines.application_deadline_monotonic)
        store = SessionStore(path / 'wire.db', authority.grant, authority.local_peer,
                             boot_id=BOOT, clock=clock, create=True)
        transfer = example.ChunkTransfer(authority, worker, journal,
            SessionCipher(authority.grant, authority.local_peer, store), descriptor,
            VerifiedChunks(tmp_path / 'chunks'), clock=clock)
        opened.append((transfer, store, authority, journal))
        transfers.append(transfer)
        workers.append(worker)
    case = SimpleNamespace(clock=clock, data=data, sender=transfers[0].endpoint,
        receiver=transfers[1].endpoint, transfers=transfers, workers=workers)
    try:
        yield case
    finally:
        for transfer, store, authority, journal in opened:
            transfer.close()
            store.close()
            authority.close()
            journal.close()


def advance(case, count=1000):
    for _ in range(count):
        case.sender.step()
        case.receiver.step()
        case.clock.advance(20000)
        if case.sender.flow.complete and case.receiver.flow.complete:
            return
    raise AssertionError('modeled composition did not finish within bound')


def test_production_10m_lora_budget_stops_at_exact_46th_unsent_reservation(composition):
    case = composition
    sender = case.sender
    sender.send(case.data)
    with pytest.raises(WireError, match='^bench_own_airtime_budget_exhausted$'):
        advance(case)
    failure = sender.snapshot()['dispatch_budget_failure']
    assert sender.link.last_result.reason == failure['reason'] == 'dispatch_budget_refused'
    assert not sender.link.last_result.invoked
    assert sender.journal.snapshot() == dict(attempts=45, fenced=False, recovered_unknown=0,
                                            reserved_estimated_airtime_us=4990275)
    assert sender.owner.cost.own_tx_us == failure['required_own_us'] == 110895
    assert sender.ledger.policy.own_tx_limit_us == failure['own_limit_us'] == 10000000
    budget = failure['ledger']
    assert budget['debt_own_us'] == 4990275
    assert budget['own_used_us'] == 9980551
    assert budget['own_used_us'] + failure['required_own_us'] == 10091446
    assert budget['pending_reservations'] == 0
    assert budget['window_end_us'] >= failure['original_send_deadline_us']
    assert len(sender.owner.retained) == 46
    assert sender.scheduler.snapshot()['events']['refused'] == 1
    before = sender.journal.snapshot(), sender.scheduler.snapshot(), len(sender.owner.retained)
    case.clock.advance(500000)
    with pytest.raises(WireError, match='^bench_own_airtime_budget_exhausted$'):
        sender.step()
    assert before == (sender.journal.snapshot(), sender.scheduler.snapshot(), len(sender.owner.retained))


def test_temporary_broker_refusal_remains_recoverable(composition):
    case = composition
    # Startup ACK refusal is confirmed unsent; restoring the same live owner
    # permits its original emission, with no newly minted grant or deadline.
    case.receiver.owner.no_send = False
    original = case.receiver.link.broker
    case.receiver.link.broker = lambda request: None
    case.receiver.step()
    assert case.receiver.link.last_result.reason == 'broker_authority_refused'
    assert case.receiver.dispatch_budget_failure is None
    assert case.workers[1].journal.snapshot()['attempts'] == 0
    case.receiver.link.broker = original
    case.sender.send(b'one frame')
    advance(case)
    assert case.sender.flow.complete
    assert case.sender.dispatch_budget_failure is None


def test_final_ack_can_complete_after_its_retry_is_budget_refused(composition):
    case = composition
    sender = case.sender
    ledger = Ledger(BudgetPolicy(120000000, 110896, 221792, 1000000,
                                 quantization_us=1), sender.ledger.epoch, case.clock()[1])
    sender.ledger = sender.scheduler.ledger = ledger
    sender.update_counter()
    sender.send(b'last frame')
    for _ in range(50):
        case.receiver.step()
        sender.step()
        if sender.journal.snapshot()['attempts'] == 1:
            break
        case.clock.advance(20000)
    else:
        raise AssertionError('expected one handed-over final DATA')
    assert sender.flow.queued_bytes() == 0 and sender.flow.in_flight() == 1
    before = sender.journal.snapshot()
    # Hold the receiver's service turn beyond the source RTO. The retry cannot
    # fit, but the original DATA is already present at the receiver.
    case.clock.advance(2000000)
    sender.step()
    assert sender.link.last_result.reason == 'dispatch_budget_refused'
    assert sender.dispatch_budget_failure is None
    assert sender.journal.snapshot() == before
    assert not sender.flow.complete
    case.receiver.step()
    sender.step()
    assert sender.flow.complete and sender.flow.acked_frames() == 1
    assert sender.dispatch_budget_failure is None
    assert sender.journal.snapshot() == before
    assert ledger.snapshot(case.clock()[1])['debt_own_us'] == 110895


def test_budget_window_before_original_deadline_can_recover_without_refund(composition):
    case = composition
    sender = case.sender
    # This explicitly different source policy has a future eligible window.
    # It retains all accepted debt when the observed-counter window advances.
    ledger = Ledger(BudgetPolicy(2000000, 300000, 1000000, 1000000,
                                 quantization_us=1), sender.ledger.epoch, case.clock()[1])
    sender.ledger = sender.scheduler.ledger = ledger
    sender.update_counter()
    sender.send(b'two frames' * 25)
    for _ in range(150):
        sender.step()
        case.receiver.step()
        if sender.link.last_result is not None and sender.link.last_result.reason == 'dispatch_budget_refused':
            break
        case.clock.advance(20000)
    else:
        raise AssertionError('expected one conservative own-budget refusal')
    assert sender.journal.snapshot()['attempts'] == 1
    assert sender.dispatch_budget_failure is None
    assert ledger.snapshot(case.clock()[1])['debt_own_us'] == 110895
    # The receiver ACKs the first DATA before rollover; that releases credit
    # but refunds no debt and cannot yet make the second reservation fit.
    case.clock.advance(500000)
    case.receiver.step()
    sender.step()
    assert sender.journal.snapshot()['attempts'] == 1
    assert sender.flow.acked_frames() == 1
    case.clock.advance(2000000)
    sender.step()
    assert sender.dispatch_budget_failure is None
    assert sender.journal.snapshot()['attempts'] == 2
    assert ledger.snapshot(case.clock()[1])['debt_own_us'] == 221790
