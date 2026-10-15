# SPDX-License-Identifier: Apache-2.0
"""Idle shutdown handoff over real held grants and local carrier queues.

Radio completion is modeled. These controls prove no device retirement,
native owner admission, or renewed right to send.
"""
from contextlib import contextmanager
from dataclasses import replace
import multiprocessing
import os
import threading
import time

import pytest

from meshticulum.privacy.forward_relay import (
    ForwardRelayError, OpaqueForwardRelay, RelayCloseDelegation, describe_stage)
from meshticulum.bench.services import ServiceTransfer
from tests.test_forward_relay import rig, stage
from tests.test_meshticulum_bench_runtime import Clock


def foreign_call(callback):
    """Run once on a real foreign thread, with bounded joining and error capture."""
    result = []

    def run():
        try:
            result.append((True, callback()))
        except BaseException as error:
            result.append((False, error))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(3)
    assert not thread.is_alive(), 'local cleanup did not return within three seconds'
    assert len(result) == 1
    return result[0]


def refused(result):
    ok, value = result
    assert not ok and type(value) is ForwardRelayError


@contextmanager
def begun(rig):
    packet = rig.sessions[0].start()
    with stage(rig, packet, 1) as (incoming, outgoing):
        ingress, egress = incoming.transfers[1], outgoing.transfers[0]
        rig.relay.begin(describe_stage(1, ingress, egress), ingress, egress)
        yield packet, incoming, outgoing


def counters(incoming, outgoing):
    return [flow.journal.snapshot() for flow in incoming.flows + outgoing.flows]


def test_preparation_does_not_read_clock_or_grant_and_keeps_original_owner(rig, monkeypatch):
    owner = rig.relay._owner

    def no_clock(_):
        raise AssertionError('cleanup preparation must not read the live clock')

    with monkeypatch.context() as patch:
        patch.setattr(Clock, '__call__', no_clock)
        token = rig.relay.prepare_close_delegation()
        assert type(token) is RelayCloseDelegation
        assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)
    assert rig.relay._owner == owner == (os.getpid(), threading.get_ident())
    result = rig.relay.snapshot()
    assert result['closed'] and not result['uncertain'] and result['refunds'] == 0
    assert not result['native_retirement_proved'] and not result['private_admission']
    assert not result['durable_replay_proved']
    refused(foreign_call(lambda: rig.relay.close_delegated(token)))
    with pytest.raises(ForwardRelayError):
        rig.relay.prepare_close_delegation()


@pytest.mark.parametrize('method,args', [
    ('prepare_close_delegation', ()), ('begin', (None, None, None)),
    ('step', ()), ('finish_stage', ()), ('snapshot', ()), ('close', ())])
def test_delegation_grants_no_foreign_live_or_owner_methods(rig, method, args):
    token = rig.relay.prepare_close_delegation()
    refused(foreign_call(lambda: getattr(rig.relay, method)(*args)))
    assert not rig.relay.snapshot()['closed']
    assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)


@pytest.mark.parametrize('wrong', ['copy', 'other_relay', 'arbitrary'])
def test_exact_token_and_relay_identity_required_without_spending_original(rig, wrong):
    token = rig.relay.prepare_close_delegation()
    other = OpaqueForwardRelay(rig.description, clock=rig.clock)
    try:
        candidate = (replace(token) if wrong == 'copy' else
                     other.prepare_close_delegation() if wrong == 'other_relay' else object())
        refused(foreign_call(lambda: rig.relay.close_delegated(candidate)))
        refused(foreign_call(lambda: other.close_delegated(token)))
        assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)
    finally:
        other.close()


@pytest.mark.parametrize('change', ['instance_method', 'class_method', 'gate'])
def test_delegation_rechecks_exact_cleanup_methods_and_original_gate(rig, monkeypatch, change):
    token = rig.relay.prepare_close_delegation()
    invoked = []
    with monkeypatch.context() as patch:
        if change == 'gate':
            patch.setattr(rig.relay, '_gate', threading.RLock())
        elif change == 'instance_method':
            patch.setitem(vars(rig.relay), '_retire', lambda: invoked.append('substituted'))
        else:
            patch.setattr(OpaqueForwardRelay, '_retire', lambda self: invoked.append('substituted'))
        refused(foreign_call(lambda: rig.relay.close_delegated(token)))
        assert not invoked
    assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)


def fork_close(relay, token, connection):
    try:
        relay.close_delegated(token)
    except ForwardRelayError:
        connection.send((os.getpid(), 'refused'))
    else:
        connection.send((os.getpid(), 'accepted'))
    finally:
        connection.close()


@pytest.mark.skipif('fork' not in multiprocessing.get_all_start_methods(), reason='requires fork')
def test_inherited_token_is_refused_in_actual_other_process(rig):
    token = rig.relay.prepare_close_delegation()
    context = multiprocessing.get_context('fork')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=fork_close, args=(rig.relay, token, sender))
    process.start()
    sender.close()
    try:
        assert receiver.poll(3), 'forked cleanup did not return'
        child_pid, status = receiver.recv()
        assert child_pid != os.getpid() and status == 'refused'
        process.join(3)
        assert process.exitcode == 0
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
            process.join(3)
    assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)


def test_real_active_producer_refuses_promptly_then_same_token_can_close(rig, monkeypatch):
    token = rig.relay.prepare_close_delegation()
    with begun(rig) as (_, incoming, outgoing):
        entered, attempted = threading.Event(), threading.Event()
        original_clock = Clock.__call__
        result = []

        def held_clock(clock):
            if not entered.is_set():
                entered.set()
                assert attempted.wait(2), 'foreign cleanup did not return while producer was held'
            return original_clock(clock)

        def closer():
            assert entered.wait(2)
            before = time.monotonic()
            try:
                rig.relay.close_delegated(token)
            except BaseException as error:
                result.append((error, time.monotonic() - before))
            finally:
                attempted.set()

        thread = threading.Thread(target=closer, daemon=True)
        thread.start()
        with monkeypatch.context() as patch:
            patch.setattr(Clock, '__call__', held_clock)
            rig.relay.step()
        thread.join(3)
        assert not thread.is_alive() and len(result) == 1
        assert type(result[0][0]) is ForwardRelayError and result[0][1] < 1
        assert not incoming.transfers[1].cancelled and not outgoing.transfers[0].cancelled
        before = counters(incoming, outgoing)
        assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)
        assert counters(incoming, outgoing) == before
        assert incoming.transfers[1].cancelled and outgoing.transfers[0].cancelled


def test_real_held_application_lease_refuses_without_consuming(rig):
    token = rig.relay.prepare_close_delegation()
    with begun(rig) as (_, incoming, outgoing):
        with incoming.transfers[1].authority.application_lease():
            refused(foreign_call(lambda: rig.relay.close_delegated(token)))
            assert not incoming.transfers[1].cancelled
        before = counters(incoming, outgoing)
        assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)
        assert counters(incoming, outgoing) == before


def test_failed_exact_cancellation_consumes_before_callbacks_and_preserves_uncertainty(rig, monkeypatch):
    token = rig.relay.prepare_close_delegation()
    with begun(rig) as (_, incoming, outgoing):
        before = counters(incoming, outgoing)
        substituted = []
        with monkeypatch.context() as patch:
            patch.setitem(vars(incoming.transfers[1]), 'cancel', lambda: substituted.append(True))
            assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, False)
        assert not substituted and outgoing.transfers[0].cancelled
        assert counters(incoming, outgoing) == before
        refused(foreign_call(lambda: rig.relay.close_delegated(token)))
        assert rig.relay.snapshot()['uncertain']
        assert rig.relay.close() is False


def test_accepted_cleanup_spends_token_before_callback_and_still_cancels_other_leg(rig, monkeypatch):
    token = rig.relay.prepare_close_delegation()
    original = ServiceTransfer.cancel
    fail = []
    called = []

    def interrupted_cancel(transfer):
        # The callback's attempt to recursively consume the token must fail.
        with pytest.raises(ForwardRelayError):
            rig.relay.close_delegated(token)
        called.append(transfer)
        original(transfer)
        if transfer in fail:
            raise OSError('local cancellation acknowledgement failed')

    with monkeypatch.context() as patch:
        patch.setattr(ServiceTransfer, 'cancel', interrupted_cancel)
        with begun(rig) as (_, incoming, outgoing):
            fail.append(incoming.transfers[1])
            before = counters(incoming, outgoing)
            assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, False)
            assert called == [incoming.transfers[1], outgoing.transfers[0]]
            assert all(transfer.cancelled for transfer in called)
            assert counters(incoming, outgoing) == before
            assert rig.relay.close() is False
    refused(foreign_call(lambda: rig.relay.close_delegated(token)))


@pytest.mark.parametrize('stale_reservation', [False, True])
def test_real_unsent_queue_reconciliation_preserves_debt_and_reports_unknown(rig, stale_reservation):
    token = rig.relay.prepare_close_delegation()
    with begun(rig) as (packet, incoming, outgoing):
        sender = outgoing.flows[0]
        outgoing.transfers[0].send(packet)
        sender.link.defer_to_host = True
        for _ in range(20):
            outgoing.flows[1].step()
            sender.step()
            if sender.link._held is not None:
                break
            rig.clock.advance(50000)
        assert sender.link._held is not None and sender.owner._live is not None
        original = sender.owner._live
        assert any(sender.scheduler._queues.values())
        before = counters(incoming, outgoing)
        if stale_reservation:
            # The real lease checks its original owner revision; losing that
            # binding leaves an actual unresolved handle during Scheduler.close.
            sender.owner._revision += 1
        assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, not stale_reservation)
        assert counters(incoming, outgoing) == before
        assert sender.closed and sender.owner.no_send and sender.scheduler._closed
        assert not sender.scheduler._queues and not sender.scheduler._flows
        assert bool(sender.scheduler._unresolved) is stale_reservation
        if stale_reservation:
            assert next(iter(sender.scheduler._unresolved.values())) == (sender.owner, original)
        assert rig.relay.snapshot()['uncertain'] is stale_reservation
        assert rig.relay.snapshot()['refunds'] == 0


def test_cleanup_after_expiry_never_reads_live_permission(rig, monkeypatch):
    token = rig.relay.prepare_close_delegation()
    with begun(rig) as (_, incoming, outgoing):
        rig.clock.epoch = rig.context.expires_epoch_us
        before = counters(incoming, outgoing)

        def no_authority(*args, **kwargs):
            raise AssertionError('shutdown must not acquire or refresh authority')

        with monkeypatch.context() as patch:
            for transfer in (incoming.transfers[1], outgoing.transfers[0]):
                patch.setattr(transfer.authority, 'live', no_authority)
                patch.setattr(transfer.authority, 'application_lease', no_authority)
            patch.setattr(Clock, '__call__', no_authority)
            assert foreign_call(lambda: rig.relay.close_delegated(token)) == (True, True)
        assert counters(incoming, outgoing) == before

