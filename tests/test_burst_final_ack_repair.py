"""Paired source recovery controls; modeled time is not an RF speed claim."""
from collections import Counter
import math
from types import SimpleNamespace

import pytest

from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.bench.serial_worker import AttemptResult, estimated_airtime_us
from meshticulum.contracts.negotiation import CommitOutcome
from meshticulum.scheduler.budget import Cost
from tests import test_burst_sf5_recovery as model


class PreviousAckPolicy(BenchFlow):
    """Keep both examples opted in; disable only the new scheduling hook."""
    def _service_ack_recovery(self, now_us):
        return False


def paired(tmp_path, monkeypatch, **faults):
    with monkeypatch.context() as patch:
        patch.setattr(model, 'BenchFlow', PreviousAckPolicy)
        before = model.exercise(tmp_path / 'before', receipt_confirmation=True, **faults)
    after = model.exercise(tmp_path / 'after', receipt_confirmation=True, **faults)
    return before, after


def test_clean_transfer_changes_no_transmission_or_modeled_elapsed_time(tmp_path, monkeypatch):
    before, after = paired(tmp_path, monkeypatch)
    for results, air in (before, after):
        model.assert_complete(results, air)
        assert results[0]['snapshot']['retransmits'] == 0
        assert results[1]['snapshot']['counts'].get('final_ACK_reannouncement_requested', 0) == 0
    assert before[1].timeline.now == after[1].timeline.now
    assert [(f.channel.index, f.inner) for f in before[1].frames] == [
        (f.channel.index, f.inner) for f in after[1].frames]


def test_one_final_ack_reannouncement_replaces_data_retry(tmp_path, monkeypatch):
    before, after = paired(tmp_path, monkeypatch, drop_final_ack=True)
    for results, air in (before, after):
        model.assert_complete(results, air)
        assert not any(frame.collision for frame in air.frames)
    assert before[0][0]['snapshot']['retransmits'] == 1
    assert after[0][0]['snapshot']['retransmits'] == 0
    assert after[1].timeline.now < before[1].timeline.now
    assert len([f for f in before[1].frames if 'seq' in f.inner]) == 10
    assert len([f for f in after[1].frames if 'seq' in f.inner]) == 9
    final = [f for f in after[1].frames if f.inner.get('ack_seq') == 9]
    assert len(final) == 2 and final[0].lost and not final[1].lost
    assert final[0].inner == final[1].inner  # same canonical revision and credit
    assert final[0].packet != final[1].packet  # fresh authenticated wire nonce
    assert after[0][1]['snapshot']['counts']['final_ACK_reannouncement_requested'] == 1
    assert after[0][1]['snapshot']['counts']['final_ACK_reannouncement_satisfied'] == 1
    assert after[0][1]['snapshot']['attempt_journal']['attempts'] == 5


def test_same_data_losses_fit_original_twenty_ack_budget_with_idle_backoff(tmp_path, monkeypatch):
    faults = {0: 1, 2: 1, 5: 2}
    before, after = paired(tmp_path, monkeypatch, receiver_cap=20, drop_data=faults)
    assert not before[0][0]['complete'] and before[0][0]['snapshot']['acked_frames'] == 8
    assert str(before[0][0]['error']) == 'reliable_flow_retry_ceiling'
    assert before[0][1]['complete']
    assert before[0][1]['snapshot']['attempt_journal']['attempts'] == 20
    model.assert_complete(*after)
    assert after[0][0]['snapshot']['retransmits'] == 4
    assert after[0][0]['snapshot']['attempt_journal']['attempts'] == 14  # 13 DATA + receipt CONTROL
    assert after[0][1]['snapshot']['attempt_journal']['attempts'] == 12
    assert after[0][1]['snapshot']['counts']['tx_ACK_RF_DONE_unchanged'] == 6
    assert after[1].timeline.now < before[1].timeline.now
    assert before[1].drop_data == after[1].drop_data == {0: 0, 2: 0, 5: 0}
    for results, air in (before, after):
        assert not any(frame.collision for frame in air.frames)
        assert all(not r['snapshot']['attempt_journal']['fenced'] for r in results)
        assert all(r['snapshot']['attempt_journal']['recovered_unknown'] == 0 for r in results)
        assert not air.channels[0].events and not any(c.armed for c in air.channels)


@pytest.mark.parametrize('host_work_us', [(10000, 10000), (40000, 10000), (120000, 40000)])
def test_new_policy_preserves_clean_slow_host_controls(tmp_path, monkeypatch, host_work_us):
    before, after = paired(tmp_path, monkeypatch, host_work_us=host_work_us)
    for results, air in (before, after):
        model.assert_complete(results, air)
        assert not any(frame.collision for frame in air.frames)
        assert results[0]['snapshot']['retransmits'] == 0
    assert before[0][0]['snapshot']['attempt_journal'] == after[0][0]['snapshot']['attempt_journal']


def receiver_gate(*, complete=False):
    """Isolate the gate at the boundary before frozen ACK dispatch.

    Sentinel pending records and failures must pass through unchanged so the
    existing driver can settle or refuse them, rather than being hidden by an
    optional idle-persistence timer.
    """
    requests = []
    flow = SimpleNamespace(_ack_dirty=False, _ack_pending=None, startup_pending=False,
        complete=complete, failure=None, grant_startup=lambda: requests.append('canonical_snapshot'))
    journal = SimpleNamespace(max_attempts=20, max_airtime_us=100000,
        snapshot=lambda: dict(attempts=4, reserved_estimated_airtime_us=40000))
    receiver = SimpleNamespace(_sf5_ack_recovery_eligible=True, receipt_confirmation_enabled=True,
        closed=False, worker=SimpleNamespace(fenced=False), flow=flow, receipt_confirmed=False,
        _ack_persistence_streak=2, _ack_persistence_due_us=2000000,
        _final_ack_repeat_due_us=500000, _final_ack_repeat_requested=False,
        _final_ack_repeat_satisfied=False, _receipt_ready=lambda: True,
        authority=SimpleNamespace(application_live=lambda: True), counts=Counter(),
        journal=journal, _ack_reservation_us=13057,
        owner=SimpleNamespace(cost=Cost(1000, 2000), retained=[]),
        ledger=SimpleNamespace(policy=SimpleNamespace(max_reservations=1), snapshot=lambda now: dict(
            own_remaining_us=100000, shared_remaining_us=100000, sample_fresh=True,
            fenced=False, recovery_until_us=0, pending_reservations=0)))
    return receiver, requests


@pytest.mark.parametrize('field,value', [('_ack_dirty', True), ('_ack_pending', object()),
                                        ('startup_pending', True)])
def test_pending_or_fresh_ack_is_never_held_by_idle_backoff(field, value):
    receiver, requests = receiver_gate()
    setattr(receiver.flow, field, value)
    assert BenchFlow._service_ack_recovery(receiver, 1000000) is False
    assert getattr(receiver.flow, field) is value
    assert requests == []


def test_first_repeat_keeps_existing_frozen_clock_origin():
    receiver, requests = receiver_gate()
    receiver._ack_persistence_streak = 0
    assert BenchFlow._service_ack_recovery(receiver, 0) is False
    assert requests == []


@pytest.mark.parametrize('fault', ['expired', 'not_committed', 'cancelled', 'worker_fenced',
                                   'closed', 'confirmed', 'disabled', 'wrong_profile'])
def test_final_reannouncement_does_not_bypass_original_guards(fault):
    receiver, requests = receiver_gate(complete=True)
    if fault == 'expired': receiver.authority.application_live = lambda: False
    if fault == 'not_committed': receiver._receipt_ready = lambda: False
    if fault == 'cancelled': receiver.flow.failure = 'cancelled'
    if fault == 'worker_fenced': receiver.worker.fenced = True
    if fault == 'closed': receiver.closed = True
    if fault == 'confirmed': receiver.receipt_confirmed = True
    if fault == 'disabled': receiver.receipt_confirmation_enabled = False
    if fault == 'wrong_profile': receiver._sf5_ack_recovery_eligible = False
    assert BenchFlow._service_ack_recovery(receiver, 1000000) is False
    assert requests == [] and receiver.counts == {}
    assert receiver._final_ack_repeat_due_us == 500000


@pytest.mark.parametrize('budget', ['attempts', 'airtime'])
def test_optional_final_reannouncement_skips_exhausted_durable_budget(budget):
    receiver, requests = receiver_gate(complete=True)
    receiver.journal.snapshot = lambda: dict(attempts=20 if budget == 'attempts' else 4,
        reserved_estimated_airtime_us=100000 if budget == 'airtime' else 40000)
    for _ in range(3):
        assert BenchFlow._service_ack_recovery(receiver, 1000000) is False
    assert requests == []
    assert receiver.counts == {'final_ACK_reannouncement_budget_unavailable': 1}


def test_failed_accounting_read_is_preserved_without_requesting_transmission():
    receiver, requests = receiver_gate(complete=True)
    failure = OSError('model journal unavailable')
    def unavailable():
        raise failure
    receiver.journal.snapshot = unavailable
    with pytest.raises(OSError) as caught:
        BenchFlow._service_ack_recovery(receiver, 1000000)
    assert caught.value is failure and requests == []


def test_optional_repeat_cannot_exhaust_owner_compatibility_retention():
    receiver, requests = receiver_gate(complete=True)
    receiver.owner.retained = [object()] * 1023
    assert BenchFlow._service_ack_recovery(receiver, 1000000) is False
    assert requests == [] and len(receiver.owner.retained) == 1023
    assert receiver.counts == {'final_ACK_reannouncement_budget_unavailable': 1}


def test_final_snapshot_request_is_one_shot_and_does_not_renew_its_timer():
    receiver, requests = receiver_gate(complete=True)
    for now in (500000, 1000000, 2000000):
        assert BenchFlow._service_ack_recovery(receiver, now) is False
    assert requests == ['canonical_snapshot']
    assert receiver.counts == {'final_ACK_reannouncement_requested': 1}
    assert receiver._final_ack_repeat_due_us == 500000


@pytest.mark.parametrize('outcome', ['REFUSED', 'NOT_SENT', 'UNKNOWN'])
def test_actual_optional_ack_dispatch_failure_is_charged_without_false_success(tmp_path, monkeypatch, outcome):
    receivers = []
    class FaultedAck(BenchFlow):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.test_faulted = False
            self.test_record = None
            if not self.sends:
                receivers.append(self)
                original = self.worker.transmit
                def transmit(packet):
                    if self._final_ack_repeat_requested and not self.test_faulted:
                        self.test_faulted = True
                        estimate = math.ceil(estimated_airtime_us(model.PROFILE, len(packet)) * 1.10) + 1000
                        request = self.journal.reserve(packet, estimate)
                        self.journal.settle(request, packet, outcome, 'source_worker_fault')
                        if outcome == 'UNKNOWN':
                            self.worker.fenced = True
                        return AttemptResult(request, outcome, estimate, None)
                    return original(packet)
                self.worker.transmit = transmit

        def _send(self, frame):
            before = (self._last_ack_snapshot, self._ack_persistence_streak,
                      self._final_ack_repeat_due_us, self._final_ack_repeat_satisfied)
            result = super()._send(frame)
            if self.test_faulted and self.test_record is None:
                self.test_record = (before, (self._last_ack_snapshot, self._ack_persistence_streak,
                    self._final_ack_repeat_due_us, self._final_ack_repeat_satisfied), result)
            return result

    monkeypatch.setattr(model, 'BenchFlow', FaultedAck)
    results, air = model.exercise(tmp_path, receipt_confirmation=True, drop_final_ack=True)
    receiver, = receivers
    before, after, result = receiver.test_record
    assert before == after and not after[-1]
    assert result is (CommitOutcome.UNKNOWN if outcome == 'UNKNOWN' else CommitOutcome.REFUSED_CARRIER)
    assert results[1]['snapshot']['counts']['final_ACK_reannouncement_requested'] == 1
    if outcome == 'UNKNOWN':
        assert results[1]['snapshot']['attempt_journal']['fenced']
        assert results[1]['snapshot']['counts'].get('final_ACK_reannouncement_satisfied', 0) == 0
        assert 'error' in results[1]
    else:
        # A known-unsent attempt leaves the frozen dirty ACK available; its
        # ordinary next pump succeeds without another optional request.
        model.assert_complete(results, air)
        assert results[1]['snapshot']['attempt_journal']['attempts'] == 6
        assert results[1]['snapshot']['counts']['final_ACK_reannouncement_satisfied'] == 1


@pytest.mark.parametrize('lane', ['own', 'shared'])
def test_completed_receiver_with_exhausted_scheduler_budget_keeps_no_extra_handles(tmp_path, monkeypatch, lane):
    receivers = []
    class SpentBudget(BenchFlow):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.test_injected = False
            self.test_steps = 0
            self.test_complete_at = None
            if not self.sends:
                receivers.append(self)

        def _service_ack_recovery(self, now_us):
            if not self.sends and self._final_ack_repeat_due_us is not None and not self.test_injected:
                # Model previously admitted ledger debt, independently of the
                # still-available durable ACK attempt budget. No refund/reset.
                budget = self.ledger.snapshot(now_us)
                cost = (Cost(budget['own_remaining_us'], budget['own_remaining_us']) if lane == 'own'
                        else Cost(1, budget['shared_remaining_us']))
                booked = self.ledger.reserve(cost, now_us)
                self.ledger.settle(booked, CommitOutcome.ACCEPTED, now_us)
                self.test_injected = True
                self.test_retained = len(self.owner.retained)
            return super()._service_ack_recovery(now_us)

        def step(self):
            complete = super().step()
            if complete:
                self.test_steps += 1
                if self.sends:
                    self.test_complete_at = self.test_complete_at or self.clock()[1]
                    # Leave the completed receiver alive for over1024 pumps.
                    return self.clock()[1] >= self.test_complete_at + 7_000_000
            return complete

        def confirm_receipt(self):
            result = super().confirm_receipt()
            # Let the receiver authenticate CONTROL before the driver stops.
            self.worker.channel.timeline.sleep(0, 30000)
            return result

    monkeypatch.setattr(model, 'BenchFlow', SpentBudget)
    results, air = model.exercise(tmp_path, receipt_confirmation=True)
    model.assert_complete(results, air)
    receiver, = receivers
    assert receiver.test_steps > 1024
    assert len(receiver.owner.retained) == receiver.test_retained == 4
    assert results[1]['snapshot']['attempt_journal']['attempts'] == 4
    assert results[1]['snapshot']['counts']['final_ACK_reannouncement_budget_unavailable'] == 1
    assert results[1]['snapshot']['counts']['rx_CONTROL'] == 1
    assert results[1]['snapshot']['receipt_confirmation']['peer_receipt_confirmed']


def test_an_earlier_successful_second_final_ack_satisfies_optional_timer(tmp_path, monkeypatch):
    receivers = []
    class EarlyReannouncement(BenchFlow):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.test_reannounced = False
            if not self.sends:
                receivers.append(self)

        def _service_ack_recovery(self, now_us):
            if not self.sends and self._final_ack_repeat_due_us is not None and not self.test_reannounced:
                self.test_reannounced = True
                # An external request for the existing canonical snapshot
                # exercises the same second-success boundary as duplicate DATA.
                self.flow.grant_startup()
            return super()._service_ack_recovery(now_us)

    monkeypatch.setattr(model, 'BenchFlow', EarlyReannouncement)
    results, air = model.exercise(tmp_path, receipt_confirmation=True, drop_final_ack=True)
    model.assert_complete(results, air)
    receiver, = receivers
    assert receiver._final_ack_repeat_satisfied and receiver._final_ack_repeat_due_us is None
    assert not receiver._final_ack_repeat_requested
    assert results[1]['snapshot']['counts']['final_ACK_reannouncement_satisfied'] == 1
    assert len([frame for frame in air.frames if frame.inner.get('ack_seq') == 9]) == 2
