"""A partial final handover must close an active repair-fill turn."""
from tests.test_burst_runtime import composition


def test_other_hole_becoming_due_inside_partial_final_handover_cannot_join_train(composition):
    case = composition(b'x' * (9*193), train_depth=8, repair_fill=True)
    for endpoint in (case.sender, case.receiver):
        endpoint.enable_receipt_confirmation(b'c'*32,
            receiver_ready=None if endpoint.sends else lambda: False)
    case.sender.send(case.data)
    for _ in range(100):
        case.receiver.step(); case.sender.step()
        if len(case.workers[0].pending_physical) == 8:
            break
        case.clock.advance(1000)
    assert len(case.workers[0].pending_physical) == 8
    deliver = case.channels[0].deliver
    offered = []
    def lose_two(packet):
        offered.append(packet)
        if len(offered)-1 not in (0, 2):
            deliver(packet)
    case.channels[0].deliver = lose_two
    case.channels[0].finish_packets()
    case.channels[0].deliver = deliver
    for _ in range(500):
        case.receiver.step(); case.sender.step()
        if case.sender._repair_fill_active:
            break
        case.clock.advance(1000)
    assert case.sender._repair_fill_active
    assert case.sender.flow.fast_retransmits == 1
    assert case.sender.flow._in_flight[0].attempts == 2
    assert case.sender.flow._in_flight[2].attempts == 1
    case.channels[0].finish_packets()
    case.sender._poll_physical()
    assert not case.workers[0].pending_physical
    hole = case.sender.flow._in_flight[2]
    due_us = round((hole.sent_at + hole.rto) * 1_000_000)
    case.clock.advance(due_us - 10_000 - case.clock()[1])
    queue = case.workers[0].queue_data
    accepted = []
    def crossing_queue(*args, **kwargs):
        result = queue(*args, **kwargs)
        assert result.outcome == 'QUEUED'
        accepted.append(result.request)
        case.clock.advance(20_000)
        return result
    case.workers[0].queue_data = crossing_queue
    case.sender.step()
    case.workers[0].queue_data = queue
    assert len(accepted) == 1 and case.clock()[1] > due_us
    final = case.sender.flow._in_flight[8]
    assert final.original_last and len(final.payload) == 193
    assert case.sender.flow.queued_bytes() == 0
    before = (case.journals[0].snapshot(), case.sender.owner.rf_attempts,
        len(case.sender.owner.retained), case.stores[0]._db.execute('SELECT tx FROM state').fetchone(),
        case.sender.ledger.snapshot(case.clock()[1]))
    case.sender.step()
    after = (case.journals[0].snapshot(), case.sender.owner.rf_attempts,
        len(case.sender.owner.retained), case.stores[0]._db.execute('SELECT tx FROM state').fetchone(),
        case.sender.ledger.snapshot(case.clock()[1]))
    assert before[:4] == after[:4]
    for field in ('debt_own_us', 'debt_shared_us', 'pending_reservations'):
        assert before[4][field] == after[4][field]
    assert case.sender.flow._in_flight[2].attempts == 1
    assert case.workers[0].pending_physical == tuple(accepted)
    assert case.sender.owner.no_send and case.sender.next_send_us > case.clock()[1]
    assert not case.sender._repair_fill_active
