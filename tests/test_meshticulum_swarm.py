# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Local policy/adapter controls. Model transports here are explicitly not RF."""
from pathlib import Path
import sys
import threading
import time

import pytest

from meshticulum.resources import swarm as ms


def source(name, rate=1000, *, medium='model', resource=None, **kwargs):
    route = ms.Route(name, 1, (ms.Hop('receiver', name, medium, resource or name, rate),))
    return ms.Source(route, lambda _path, _limit, _deadline: b'', **kwargs)


def piece(number, size=1000):
    return {'sha256': f'{number:064x}', 'bytes': size}


def availability(sources, pieces):
    return {s.name: {p['sha256'] for p in pieces} for s in sources}


def test_transport_name_never_overrides_measured_goodput():
    slow_wifi, fast_radio = source('wifi', 1000000), source('radio', 1000)
    slow_wifi.observe_verified(1000, 5, 10)
    fast_radio.observe_verified(1000, 1, 10)
    paths = [slow_wifi, fast_radio]
    item = piece(0)
    assert ms.choose_source(paths, item, availability(paths, [item]), {}, 11) is fast_radio
    # A stale sample no longer masquerades as current measured performance.
    assert ms.choose_source(paths, item, availability(paths, [item]), {}, 100) is slow_wifi
    assert slow_wifi.snapshot(100)['basis'] == 'local bootstrap estimate'


def test_wait_for_busy_fast_path_instead_of_creating_slow_tail():
    fast, slow = source('fast', 1000000), source('slow', 1000)
    paths, item = [fast, slow], piece(0)
    assert ms.choose_source(paths, item, availability(paths, [item]),
                            {fast.name: (fast, 10.1)}, 10) is fast


def test_full_route_cost_includes_slow_hop_and_rejects_cycles():
    mixed = ms.Source(ms.Route('mixed', 1, (
        ms.Hop('c', 'a', 'lora-model', 'rf-ca', 1000),
        ms.Hop('a', 'b', 'ip-model', 'lan-ab', 1000000))), lambda *args: b'')
    direct = source('direct', 10000)
    item = piece(1, 10000)
    assert 10 < mixed.seconds(10000, 0) < 11
    assert ms.choose_source([mixed, direct], item, availability([mixed, direct], [item]), {}, 0) is direct
    for hops in ((ms.Hop('a', 'b', 'x', 'ab', 1), ms.Hop('b', 'a', 'y', 'ba', 1)),
                 (ms.Hop('a', 'b', 'x', 'ab', 1), ms.Hop('c', 'd', 'y', 'cd', 1))):
        with pytest.raises(ValueError, match='route gap or loop'):
            ms.Route('bad', 1, hops)


def test_resource_contention_is_shared_across_distinct_path_names():
    one, two, independent = source('one', 10000, resource='radio-a'), source(
        'two', 10000, resource='radio-a'), source('three', 5000)
    item = piece(0)
    paths = [one, two, independent]
    assert ms.choose_source(paths, item, availability(paths, [item]), {'one': (one, 12)}, 10) is independent


def test_route_generation_change_requires_fresh_measurements():
    path = source('one')
    path.observe_verified(1000, .001, 10)
    path.route = ms.Route(path.name, 2, path.route.hops)
    with pytest.raises(ValueError, match='fresh source'):
        path.seconds(10, 11)
    fresh = ms.Source(path.route, path.reader)
    assert fresh.snapshot(11)['verified_samples'] == 0


def test_disjoint_paths_download_in_parallel_without_duplicate_chunks():
    paths = [source('a', max_batch=1), source('b', max_batch=1)]
    pieces = [piece(0), piece(1)]
    have = {s.name: {p['sha256']} for s, p in zip(paths, pieces)}
    barrier = threading.Barrier(2)
    seen = []
    def load(path, batch, deadline):
        assert len(batch) == 1 and deadline > time.monotonic()
        barrier.wait(timeout=2)
        return batch
    ms.download(paths, pieces, have, load, lambda s, batch: seen.extend(batch),
                deadline=time.monotonic() + 5)
    assert sorted(seen, key=lambda p: p['sha256']) == pieces
    assert all(s.snapshot(time.monotonic())['verified_samples'] == 1 for s in paths)


def test_shared_radio_paths_never_execute_simultaneously():
    paths = [source('a', resource='one-radio', max_batch=1), source('b', resource='one-radio', max_batch=1)]
    pieces = [piece(0), piece(1)]
    have = {s.name: {p['sha256']} for s, p in zip(paths, pieces)}
    in_flight, maximum = 0, 0
    lock = threading.Lock()
    seen = []
    def load(path, batch, deadline):
        nonlocal in_flight, maximum
        with lock:
            in_flight += 1
            maximum = max(maximum, in_flight)
        time.sleep(.01)
        with lock:
            in_flight -= 1
        return batch
    ms.download(paths, pieces, have, load, lambda s, batch: seen.extend(batch), deadline=time.monotonic() + 5)
    assert len(seen) == 2 and maximum == 1


def test_rarest_piece_goes_first_with_bounded_request_size():
    paths = [source('a', 100000, max_batch=1), source('b', max_batch=1)]
    pieces = [piece(0), piece(1), piece(2)]
    have = availability(paths, pieces)
    have['b'].remove(pieces[2]['sha256'])
    seen = []
    ms.download(paths, pieces, have, lambda s, batch, deadline: batch,
                lambda s, batch: seen.extend(batch), deadline=time.monotonic() + 5, max_workers=1)
    assert seen[0] == pieces[2]


def test_failed_source_is_attempted_once_and_never_trains_estimate():
    paths = [source('a', 1000000), source('b')]
    pieces = [piece(i) for i in range(4)]
    calls, committed = [], []
    def load(path, batch, deadline):
        calls.append(path.name)
        if path.name == 'a':
            raise ValueError('corrupt chunk')
        return batch
    ms.download(paths, pieces, availability(paths, pieces), load,
                lambda s, batch: committed.extend(batch), deadline=time.monotonic() + 5)
    assert calls.count('a') == 1 and len(committed) == 4
    assert paths[0].snapshot(time.monotonic())['verified_samples'] == 0


def test_storage_failure_propagates_without_retrying_other_peer():
    paths, pieces = [source('a'), source('b')], [piece(0)]
    calls = []
    def commit(path, batch):
        calls.append(path.name)
        raise OSError('disk full')
    with pytest.raises(OSError, match='disk full'):
        ms.download(paths, pieces, availability(paths, pieces), lambda s, batch, deadline: batch,
                    commit, deadline=time.monotonic() + 5)
    assert len(calls) == 1
    assert all(p._samples == 0 for p in paths)


@pytest.mark.parametrize('expired', [False, True])
def test_cancel_or_expiry_before_dispatch_never_calls_reader(expired):
    paths, pieces = [source('a')], [piece(0)]
    calls = []
    with pytest.raises(TimeoutError if expired else InterruptedError):
        ms.download(paths, pieces, availability(paths, pieces), lambda *args: calls.append('read'),
                    lambda *args: calls.append('commit'), deadline=time.monotonic() + (-1 if expired else 5),
                    cancelled=lambda: not expired)
    assert calls == []


def test_late_or_oversized_reader_result_is_rejected():
    path = source('a')
    path.reader = lambda *args: b'x' * 11
    with pytest.raises(ValueError, match='oversized'):
        path.read('/resource', 10, time.monotonic() + 1)
    with pytest.raises(TimeoutError):
        path.read('/resource', 10, time.monotonic() - 1)


def test_fixed_request_overhead_does_not_create_a_false_fast_source(monkeypatch):
    # Virtual transport costs, not a host timing or physical throughput claim.
    # Both share a resource so wall-clock thread overlap cannot affect the model.
    now = [100.0]
    monkeypatch.setattr(ms.time, 'monotonic', lambda: now[0])
    paths = [source('a', 65536, resource='shared', max_batch=32),
             source('b', 65536, resource='shared', max_batch=32)]
    # Bootstrap underestimates both. B really is faster, despite A being tried first.
    rate = {'a': 100000, 'b': 1000000}
    pieces = [piece(i, 400) for i in range(512)]
    calls, committed = [], []
    def load(path, batch, deadline):
        elapsed = .05 + sum(e['bytes'] for e in batch) / rate[path.name]
        now[0] += elapsed
        calls.append((path.name, len(batch)))
        return batch
    ms.download(paths, pieces, availability(paths, pieces), load,
                lambda s, batch: committed.extend(batch), deadline=1000, max_workers=1)
    assert len(committed) == 512
    assert calls[0] == ('a', 32) and calls[1] == ('b', 32)
    assert all(name == 'b' for name, _ in calls[2:])
    assert paths[1].seconds(12800, now[0]) < paths[0].seconds(12800, now[0])


def test_optional_calibration_does_not_create_a_slow_radio_tail(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(ms.time, 'monotonic', lambda: now[0])
    paths = [source('fast', 1000000, max_batch=32), source('radio', 1000, max_batch=1)]
    pieces, calls = [piece(i, 400) for i in range(64)], []
    def load(path, batch, deadline):
        calls.append(path.name)
        now[0] += .01
        return batch
    ms.download(paths, pieces, availability(paths, pieces), load, lambda *args: None,
                deadline=1000, max_workers=1)
    assert calls == ['fast', 'fast']
    assert paths[1]._samples == 0
