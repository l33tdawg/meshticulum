"""Real cipher/store/flow composition; radio time and diagnostic time are modeled."""
import json
from types import SimpleNamespace

import pytest

from meshticulum.bench import host_timing
from meshticulum.bench.host_timing import HostPhaseTimings, Phase
from meshticulum.bench.serial_protocol import Message
from tests.test_burst_runtime import composition, queue_one


def measured(timing, phase):
    return timing.snapshot()['phases'][phase.value]


def store_state(store):
    return store._db.execute('SELECT tx,high,bitmap FROM state').fetchone()


def complete(case, burst):
    case.sender.send(case.data)
    for _ in range(500):
        sender_done = case.sender.step()
        receiver_done = case.receiver.step()
        if burst and (len(case.workers[0].pending_physical) >= 4
                      or case.sender.flow.queued_bytes() == 0):
            case.channels[0].finish_packets()
        if sender_done and receiver_done:
            return
        case.clock.advance(10_000)
    raise AssertionError('source composition did not complete')


@pytest.mark.parametrize('burst', [False, True])
def test_cipher_timings_include_store_and_crypto_without_changing_wire_or_debt(
        composition, monkeypatch, burst):
    observed = []
    for enabled in (False, True):
        case = composition(bytes(range(256)) * 3, burst=burst)
        tick = [0]
        collectors = []
        def forbidden():
            raise AssertionError('disabled profiling sampled a diagnostic clock')
        monkeypatch.setattr(host_timing, 'perf_counter_ns',
                            (lambda: tick[0]) if enabled else forbidden)

        def instrument(target, name, duration):
            original = getattr(target, name)
            def call(*args, **kwargs):
                try:
                    return original(*args, **kwargs)
                finally:
                    tick[0] += duration
            monkeypatch.setattr(target, name, call)

        for endpoint, store in zip((case.sender, case.receiver), case.stores):
            collector = HostPhaseTimings() if enabled else None
            endpoint.host_timing = collector
            collectors.append(collector)
            # Advance only a separate diagnostic clock around the real calls.
            # The authority/radio clock, real encryption and FULL transactions
            # remain unchanged in both arms.
            instrument(store, 'reserve_transmit', 17)
            instrument(store, 'check_live', 7)
            instrument(store, 'record_receive', 23)
            tx = SimpleNamespace(encrypt=endpoint.cipher._tx.encrypt)
            rx = SimpleNamespace(decrypt=endpoint.cipher._rx.decrypt)
            monkeypatch.setattr(endpoint.cipher, '_tx', tx)
            monkeypatch.setattr(endpoint.cipher, '_rx', rx)
            instrument(tx, 'encrypt', 29)
            instrument(rx, 'decrypt', 19)

        complete(case, burst)
        assert bytes(case.receiver.received) == case.data
        assert case.sender.flow.retransmits == 0
        states = [store_state(store) for store in case.stores]
        observed.append((states, [journal.snapshot() for journal in case.journals],
            [[(frame.type, frame.boot, frame.request, frame.body)
              for frame in channel.writes
              if frame.type not in (Message.HELLO, Message.BURST_CAPABILITIES)]
             for channel in case.channels],
            [dict(endpoint.counts) for endpoint in (case.sender, case.receiver)]))
        for endpoint, collector, state in zip(
                (case.sender, case.receiver), collectors, states):
            if enabled:
                sealed = measured(collector, Phase.CIPHER_SEAL)
                opened = measured(collector, Phase.CIPHER_OPEN)
                assert sealed['count'] == state[0] > 0
                assert opened['count'] == state[1] > 0
                assert sealed['total_ns'] == sealed['count'] * (17 + 29)
                assert opened['total_ns'] == opened['count'] * (7 + 19 + 23)
                assert sealed['min_ns'] == sealed['max_ns'] == 46
                assert opened['min_ns'] == opened['max_ns'] == 49
                assert endpoint.snapshot()['host_phase_timings'] == collector.snapshot()
            else:
                assert 'host_phase_timings' not in endpoint.snapshot()
    assert observed[0] == observed[1]


def test_open_counts_authentication_and_replay_refusals_without_plaintext_or_replay_change(
        composition):
    case = composition(b'one authenticated application payload')
    queue_one(case)
    packet, = case.channels[0].packets.values()
    timing = case.receiver.host_timing = HostPhaseTimings()
    case.channels[0].finish_packets()
    case.channels[0].deliver(packet)
    case.channels[0].deliver(packet[:-1] + bytes([packet[-1] ^ 1]))
    case.receiver.step()
    assert bytes(case.receiver.received) == case.data
    assert case.receiver.counts['rx_DATA'] == 1
    assert case.receiver.counts['rx_refused'] == 2
    assert store_state(case.stores[1])[1] == 1
    assert measured(timing, Phase.CIPHER_OPEN)['count'] == 3
    report = json.dumps(timing.snapshot())
    assert 'authentication_failed' not in report
    assert 'replayed_or_too_old' not in report
    assert case.data.decode() not in report
    case.sender.step()


@pytest.mark.parametrize('which', ['seal', 'open'])
@pytest.mark.parametrize('failure_type', [OSError, KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('diagnostic_exit_failure', [False, True])
def test_cipher_failure_after_durable_mutation_keeps_exact_exception_and_no_new_delivery(
        composition, monkeypatch, which, failure_type, diagnostic_exit_failure):
    case = composition(b'failure after durable state mutation')
    failure = failure_type('original cipher operation failure')
    endpoint = case.sender if which == 'seal' else case.receiver
    timing = endpoint.host_timing = HostPhaseTimings()
    label = Phase.CIPHER_SEAL if which == 'seal' else Phase.CIPHER_OPEN
    if diagnostic_exit_failure:
        original_finish = HostPhaseTimings._finish
        def finish(self, phase, start):
            if phase is label:
                raise KeyboardInterrupt('diagnostic exit failure')
            return original_finish(self, phase, start)
        monkeypatch.setattr(HostPhaseTimings, '_finish', finish)

    if which == 'seal':
        # Encryption fails only after the real nonce transaction commits.
        def encrypt(*args, **kwargs):
            raise failure
        monkeypatch.setattr(case.sender.cipher, '_tx', SimpleNamespace(encrypt=encrypt))
        errors = []
        carrier = case.sender.link.carrier
        def capture(frame):
            try:
                return carrier(frame)
            except BaseException as error:
                errors.append(error)
                raise
        monkeypatch.setattr(case.sender.link, 'carrier', capture)
        case.sender.send(case.data)
        case.receiver.step()
        outward = None
        try:
            case.sender.step()
        except BaseException as error:
            outward = error
        assert errors == [failure]
        if not isinstance(failure, Exception):
            assert outward is failure
        assert store_state(case.stores[0])[0] == 1  # nonce stays burned
        assert case.journals[0].snapshot()['attempts'] == 0
        assert not any(frame.type is Message.BURST_DATA for frame in case.channels[0].writes)
    else:
        queue_one(case)
        original_record = case.stores[1].record_receive
        def record_receive(*args, **kwargs):
            original_record(*args, **kwargs)
            raise failure
        monkeypatch.setattr(case.stores[1], 'record_receive', record_receive)
        case.channels[0].finish_packets()
        before_attempts = case.journals[1].snapshot()['attempts']
        with pytest.raises(failure_type) as caught:
            case.receiver.step()
        assert caught.value is failure
        assert store_state(case.stores[1])[1] == 1  # replay admission stays durable
        assert not case.receiver.received
        assert case.journals[1].snapshot()['attempts'] == before_attempts
    row = measured(timing, label)
    assert row['count'] == (0 if diagnostic_exit_failure else 1)
    assert row['dropped'] == int(diagnostic_exit_failure)
    assert 'original cipher operation failure' not in json.dumps(timing.snapshot())


def test_revoked_seal_is_measured_without_burning_nonce_or_physical_attempt(
        composition, monkeypatch):
    case = composition()
    timing = case.sender.host_timing = HostPhaseTimings()
    reserve = case.stores[0].reserve_transmit
    monkeypatch.setattr(case.stores[0], 'reserve_transmit',
                        lambda **kwargs: reserve(authorize=lambda: False))
    case.sender.send(case.data)
    case.receiver.step()
    try:
        case.sender.step()
    except ValueError:
        pass  # Existing scheduler/flow failure policy remains authoritative.
    assert measured(timing, Phase.CIPHER_SEAL)['count'] == 1
    assert store_state(case.stores[0])[0] == 0
    assert case.journals[0].snapshot()['attempts'] == 0
    assert not any(frame.type is Message.BURST_DATA for frame in case.channels[0].writes)
