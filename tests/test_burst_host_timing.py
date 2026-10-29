"""Host phase diagnostics preserve wire, debt and ambiguous-write behavior."""
import json

import pytest

from meshticulum.bench import host_timing
from meshticulum.bench.host_timing import HostPhaseTimings, Phase
from meshticulum.bench.serial_protocol import Decoder, Message
from meshticulum.bench.tx_burst import Stage
from tests.test_burst_worker import setup
from tests.test_burst_runtime import composition


def finish(worker, channel, request):
    channel.event(request, Stage.STARTED)
    channel.event(request, Stage.RF_DONE)
    results = worker.poll_physical()
    assert [value.outcome for value in results] == ['RF_DONE']
    assert worker.assert_drained()


def test_enabled_phases_follow_durable_queue_and_release(setup):
    worker, channel, journal, _ = setup()
    timing = HostPhaseTimings()
    worker.host_timing = journal.host_timing = timing
    result = worker.queue_data(b'synthetic encrypted bytes', logical_id=b'l' * 32)
    assert result.outcome == 'QUEUED'
    assert journal.snapshot()['attempts'] == 1
    finish(worker, channel, result.request)
    assert not worker.pending_physical
    assert journal.queued_records()[0]['released']
    report = timing.snapshot()
    for label in (Phase.QUEUE_CALL, Phase.QUEUE_PUMP, Phase.QUEUE_CLOCK,
                  Phase.QUEUE_RESERVE, Phase.QUEUE_SERIAL_WRITE,
                  Phase.QUEUE_ADMISSION_WAIT, Phase.QUEUE_TERMINAL_SETTLE,
                  Phase.RELEASE_REQUEST, Phase.RELEASE_ROUNDTRIP, Phase.RELEASE_COMMIT):
        assert report['phases'][label.value]['count'] == 1
    assert report['phases'][Phase.JOURNAL_TRANSACTION.value]['count'] >= 4
    assert 'synthetic encrypted bytes' not in json.dumps(report)
    assert str(result.request) not in json.dumps(report)


def test_disabled_timing_reads_no_perf_clock(setup, monkeypatch):
    def forbidden():
        raise AssertionError('disabled profiling read its clock')
    monkeypatch.setattr(host_timing, 'perf_counter_ns', forbidden)
    worker, channel, journal, _ = setup()
    result = worker.queue_data(b'packet', logical_id=b'l' * 32)
    assert result.outcome == 'QUEUED'
    finish(worker, channel, result.request)
    assert journal.snapshot()['attempts'] == 1


def test_diagnostic_clock_failure_cannot_fence_or_discard_completion(setup, monkeypatch):
    def broken():
        raise OSError('private clock diagnostic detail')
    monkeypatch.setattr(host_timing, 'perf_counter_ns', broken)
    worker, channel, journal, _ = setup()
    timing = HostPhaseTimings()
    worker.host_timing = journal.host_timing = timing
    result = worker.queue_data(b'packet', logical_id=b'l' * 32)
    assert result.outcome == 'QUEUED'
    finish(worker, channel, result.request)
    assert not worker.fenced and not journal.snapshot()['fenced']
    report = timing.snapshot()
    assert report['phases'][Phase.QUEUE_CALL.value]['dropped'] == 1
    assert all(row['count'] == 0 for row in report['phases'].values())
    assert 'private clock' not in json.dumps(report)


@pytest.mark.parametrize('enabled', [False, True])
def test_partial_write_stays_unknown_and_charged_with_or_without_timing(setup, enabled):
    worker, channel, journal, _ = setup('partial_write')
    if enabled:
        worker.host_timing = journal.host_timing = HostPhaseTimings()
    result = worker.queue_data(b'packet', logical_id=b'l' * 32)
    assert result.outcome == 'UNKNOWN'
    assert worker.fenced and journal.snapshot()['fenced']
    assert journal.snapshot()['attempts'] == 1
    assert journal.queued_records()[0]['state'] == 'UNKNOWN'
    assert journal.snapshot()['reserved_estimated_airtime_us'] == result.reserved_estimated_us
    if enabled:
        assert worker.host_timing.snapshot()['phases'][Phase.QUEUE_SERIAL_WRITE.value]['count'] == 1


def test_profiling_cancellation_after_write_keeps_original_cleanup(setup, monkeypatch):
    worker, channel, journal, _ = setup()
    timing = HostPhaseTimings()
    worker.host_timing = journal.host_timing = timing
    original = channel.write
    def interrupted(raw, deadline):
        original(raw, deadline)
        frame, = Decoder().feed(raw)
        if frame.type is Message.BURST_DATA:
            raise KeyboardInterrupt('operator stop')
    monkeypatch.setattr(channel, 'write', interrupted)
    with pytest.raises(KeyboardInterrupt, match='operator stop'):
        worker.queue_data(b'packet', logical_id=b'l' * 32)
    assert worker.fenced and journal.snapshot()['fenced']
    assert journal.queued_records()[0]['state'] == 'UNKNOWN'
    assert journal.snapshot()['attempts'] == 1


@pytest.mark.parametrize('fault', [None, 'refused', 'terminal_first', 'lost_ack', 'partial_write'])
def test_profiling_preserves_exact_wire_and_debt_for_success_and_failure(setup, fault):
    observed = []
    for enabled in (False, True):
        worker, channel, journal, _ = setup(fault)
        if enabled:
            worker.host_timing = journal.host_timing = HostPhaseTimings()
        result = worker.queue_data(b'packet', logical_id=b'l' * 32)
        if result.outcome == 'QUEUED':
            if fault != 'terminal_first':
                channel.event(result.request, Stage.STARTED)
                channel.event(result.request, Stage.RF_DONE)
            worker.poll_physical()
            assert worker.assert_drained()
        observed.append((result, journal.snapshot(), journal.queued_records(),
            [(frame.type, frame.boot, frame.request, frame.body) for frame in channel.writes
             if frame.type not in (Message.HELLO, Message.BURST_CAPABILITIES)],
            worker.physical_snapshot(), worker.fenced))
    assert observed[0] == observed[1]


@pytest.mark.parametrize('enabled', [False, True])
def test_real_crypto_flow_composition_finishes_with_identical_charged_work(composition, enabled):
    case = composition(bytes(range(256)) * 5)
    if enabled:
        for endpoint, worker, journal in zip((case.sender, case.receiver), case.workers, case.journals):
            endpoint.host_timing = worker.host_timing = journal.host_timing = HostPhaseTimings()
    case.sender.send(case.data)
    for _ in range(500):
        sender_done = case.sender.step()
        receiver_done = case.receiver.step()
        if len(case.workers[0].pending_physical) >= 4 or case.sender.flow.queued_bytes() == 0:
            case.channels[0].finish_packets()
        if sender_done and receiver_done:
            break
        case.clock.advance(10_000)
    else:
        raise AssertionError('modeled transfer did not complete')
    assert bytes(case.receiver.received) == case.data
    assert case.sender.counts['tx_QUEUED'] == case.sender.counts['tx_RF_DONE'] == 7
    assert case.sender.flow.retransmits == 0
    assert not case.workers[0].pending_physical
    assert not case.journals[0].snapshot()['fenced']
    snapshot = case.sender.snapshot()
    if enabled:
        phases = snapshot['host_phase_timings']['phases']
        assert phases[Phase.FLOW_STEP.value]['count'] > 0
        assert phases[Phase.FLOW_ACCOUNTING.value]['count'] > 0
        assert phases[Phase.FLOW_DISPATCH.value]['count'] > 0
        assert phases[Phase.FLOW_PUMP.value]['count'] > 0
        assert phases[Phase.QUEUE_CALL.value]['count'] == 7
    else:
        assert 'host_phase_timings' not in snapshot


def test_enabled_cleanup_after_expiry_never_refunds_debt(setup):
    worker, channel, journal, _ = setup()
    worker.host_timing = journal.host_timing = HostPhaseTimings()
    result = worker.queue_data(b'packet', logical_id=b'l' * 32)
    before = journal.snapshot()['reserved_estimated_airtime_us']
    channel.event(result.request, Stage.STARTED)
    channel.event(result.request, Stage.RF_DONE)
    channel.time = worker.deadline + 1
    assert [item.outcome for item in worker.drain_physical()] == ['RF_DONE']
    assert journal.snapshot()['reserved_estimated_airtime_us'] == before
    assert journal.queued_records()[0]['released']
