"""Real host integration for train12's saturated-window stall repair."""
from dataclasses import replace

from tests.test_burst_runtime import composition


def stalled_case(composition):
    case = composition(b'x' * (24 * 193), train_depth=8, train12=True)
    for endpoint in (case.sender, case.receiver):
        endpoint.enable_receipt_confirmation(
            b'c' * 32,
            receiver_ready=None if endpoint.sends else lambda: False,
        )
    case.sender.flow.limits = replace(
        case.sender.flow.limits,
        window_frames=6,
    )
    case.sender.send(case.data)
    original = case.channels[0].deliver
    captured = {}

    def drop(packet):
        return None

    case.channels[0].deliver = drop
    for _ in range(500):
        case.receiver.step()
        case.sender.step()
        pending = case.workers[0].pending_physical
        if pending:
            for result in pending:
                request = result if type(result) is int else result.request
                packet = case.channels[0].packets.get(request)
                metadata = case.sender._physical_data.get(request)
                if packet is not None and metadata is not None and metadata['domain'] is not None:
                    captured[metadata['domain'][-1]] = packet
            case.channels[0].finish_packets()
        if case.sender.flow.in_flight() == 6 and not case.workers[0].pending_physical:
            break
        case.clock.advance(10_000)
    else:
        raise AssertionError('the sender did not fill its 6-frame logical window')
    assert case.sender.flow.queued_bytes() > 0
    assert captured
    later_seq = max(captured)
    assert later_seq > 0
    case.original_delivery = original
    original(captured[later_seq])
    return case


def test_train12_saturated_window_repairs_one_proven_hole_before_rto(composition):
    case = stalled_case(composition)
    sender = case.sender
    for _ in range(300):
        case.receiver.step()
        sender.step()
        if sender.counts['stall_repair_turn']:
            break
        if case.workers[0].pending_physical:
            case.channels[0].finish_packets()
        case.clock.advance(10_000)
    else:
        raise AssertionError((sender.snapshot(), case.receiver.snapshot()))

    assert sender.counts['stall_repair_turn'] == 1
    assert sender.flow.fast_retry_offers >= 1
    assert sender.flow.fast_retransmits >= 1
    assert sender.flow.retransmits >= 1
    assert sender._repair_train_active
    assert sender._repair_train_mode == 'stall'
    assert any(p.attempts > 1 for p in sender.flow._in_flight.values())
    assert case.journals[0].snapshot()['attempts'] == sender.counts['tx_QUEUED']
    ack_event = next(
        event for event in reversed(sender.events)
        if event['event'] == 'rx'
        and event['outer_kind'] == 'ACK'
        and event['ack_sack']
    )
    repair_event = next(
        event for event in reversed(sender.events)
        if event['event'] == 'tx_queued'
        and event['mono_us'] > ack_event['mono_us']
    )
    assert repair_event['mono_us'] - ack_event['mono_us'] <= 250_000


def test_stall_train_keeps_each_repair_separately_paid(composition):
    case = stalled_case(composition)
    for _ in range(300):
        case.receiver.step()
        case.sender.step()
        if case.sender.counts['stall_repair_turn']:
            break
        if case.workers[0].pending_physical:
            case.channels[0].finish_packets()
        case.clock.advance(10_000)
    else:
        raise AssertionError('stall repair did not start')
    sender = case.sender
    first_attempts = case.journals[0].snapshot()['attempts']
    first_packets = {
        event['packet_sha256'] for event in sender.events
        if event['event'] == 'tx_queued'
    }
    case.channels[0].finish_packets()
    for _ in range(20):
        case.receiver.step()
        sender.step()
        if case.workers[0].pending_physical:
            break
        case.clock.advance(10_000)
    else:
        raise AssertionError('the bounded stall train did not offer another hole')
    assert sender.flow.fast_retransmits >= 2
    assert case.journals[0].snapshot()['attempts'] == sender.counts['tx_QUEUED']
    assert case.journals[0].snapshot()['attempts'] > first_attempts
    packets = {
        event['packet_sha256'] for event in sender.events
        if event['event'] == 'tx_queued'
    }
    assert len(packets) == len(first_packets) + 1
    assert case.stores[0]._db.execute('SELECT tx FROM state').fetchone()[0] == sender.owner.rf_attempts
    assert not case.journals[0].snapshot()['fenced']
