"""Adversarial adapter checks; these controls do not establish radio throughput."""
from dataclasses import replace

import pytest

from meshticulum.bench.serial_worker import AttemptResult
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import FrameKind, SessionCipher, WireError
from meshticulum.contracts.errors import ContractError
from tests.test_radio_receipt_confirmation import (
    BOOT, close_pair, complete_sender_flow, drop_confirmations, example, frame_kind, open_confirming_pair,
)


@pytest.mark.parametrize('failure', ['unknown_result', 'exception_after_write'])
def test_confirmation_uncertainty_fails_without_relying_on_worker_flag(tmp_path, failure):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    sender, receiver = transfers
    try:
        complete_sender_flow(transfers, clock)
        worker = workers[0]
        journal_before = worker.journal.snapshot()
        owner_before = sender.endpoint.owner.snapshot()
        tx_before = sender.endpoint.cipher.store._db.execute('SELECT tx FROM state').fetchone()[0]
        original = worker.transmit
        calls = []

        def uncertain(packet):
            assert frame_kind(packet) is FrameKind.CONTROL
            calls.append(packet)
            if failure == 'exception_after_write':
                # The carrier may have delivered bytes before its API failed.
                original(packet)
                raise OSError('confirmation result lost after write')
            request = worker.journal.reserve(packet, 12000)
            worker.journal.settle(request, packet, 'UNKNOWN', 'audit_unknown_control')
            return AttemptResult(request, 'UNKNOWN', 12000, None)

        worker.transmit = uncertain
        with pytest.raises(WireError, match='receipt_confirmation_write_unknown'):
            sender.step()
        assert not worker.fenced  # The adapter must inspect the actual dispatch outcome.
        journal_after = worker.journal.snapshot()
        assert len(calls) == 1
        assert journal_after['attempts'] == journal_before['attempts'] + 1
        assert journal_after['reserved_estimated_airtime_us'] == journal_before['reserved_estimated_airtime_us'] + 12000
        assert sender.endpoint.owner.snapshot()['rf_attempts'] == owner_before['rf_attempts'] + 1
        assert sender.endpoint.cipher.store._db.execute('SELECT tx FROM state').fetchone()[0] == tx_before + 1
        with pytest.raises(WireError, match='receipt_confirmation_write_unknown'):
            sender.endpoint.confirm_receipt()
        assert len(calls) == 1  # No retry under an unresolved disposition.
        assert worker.journal.snapshot() == journal_after
    finally:
        close_pair(opened)


@pytest.mark.parametrize('fault', ['short', 'long', 'domain', 'as_ack', 'as_data', 'wrong_key'])
def test_authenticated_control_cannot_be_relabelled_or_forge_context(tmp_path, fault):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    sender, receiver = transfers
    try:
        complete_sender_flow(transfers, clock)
        completed = receiver.completed_at
        sender_cipher = sender.endpoint.cipher
        payload = sender.endpoint._receipt_payload
        kind = FrameKind.CONTROL
        if fault == 'short':
            payload = payload[:-1]
        elif fault == 'long':
            payload += b'X'
        elif fault == 'domain':
            payload = b'ARC1' + payload[4:]
        elif fault == 'as_ack':
            kind = FrameKind.ACK
        elif fault == 'as_data':
            kind = FrameKind.DATA
        elif fault == 'wrong_key':
            sender_cipher = SessionCipher(replace(sender_cipher.grant, secret=b'W' * 32),
                sender.authority.local_peer, sender_cipher.store)
        before = receiver.endpoint.cipher.store._db.execute('SELECT high,bitmap FROM state').fetchone()
        packet = sender_cipher.seal(payload, kind, authorize=sender.authority.live)
        clock.advance(20000)
        workers[1].incoming.append(packet)
        assert receiver.step() is False
        assert receiver.completed_at == completed
        assert not receiver.endpoint.receipt_confirmed
        assert receiver.endpoint.counts['rx_refused'] > 0
        after = receiver.endpoint.cipher.store._db.execute('SELECT high,bitmap FROM state').fetchone()
        if fault == 'wrong_key':
            assert before == after  # Unauthenticated ciphertext never moves durable replay state.
        else:
            assert before != after  # Admitted-but-invalid records cannot be replayed later.
        clock.advance(20000)
        assert sender.endpoint.confirm_receipt() is True
        assert receiver.step() is True
        assert receiver.endpoint.receipt_confirmed
        assert receiver.completed_at == completed
    finally:
        close_pair(opened)


def test_valid_peer_control_in_sender_role_is_rejected_without_an_attempt(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    sender, receiver = transfers
    try:
        complete_sender_flow(transfers, clock)
        before = workers[0].journal.snapshot()
        packet = receiver.endpoint.cipher.seal(sender.endpoint._receipt_payload,
            FrameKind.CONTROL, authorize=receiver.authority.live)
        clock.advance(20000)
        workers[0].incoming.append(packet)
        sender.endpoint.step()
        assert sender.endpoint.counts['rx_refused'] > 0
        assert not sender.endpoint.receipt_confirmed
        assert not sender.endpoint._receipt_requested
        assert workers[0].journal.snapshot() == before
    finally:
        close_pair(opened)


def test_confirmation_budget_refusal_spends_no_nonce_or_worker_attempt(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    sender, receiver = transfers
    try:
        complete_sender_flow(transfers, clock)
        before = workers[0].journal.snapshot()
        tx_before = sender.endpoint.cipher.store._db.execute('SELECT tx FROM state').fetchone()[0]

        def refuse(*args, **kwargs):
            raise ContractError('audit_budget_exhausted')

        sender.endpoint.ledger.reserve = refuse
        assert sender.endpoint.confirm_receipt() is False
        assert sender.endpoint.receipt_confirmation_snapshot() == dict(
            enabled=True, attempted=False, outcome='NOT_DISPATCHED', peer_receipt_confirmed=False)
        assert workers[0].journal.snapshot() == before
        assert sender.endpoint.cipher.store._db.execute('SELECT tx FROM state').fetchone()[0] == tx_before
        assert sender.endpoint.scheduler.snapshot()['queued_frames'] == 0
        assert sender.endpoint.confirm_receipt() is False
        clock.advance(20000)
        assert receiver.step() is False
        completed = receiver.completed_at
        clock.advance(completed + example.ACK_GRACE_US - clock()[1])
        assert receiver.step() is True
    finally:
        close_pair(opened)


def test_confirmation_noise_is_bounded_and_never_renews_grace(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    sender, receiver = transfers
    dropped = drop_confirmations(workers[0])
    try:
        complete_sender_flow(transfers, clock)
        completed = receiver.completed_at
        assert sender.endpoint.confirm_receipt()
        assert len(dropped) == 1
        malformed = b'BAD1' + sender.endpoint._receipt_payload[4:]
        packets = [sender.endpoint.cipher.seal(malformed, FrameKind.CONTROL,
            authorize=sender.authority.live) for _ in range(19)]
        workers[1].incoming.extend(packets)
        clock.advance(20000)
        refused = receiver.endpoint.counts['rx_refused']
        assert receiver.step() is False
        assert len(workers[1].incoming) == 11
        assert receiver.endpoint.counts['rx_refused'] == refused + 8
        clock.advance(20000)
        assert receiver.step() is False
        assert len(workers[1].incoming) == 3
        clock.advance(20000)
        assert receiver.step() is False
        assert not workers[1].incoming
        assert receiver.completed_at == completed
        clock.advance(completed + example.ACK_GRACE_US - 1 - clock()[1])
        assert receiver.step() is False
        clock.advance(1)
        assert receiver.step() is True
        assert not receiver.endpoint.receipt_confirmed
    finally:
        close_pair(opened)


@pytest.mark.parametrize('changed', ['generation', 'initiator_chain', 'responder_agent', 'route', 'issued', 'expiry'])
def test_control_from_different_original_grant_cannot_advance_replay(tmp_path, changed):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    sender, receiver = transfers
    foreign_store = None
    try:
        complete_sender_flow(transfers, clock)
        grant = sender.endpoint.cipher.grant
        session = grant.session
        if changed == 'generation':
            grant = replace(grant, session=replace(session, session_generation=b'G' * 16))
        elif changed == 'initiator_chain':
            grant = replace(grant, session=replace(session,
                initiator=replace(session.initiator, chain_id='different-origin-chain')))
        elif changed == 'responder_agent':
            grant = replace(grant, session=replace(session,
                responder=replace(session.responder, agent_id='c' * 64)))
        elif changed == 'route':
            grant = replace(grant, route_binding_digest=b'R' * 32)
        elif changed == 'issued':
            grant = replace(grant, issued_epoch_us=grant.issued_epoch_us + 1)
        else:
            grant = replace(grant, expires_epoch_us=grant.expires_epoch_us - 1)
        foreign = tmp_path / 'foreign-state'
        foreign.mkdir(mode=0o700)
        foreign_store = SessionStore(foreign / 'wire.db', grant, grant.session.initiator,
            boot_id=BOOT, clock=clock, create=True)
        cipher = SessionCipher(grant, grant.session.initiator, foreign_store)
        # Same compact session id, direction and current confirmation plaintext.
        # The original peer, generation, route and window still have to match.
        packet = cipher.seal(sender.endpoint._receipt_payload, FrameKind.CONTROL,
            authorize=lambda: True)
        before = receiver.endpoint.cipher.store._db.execute('SELECT high,bitmap FROM state').fetchone()
        completed = receiver.completed_at
        clock.advance(20000)
        workers[1].incoming.append(packet)
        assert receiver.step() is False
        assert not receiver.endpoint.receipt_confirmed
        assert receiver.completed_at == completed
        assert receiver.endpoint.cipher.store._db.execute('SELECT high,bitmap FROM state').fetchone() == before
        assert any(e['event'] == 'rx_refused' and e['reason'] == 'authentication_failed'
            for e in receiver.endpoint.events)
        clock.advance(20000)
        assert sender.endpoint.confirm_receipt()
        assert receiver.step() is True
    finally:
        if foreign_store is not None:
            foreign_store.retire()
            foreign_store.close()
        close_pair(opened)
