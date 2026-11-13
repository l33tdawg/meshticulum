"""Modeled SPI parity only; these timings are not physical RF measurements."""
import json
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.hat_worker import HATWorker, journal_binding
from meshticulum.bench.host_timing import HostPhaseTimings
from meshticulum.bench import host_timing
from tests.test_meshticulum_bench_sx1262 import Chip, PROFILE


def exercise(path, timing):
    chip=Chip();context=b'c'*32
    journal=AttemptJournal(path,binding=journal_binding(context,chip.boot_id,PROFILE),
        max_attempts=20,max_airtime_us=10000000,create=True,host_timing=timing)
    worker=HATWorker(chip,journal=journal,profile=PROFILE,context_digest=context,
        authorize=lambda:True,authority_deadline=200,antenna_confirmed=True,
        monotonic=lambda:chip.time,time_basis='model-monotonic')
    deadline=worker.lease_deadline
    try:
        assert worker.receive(timeout=0) is None
        chip.flags=2
        received=worker.receive(timeout=0)
        assert received[1].packet==chip.rx and worker.radio.receiving
        sent=worker.transmit(b'ack bytes')
        assert sent.outcome=='RF_DONE' and worker.radio.receiving
        assert worker.lease_deadline==deadline
        return chip.commands,chip.switches,chip.resets,journal.snapshot(),sent.outcome,deadline
    finally:
        worker.disarm();journal.close()


def test_optional_phases_preserve_spi_order_debt_and_original_deadline(tmp_path):
    plain=exercise(tmp_path/'plain.db',None)
    timing=HostPhaseTimings();measured=exercise(tmp_path/'measured.db',timing)
    assert measured==plain
    phases=timing.snapshot()['phases']
    assert phases['hat_rx_poll']['count']==2
    assert phases['hat_rx_drain']['count']==1
    assert phases['hat_rx_packet_read']['count']==1
    assert phases['hat_rx_rearm']['count']==3
    assert phases['hat_tx_to_rx_restore']['count']==1
    assert len(json.dumps(timing.snapshot()))<10000


def test_disabled_hat_phases_do_not_read_diagnostic_clock(tmp_path,monkeypatch):
    def forbidden():raise AssertionError('disabled diagnostic clock')
    monkeypatch.setattr(host_timing,'perf_counter_ns',forbidden)
    exercise(tmp_path/'disabled.db',None)


def test_failed_diagnostic_clock_does_not_change_spi_or_paid_outcome(tmp_path,monkeypatch):
    plain=exercise(tmp_path/'plain.db',None)
    def failed():raise RuntimeError('clock unavailable')
    monkeypatch.setattr(host_timing,'perf_counter_ns',failed)
    timing=HostPhaseTimings();measured=exercise(tmp_path/'measured.db',timing)
    assert measured==plain
    phases=timing.snapshot()['phases']
    assert phases['hat_rx_poll']['dropped']>=2
    assert phases['hat_rx_rearm']['dropped']>=3
