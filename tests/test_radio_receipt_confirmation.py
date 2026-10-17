"""Real authority, cipher, journal and frozen scheduler with source-model radios.

These fault controls establish adapter behavior, not physical goodput.
"""
import pytest

from meshticulum.bench.serial_worker import AttemptResult
from meshticulum.bench.wire import FrameKind, HEADER, WireError
from meshticulum.resources import byte_bundle
from tests.test_radio_file_example import example, Clock, BOOT, pair, close_pair, finish
from tests.test_radio_bundle_example import published, open_pair

CONTEXT = b'C' * 32


def frame_kind(packet):
    return FrameKind(HEADER.unpack_from(packet)[2])


def open_confirming_pair(tmp_path, *, context=CONTEXT, receiver_context=None):
    clock = Clock()
    payload = b'original public bytes' * 30
    descriptor = example.manifest(payload)['chunks'][0]
    opened, transfers, workers = pair(tmp_path, descriptor, clock,
        clock()[0], clock()[0] + 240000000, [0])
    transfers[0].enable_receipt_confirmation(context)
    transfers[1].enable_receipt_confirmation(receiver_context or context)
    transfers[0].send(payload)
    return opened, transfers, workers, clock, payload


def complete_sender_flow(transfers, clock):
    """Stop before the example offers its optional confirmation."""
    sender, receiver = transfers
    for _ in range(1000):
        sender.endpoint.step()
        receiver.step()
        if sender.endpoint.flow.complete:
            assert receiver.completed_at is not None
            return
        clock.advance(20000)
    raise AssertionError('source flow did not receive final ACK')


def drop_confirmations(worker):
    original = worker.transmit
    dropped = []

    def transmit(packet):
        result = original(packet)
        if frame_kind(packet) is FrameKind.CONTROL:
            assert worker.peer.incoming.pop() == packet
            dropped.append(packet)
        return result

    worker.transmit = transmit
    return dropped


def test_opt_in_confirmation_closes_after_durable_acceptance_with_real_accounting(tmp_path):
    opened, transfers, workers, clock, payload = open_confirming_pair(tmp_path)
    sender, receiver = transfers
    observed = []
    original = workers[0].transmit

    def inspect_transmit(packet):
        if frame_kind(packet) is FrameKind.CONTROL:
            assert sender.endpoint.flow.complete
            assert receiver.completed_at is not None
            assert receiver.cache.get(receiver.descriptor) == payload
            assert sender.authority.held  # Real service/broker leases are held.
            observed.append((workers[0].journal.snapshot(), sender.endpoint.owner.snapshot()))
        return original(packet)

    workers[0].transmit = inspect_transmit
    try:
        finish(transfers, clock)
        assert len(observed) == 1
        before, owner_before = observed[0]
        journal = workers[0].journal.snapshot()
        assert journal['attempts'] == before['attempts'] + 1
        assert journal['reserved_estimated_airtime_us'] == before['reserved_estimated_airtime_us'] + 12000
        assert sender.endpoint.owner.snapshot()['rf_attempts'] == owner_before['rf_attempts'] + 1
        assert receiver.endpoint.receipt_confirmed
        assert clock()[1] - receiver.completed_at < example.ACK_GRACE_US
        assert sender.endpoint.receipt_confirmation_outcome == 'RF_DONE'
        controls = [packet for packet in workers[0].sent if frame_kind(packet) is FrameKind.CONTROL]
        counters = [HEADER.unpack_from(packet)[5] for packet in workers[0].sent]
        assert len(controls) == 1 and len(controls[0]) == 79
        assert len(counters) == len(set(counters))
        assert sender.endpoint.cipher.store._db.execute('SELECT tx FROM state').fetchone()[0] == max(counters)
        assert receiver.endpoint.counts['rx_CONTROL'] == 1
        assert not sender.endpoint.scheduler.snapshot()['fenced_flows']
        snapshot = sender.endpoint.receipt_confirmation_snapshot()
        assert snapshot == dict(enabled=True, attempted=True, outcome='RF_DONE', peer_receipt_confirmed=False)
        before = journal
        assert sender.endpoint.confirm_receipt() is True
        assert workers[0].journal.snapshot() == before
    finally:
        close_pair(opened)


def test_confirmation_cannot_be_sent_before_authenticated_final_ack(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    try:
        before = workers[0].journal.snapshot()
        with pytest.raises(WireError, match='before_receipt'):
            transfers[0].endpoint.confirm_receipt()
        assert workers[0].journal.snapshot() == before
        assert not transfers[0].endpoint._receipt_requested
    finally:
        close_pair(opened)


def test_lost_confirmation_keeps_original_nonrenewing_grace(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    dropped = drop_confirmations(workers[0])
    try:
        finish(transfers, clock)
        assert len(dropped) == 1
        assert not transfers[1].endpoint.receipt_confirmed
        assert clock()[1] - transfers[1].completed_at >= example.ACK_GRACE_US
        assert transfers[0].endpoint.receipt_confirmation_outcome == 'RF_DONE'
    finally:
        close_pair(opened)


@pytest.mark.parametrize('fault', ['wrong_context', 'tamper', 'wrong_direction', 'wrong_session', 'premature_replay'])
def test_invalid_confirmation_never_shortens_grace_or_changes_its_start(tmp_path, fault):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path,
        receiver_context=b'W' * 32 if fault == 'wrong_context' else CONTEXT)
    sender, receiver = transfers
    dropped = drop_confirmations(workers[0])
    try:
        premature = None
        if fault == 'premature_replay':
            premature = sender.endpoint.cipher.seal(sender.endpoint._receipt_payload,
                FrameKind.CONTROL, authorize=sender.authority.live)
            workers[1].incoming.append(premature)
            receiver.step()
            assert not receiver.endpoint.receipt_confirmed and receiver.completed_at is None
            clock.advance(20000)
        complete_sender_flow(transfers, clock)
        completed = receiver.completed_at
        assert sender.endpoint.confirm_receipt()
        assert len(dropped) == 1
        packet = dropped[0]
        if fault == 'tamper':
            packet = packet[:-1] + bytes([packet[-1] ^ 1])
        elif fault == 'wrong_direction':
            packet = receiver.endpoint.cipher.seal(sender.endpoint._receipt_payload,
                FrameKind.CONTROL, authorize=receiver.authority.live)
        elif fault == 'wrong_session':
            header = list(HEADER.unpack_from(packet))
            header[4] = b'F' * 16
            packet = HEADER.pack(*header) + packet[HEADER.size:]
        elif fault == 'premature_replay':
            packet = premature
        workers[1].incoming.append(packet)
        assert receiver.step() is False
        assert not receiver.endpoint.receipt_confirmed
        assert receiver.completed_at == completed
        assert receiver.endpoint.counts['rx_refused'] > 0
        if fault == 'premature_replay':
            assert any(event['event'] == 'rx_refused' and 'replay' in event['reason']
                for event in receiver.endpoint.events)
        clock.advance(completed + example.ACK_GRACE_US - 1 - clock()[1])
        assert receiver.step() is False
        assert receiver.completed_at == completed
        clock.advance(1)
        assert receiver.step() is True
    finally:
        close_pair(opened)


def test_expiry_before_confirmation_refuses_without_attempt_or_retirement(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    try:
        complete_sender_flow(transfers, clock)
        before = [worker.journal.snapshot() for worker in workers]
        clock.advance(240000001)
        with pytest.raises(WireError, match='before_receipt'):
            transfers[0].endpoint.confirm_receipt()
        with pytest.raises(WireError):
            transfers[1].step()
        assert not transfers[1].endpoint.receipt_confirmed
        assert [worker.journal.snapshot() for worker in workers] == before
    finally:
        close_pair(opened)


def test_confirmation_unknown_write_is_charged_and_fails_instead_of_success(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    original = workers[0].transmit
    before = []

    def uncertain(packet):
        if frame_kind(packet) is not FrameKind.CONTROL:
            return original(packet)
        before.append(workers[0].journal.snapshot())
        request = workers[0].journal.reserve(packet, 12000)
        workers[0].journal.settle(request, packet, 'UNKNOWN', 'source_control_unknown')
        workers[0].fenced = True
        return AttemptResult(request, 'UNKNOWN', 12000, None)

    workers[0].transmit = uncertain
    try:
        with pytest.raises(WireError):
            finish(transfers, clock)
        journal = workers[0].journal.snapshot()
        assert journal['fenced'] and journal['attempts'] == before[0]['attempts'] + 1
        assert journal['reserved_estimated_airtime_us'] == before[0]['reserved_estimated_airtime_us'] + 12000
        assert not transfers[1].endpoint.receipt_confirmed
        assert transfers[0].endpoint.receipt_confirmation_outcome == 'UNKNOWN'
    finally:
        close_pair(opened)


@pytest.mark.parametrize('sender_enabled,receiver_enabled', [(True, False), (False, True), (False, False)])
def test_old_and_mixed_configuration_keep_original_grace(tmp_path, sender_enabled, receiver_enabled):
    clock = Clock()
    data = b'compatible bytes'
    descriptor = example.manifest(data)['chunks'][0]
    opened, transfers, workers = pair(tmp_path, descriptor, clock,
        clock()[0], clock()[0] + 240000000, [0])
    try:
        for enabled, transfer in zip((sender_enabled, receiver_enabled), transfers):
            if enabled:
                transfer.enable_receipt_confirmation(CONTEXT)
        transfers[0].send(data)
        finish(transfers, clock)
        assert clock()[1] - transfers[1].completed_at >= example.ACK_GRACE_US
        assert not transfers[1].endpoint.receipt_confirmed
    finally:
        close_pair(opened)


@pytest.mark.parametrize('fault', ['none', 'lost_final_ack', 'corruption'])
def test_five_part_signed_100k_file_retires_early_without_changing_original_window(tmp_path, published, fault):
    data, raw, signature, trust, digest, manifest, _ = published
    clock = Clock()
    issued, expires = clock()[0], clock()[0] + 240000000
    original_end = clock()[1] + 240000000
    state, quarantine = tmp_path / 'state', tmp_path / 'receiver'
    state.mkdir(mode=0o700)
    quarantine.mkdir(mode=0o700)
    mcu_counter, total_retries, confirmations = [0], 0, []
    for index in range(5):
        window = example.FileWindow(state, dict(sha256=digest, parts=5), issued, expires,
            time_source=clock, boot_reader=lambda: BOOT)
        assert window.deadline_us == original_end
        window.require_capacity(5 - index)  # Preserve worst-case fallback capacity.
        with byte_bundle.Receiver(quarantine, raw, signature, trust, digest,
                expires_epoch_us=expires, clock=window.check, boot_reader=lambda: BOOT) as receiver:
            opened, transfers, workers = open_pair(tmp_path / str(index), published, index,
                clock, window, receiver, mcu_counter, fault if index == 0 else 'none', confirm_receipt=True)
            try:
                finish(transfers, clock)
                assert transfers[1].endpoint.receipt_confirmed
                assert clock()[1] - transfers[1].completed_at < example.ACK_GRACE_US
                total_retries += transfers[0].endpoint.flow.retransmits
                confirmations.extend(packet for packet in workers[0].sent
                    if frame_kind(packet) is FrameKind.CONTROL)
                for worker in workers:
                    assert not worker.journal.snapshot()['fenced']
                if index == 4:
                    result = receiver.finish()
                    assert result['complete'] and result['publisher_verified']
                    assert result['useful_decoded_bytes'] == 100000
                    assert result['whole_file_sha256'] == example.sha(data)
                    assert result['original_expires_epoch_us'] == expires
                    assert result['original_deadline_monotonic_us'] <= original_end
            finally:
                close_pair(opened)
    assert len(confirmations) == 5 and 0 < mcu_counter[0] < 256
    if fault != 'none':
        assert total_retries > 0
    assert (quarantine / 'bundle.bin').read_bytes() == data
    assert clock()[1] < original_end


def test_known_unsent_confirmation_keeps_paid_attempt_and_original_grace(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    original = workers[0].transmit
    before = []

    def not_sent(packet):
        if frame_kind(packet) is not FrameKind.CONTROL:
            return original(packet)
        before.append(workers[0].journal.snapshot())
        request = workers[0].journal.reserve(packet, 12000)
        workers[0].journal.settle(request, packet, 'NOT_SENT', 'source_control_not_sent')
        return AttemptResult(request, 'NOT_SENT', 12000, None)

    workers[0].transmit = not_sent
    try:
        finish(transfers, clock)
        journal = workers[0].journal.snapshot()
        assert not journal['fenced'] and journal['attempts'] == before[0]['attempts'] + 1
        assert journal['reserved_estimated_airtime_us'] == before[0]['reserved_estimated_airtime_us'] + 12000
        assert transfers[0].endpoint.receipt_confirmation_outcome == 'NOT_SENT'
        assert not transfers[1].endpoint.receipt_confirmed
        assert clock()[1] - transfers[1].completed_at >= example.ACK_GRACE_US
    finally:
        close_pair(opened)


def test_owner_loss_after_final_ack_refuses_confirmation_without_dispatch(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    try:
        complete_sender_flow(transfers, clock)
        before = workers[0].journal.snapshot()
        workers[0].owned = False
        with pytest.raises(WireError, match='before_receipt'):
            transfers[0].endpoint.confirm_receipt()
        assert workers[0].journal.snapshot() == before
        assert not transfers[1].endpoint.receipt_confirmed
    finally:
        close_pair(opened)


def test_deadline_crossed_during_confirmation_keeps_debt_and_refuses_success(tmp_path):
    opened, transfers, workers, clock, _ = open_confirming_pair(tmp_path)
    original = workers[0].transmit
    before = []

    def expires_during_write(packet):
        if frame_kind(packet) is FrameKind.CONTROL:
            before.append(workers[0].journal.snapshot())
            result = original(packet)
            clock.advance(240000001)
            return result
        return original(packet)

    workers[0].transmit = expires_during_write
    try:
        with pytest.raises(WireError, match='authority_expired|scheduler_flow_fenced'):
            finish(transfers, clock)
        journal = workers[0].journal.snapshot()
        assert journal['attempts'] == before[0]['attempts'] + 1
        assert journal['reserved_estimated_airtime_us'] == before[0]['reserved_estimated_airtime_us'] + 12000
        assert not transfers[1].endpoint.receipt_confirmed
        assert transfers[0].endpoint.receipt_confirmation_outcome == 'RF_DONE'
    finally:
        close_pair(opened)
