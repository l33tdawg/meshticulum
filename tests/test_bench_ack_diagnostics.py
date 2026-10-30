"""Authenticated ACK metadata controls; modeled radios, never collision evidence."""
from meshticulum.bench.wire import FrameKind
from meshticulum.flow.flow import build_ack_frame
from tests.test_burst_runtime import composition, queue_one  # noqa: F401


def offer_ack(case, *, seq=0, credit=4, sack=0, revision=1):
    flow = case.sender.flow
    inner = build_ack_frame(flow_id=flow.flow_id, generation=flow.generation,
        ack_seq=seq, credit=credit, sack=sack, grant_revision=revision, limits=flow.limits)
    packet = case.receiver.cipher.seal(inner, FrameKind.ACK,
                                      authorize=case.receiver.authority.live)
    case.channels[1].deliver(packet)
    case.sender.step()
    return [row for row in case.sender.events if row.get('event') == 'rx'
            and row.get('outer_kind') == 'ACK'][-1]


def test_authenticated_ack_effects_distinguish_progress_from_repeat_and_refusal(composition):
    case = composition()
    rows = [offer_ack(case), offer_ack(case), offer_ack(case, revision=2),
            offer_ack(case, revision=1), offer_ack(case, revision=2, credit=3),
            offer_ack(case, revision=3, seq=1)]
    assert [row['ack_effect'] for row in rows] == [
        'startup', 'unchanged', 'fresh_credit', 'ignored', 'ignored', 'ignored']
    assert all(row['acked_frames_delta'] == 0 for row in rows)
    counts = case.sender.snapshot()['counts']
    assert counts['rx_ACK'] == 6
    assert {key: counts[key] for key in ('rx_ACK_startup', 'rx_ACK_unchanged',
        'rx_ACK_fresh_credit', 'rx_ACK_ignored')} == dict(rx_ACK_startup=1,
            rx_ACK_unchanged=1, rx_ACK_fresh_credit=1, rx_ACK_ignored=3)
    for row in rows:
        assert set(key for key in row if key.startswith('ack_')) == {
            'ack_effect', 'ack_seq', 'ack_sack', 'ack_credit', 'ack_revision'}
        assert all(type(row[key]) is int for key in ('ack_seq', 'ack_sack',
                                                    'ack_credit', 'ack_revision'))


def test_partial_ack_reports_exact_retirement_while_physical_data_remain(composition):
    case = composition(b'x' * 800)
    case.sender.send(case.data)
    case.receiver.step()
    for _ in range(20):
        case.sender.step()
        if len(case.workers[0].pending_physical) == 4:
            break
        case.clock.advance(1000)
    assert len(case.workers[0].pending_physical) == 4
    row = offer_ack(case, seq=1, revision=2)
    assert row['ack_effect'] == 'progress' and row['acked_frames_delta'] == 1
    assert case.sender.snapshot()['counts']['rx_ACK_progress'] == 1
    assert case.workers[0].pending_physical and not case.sender._completion_ready()


def test_ack_transmit_metadata_describes_committed_snapshot(composition):
    case = composition()
    queue_one(case)
    case.channels[0].finish_packets()
    for _ in range(20):
        case.receiver.step()
        case.sender.step()
        if case.sender.flow.complete:
            break
        case.clock.advance(10_000)
    assert case.sender.flow.complete
    rows = [row for row in case.receiver.events if row.get('event') == 'tx'
            and row.get('outer_kind') == 'ACK' and row.get('outcome') == 'RF_DONE']
    assert rows[0]['ack_snapshot'] == 'startup' and rows[-1]['ack_snapshot'] == 'new'
    assert rows[0]['ack_wait_reason'] == 'startup'
    assert rows[-1]['ack_wait_reason'] in ('complete', 'full_train', 'quiet')
    assert rows[-1]['ack_burst_received'] == 1
    counts = case.receiver.snapshot()['counts']
    for kind in ('startup', 'new', 'unchanged'):
        assert counts.get('tx_ACK_RF_DONE_' + kind, 0) == sum(row['ack_snapshot'] == kind for row in rows)
    assert len(case.receiver.events) <= case.receiver.events.maxlen == 512


def test_startup_reannouncement_is_an_unchanged_transmitted_snapshot(composition):
    case = composition()
    case.receiver.step()
    case.clock.advance(round(case.receiver.flow.limits.persist_interval_s * 1_000_000) + 1)
    case.receiver.step()
    rows = [row for row in case.receiver.events if row.get('event') == 'tx'
            and row.get('outer_kind') == 'ACK']
    assert [row['ack_snapshot'] for row in rows] == ['startup', 'unchanged']
    assert all(row['ack_wait_reason'] == 'startup' for row in rows)
    assert rows[0]['ack_revision'] == rows[1]['ack_revision']
    assert case.receiver.snapshot()['counts']['tx_ACK_RF_DONE_unchanged'] == 1
