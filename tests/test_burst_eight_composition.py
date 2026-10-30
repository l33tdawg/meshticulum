"""Negotiated eight-frame source controls; modeled air is not measured goodput."""
from collections import Counter

import pytest

from meshticulum.bench.serial_protocol import Message
from tests.test_burst_runtime import composition
from tests import test_burst_sf5_recovery as model


def test_eight_scalar_acceptances_keep_real_cipher_journal_and_owner_handover(composition):
    case = composition(bytes(range(251)) * 10, train_depth=8)
    assert case.sender.flow.limits.window_frames == 8
    assert case.receiver.flow.limits.max_reassembly_frames == 8
    assert case.sender.owner.contract.limits.window_bytes == 1664
    case.sender.send(case.data)
    peak = 0
    for _ in range(500):
        sender_done = case.sender.step()
        receiver_done = case.receiver.step()
        peak = max(peak, len(case.workers[0].pending_physical))
        if len(case.workers[0].pending_physical) == 8 or case.sender.flow.queued_bytes() == 0:
            case.channels[0].finish_packets()
        if sender_done and receiver_done:
            break
        case.clock.advance(10_000)
    else:
        pytest.fail('eight-frame scalar composition did not finish')
    assert peak == 8
    assert case.receiver.received == case.data
    assert case.sender.flow.retransmits == 0
    assert case.sender.counts['tx_QUEUED'] == case.sender.counts['tx_RF_DONE']
    assert not case.workers[0].pending_physical and not case.channels[0].events
    assert not case.journals[0].snapshot()['fenced']
    # Each admission is still its own scalar command, not a batch handover.
    assert sum(f.type is Message.BURST_DATA for f in case.channels[0].writes) == 14
    assert case.journals[0].snapshot()['attempts'] == 14


@pytest.mark.parametrize('faults', [
    {}, {'drop_ack_seqs': {0: 1}}, {'drop_ack_seqs': {8: 1}},
    {'drop_final_ack': True}, *[{'drop_data': {seq: 1}} for seq in range(8)],
], ids=['clean', 'startup-ack', 'train-ack', 'final-ack',
        *[f'data-{seq}' for seq in range(8)]])
def test_independent_hosts_preserve_authenticated_delivery_after_eight_train_faults(tmp_path, faults):
    results, air = model.exercise(tmp_path, train_depth=8, host_pump=True,
                                  receipt_confirmation=True, **faults)
    model.assert_complete(results, air)
    assert not any(frame.collision for frame in air.frames)
    assert 1 < air.channels[0].peak <= 8
    for result, channel in zip(results, air.channels):
        journal = result['snapshot']['attempt_journal']
        assert journal['recovered_unknown'] == 0
        assert sum(n for _, n in result['settled_states']) == journal['attempts']
        assert channel.attempts == journal['attempts']
    by_sequence = {}
    for frame in air.frames:
        if 'seq' in frame.inner:
            by_sequence.setdefault(frame.inner['seq'], []).append(frame)
    assert set(by_sequence) == set(range(9))
    for attempts in by_sequence.values():
        assert len({frame.packet for frame in attempts}) == len(attempts)
    if not faults:
        assert results[0]['snapshot']['retransmits'] == 0
        assert Counter(frame.inner.get('ack_seq') for frame in air.frames
                       if 'ack_seq' in frame.inner) == {0: 1, 8: 1, 9: 1}


@pytest.mark.parametrize('costs', [(40_000, 10_000), (120_000, 40_000)])
def test_eight_train_slow_scalar_refill_exposes_actual_empty_tail(tmp_path, costs):
    results, air = model.exercise(tmp_path, train_depth=8, host_work_us=costs,
        host_pump=True, receipt_confirmation=True)
    model.assert_complete(results, air)
    assert not any(frame.collision for frame in air.frames)
    data = [frame for frame in air.frames if 'seq' in frame.inner]
    assert len(data) == 9 and results[0]['snapshot']['retransmits'] == 0
    # A negotiated ceiling never pretends eight packets were prefetched. The
    # actual empty native queue still selects the full reverse listening floor.
    assert any(second.start - first.end >= model.REVERSE_LISTEN_US
               for first, second in zip(data[:7], data[1:8]))


@pytest.mark.parametrize('train_depth', [4, 8])
@pytest.mark.parametrize('queued_tail', [False, True])
def test_model_train_bound_keeps_checked_receive_floor_and_underfill(train_depth, queued_tail):
    timeline = model.Timeline()
    air = model.Air(timeline)
    air.channels = [model.RadioChannel(timeline, air, i, train_depth=train_depth)
                    for i in range(2)]
    channel = air.channels[0]
    packet = b'd' * 255
    air.sealed[packet] = dict(seq=0)
    estimate = model.estimated_airtime_us(model.PROFILE, len(packet))
    for request in range(1, 3 if queued_tail else 2):
        channel.events[request] = (1, model.Stage.QUEUED, timeline.now,
            model.hashlib.sha256(packet).digest(), estimate, len(packet),
            timeline.now + 2_000_000, 0, 0, 0)
        channel.queued.append((request, packet))
    channel.train_count = train_depth - 1
    channel.dispatch()
    first_end = air.frames[0].end
    while timeline.events and timeline.events[0][0] <= first_end:
        timeline.now, _, callback = model.heapq.heappop(timeline.events)
        callback()
    assert channel.next_start_us == first_end + model.REVERSE_LISTEN_US
    assert channel.train_count == 0
