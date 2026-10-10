"""Transport-neutral, bounded resource scheduling for the Meshticulum lab.

Routes are local configuration, not authenticated discovery. A reader must be
an already admitted adapter: read(resource, byte_limit, absolute_deadline).
It owns its framing, encryption, cancellation, MTU and medium/airtime budget.
This layer never opens a radio, moves a live wire session, or refunds airtime.
Only verified resource bytes train the local path estimator. No advertised
rate, transport name, or successful socket write is delivery evidence.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import math
import time


def require(condition, message):
    if not condition:
        raise ValueError(message)


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class Hop:
    source: str
    destination: str
    medium: str
    resource: str
    bootstrap_bytes_per_second: float
    bootstrap_latency_seconds: float = .01

    def __post_init__(self):
        require(all(type(x) is str and 0 < len(x) <= 256 for x in
                    (self.source, self.destination, self.medium, self.resource)), 'invalid hop')
        require(self.source != self.destination, 'self hop')
        require(positive(self.bootstrap_bytes_per_second) and
                positive(self.bootstrap_latency_seconds), 'invalid bootstrap estimate')


@dataclass(frozen=True)
class Route:
    """An explicit local route. Resource IDs identify shared bottlenecks.

Adjacent hops may use completely different media. Sum store/forward costs,
including each hop's latency, instead of assuming the last hop's link speed.
Admission, peer identity and route expiry belong to the reader's owner; this
description does not confer those authorities. Change generation on rebinding.
"""
    path_id: str
    generation: int
    hops: tuple[Hop, ...]

    def __post_init__(self):
        require(type(self.path_id) is str and 0 < len(self.path_id) <= 256, 'invalid path ID')
        require(type(self.generation) is int and self.generation > 0, 'invalid route generation')
        require(type(self.hops) is tuple and 1 <= len(self.hops) <= 8 and
                all(type(h) is Hop for h in self.hops), 'invalid route hops')
        nodes = [self.hops[0].source]
        for hop in self.hops:
            require(hop.source == nodes[-1] and hop.destination not in nodes, 'route gap or loop')
            nodes.append(hop.destination)

    @property
    def resources(self):
        return frozenset(h.resource for h in self.hops)

    def bootstrap_seconds(self, byte_count):
        return sum(h.bootstrap_latency_seconds + byte_count / h.bootstrap_bytes_per_second
                   for h in self.hops)


@dataclass
class Source:
    route: Route
    reader: object
    request_timeout: float = 5.0
    max_batch: int = 32
    sample_ttl: float = 60.0
    # Measurements never cross a Source/route generation or survive restart.
    _seconds_per_byte: float | None = field(default=None, init=False)
    _sample_at: float | None = field(default=None, init=False)
    _samples: int = field(default=0, init=False)

    def __post_init__(self):
        require(type(self.route) is Route and callable(self.reader), 'invalid source')
        require(positive(self.request_timeout) and positive(self.sample_ttl), 'invalid timeout')
        require(type(self.max_batch) is int and 1 <= self.max_batch <= 32, 'invalid batch bound')
        self._binding = self.route

    @property
    def name(self):
        return self.route.path_id

    def read(self, resource, limit, deadline):
        require(self.route == self._binding, 'source route changed; create a fresh source')
        require(type(limit) is int and limit > 0 and positive(deadline), 'invalid read bound')
        if time.monotonic() >= deadline:
            raise TimeoutError('resource deadline expired')
        raw = self.reader(resource, limit, deadline)
        if time.monotonic() >= deadline:
            raise TimeoutError('resource deadline expired')
        require(type(raw) is bytes and len(raw) <= limit, 'invalid or oversized resource')
        return raw

    def measured(self, now):
        return self._sample_at is not None and 0 <= now - self._sample_at < self.sample_ttl

    def seconds(self, byte_count, now):
        require(self.route == self._binding, 'source route changed; create a fresh source')
        if self.measured(now):
            # Elapsed request time includes framing, ACKs, queueing and verification.
            return max(.000001, byte_count * self._seconds_per_byte)
        return self.route.bootstrap_seconds(byte_count)

    def observe_verified(self, byte_count, elapsed, now):
        require(type(byte_count) is int and byte_count > 0 and positive(elapsed), 'invalid observation')
        sample = elapsed / byte_count
        self._seconds_per_byte = (.5 * self._seconds_per_byte + .5 * sample
                                  if self.measured(now) else sample)
        self._sample_at, self._samples = now, self._samples + 1

    def snapshot(self, now):
        return {'path': self.name, 'generation': self.route.generation,
                'media': [h.medium for h in self.route.hops], 'verified_samples': self._samples,
                'sample_fresh': self.measured(now),
                'verified_bytes_per_second': (1 / self._seconds_per_byte
                                              if self._seconds_per_byte is not None else None),
                'basis': 'local verified request timing' if self.measured(now) else 'local bootstrap estimate'}


def choose_source(sources, piece, availability, active, now):
    """Least predicted finish time, including work on shared resources.

active maps path IDs to (Source, predicted_finish). A busy fast path can beat
an idle slow path. Callers wait for it instead of manufacturing a slow tail.
Transport names never participate in this decision.
"""
    choices = []
    for source in sources:
        if piece['sha256'] not in availability[source.name]:
            continue
        blocking = [finish for other, finish in active.values()
                    if source.route.resources & other.route.resources or source is other]
        delay = max([0] + [max(.01, end - now) for end in blocking])
        cost = delay + source.seconds(piece['bytes'], now)
        choices.append((cost, bool(blocking), source.name, source))
    return min(choices, key=lambda c: c[:3])[3] if choices else None


def download(sources, entries, availability, load_verified, commit, *, deadline,
             cancelled=None, max_workers=4, failed=None, on_failure=None):
    """Fetch independent chunks concurrently; commit each verified batch once.

load_verified(source, entries, deadline) must validate the ENTIRE bounded
batch before returning. It may raise on a transport error or invalid content.
Failed sources are excluded for this transfer. Store failures are fatal and
are never mislabelled as peer corruption. Resumption rechecks durable chunks.
The adapter must enforce its deadline; Python cannot kill an arbitrary reader.
"""
    require(1 <= len(sources) <= 8 and len({s.name for s in sources}) == len(sources), 'invalid sources')
    require(type(max_workers) is int and 1 <= max_workers <= 8 and positive(deadline), 'invalid transfer bounds')
    failed = set() if failed is None else failed
    pending = {e['sha256']: e for e in entries}
    require(len(pending) == len(entries), 'duplicate requested chunk')
    require(set(availability) == {s.name for s in sources}, 'missing availability')
    futures, active = {}, {}
    probe_budget = None
    probe_spent = 0.0

    def check():
        if cancelled is not None and cancelled():
            raise InterruptedError('transfer paused; verified chunks retained')
        if time.monotonic() >= deadline:
            raise TimeoutError('transfer deadline expired; verified chunks retained')

    def attempt(source, batch, bound):
        started = time.monotonic()
        result = load_verified(source, batch, bound)
        ended = time.monotonic()
        if ended >= bound:
            raise TimeoutError('batch deadline expired')
        return result, max(.000001, ended - started)

    # One in-flight batch per path. No speculative duplicate/endgame traffic on
    # scarce carriers. Each reader remains responsible for its actual TX costs.
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='meshticulum-chunk') as pool:
        try:
            while pending or futures:
                check()
                eligible = [s for s in sources if s.name not in failed]
                now = time.monotonic()
                ordered = sorted(pending.values(), key=lambda e: (
                    sum(e['sha256'] in availability[s.name] for s in eligible), e['sha256']))
                measured = [s for s in eligible if s.measured(now)]
                if probe_budget is None and measured:
                    # Reserve a small, finite share of predicted transfer time
                    # to discover an under-estimated alternative. This is a
                    # selection estimate, never an RF airtime grant or refund.
                    size = sum(e['bytes'] for e in entries)
                    probe_budget = min(1.0, .1 * min(s.seconds(size, now) for s in measured))
                probe = None
                if probe_budget is not None:
                    for candidate in eligible:
                        if candidate._samples or candidate.name in active or any(
                                candidate.route.resources & s.route.resources for s, _ in active.values()):
                            continue
                        sample = [e for e in ordered if e['sha256'] in availability[candidate.name]][:candidate.max_batch]
                        if not sample:
                            continue
                        cost = candidate.seconds(sum(e['bytes'] for e in sample), now)
                        if cost <= probe_budget - probe_spent:
                            probe = candidate, sample, cost
                            break
                for first in ordered:
                    if len(futures) >= max_workers:
                        break
                    if first['sha256'] not in pending:
                        continue
                    source = probe[0] if probe is not None else choose_source(eligible, first, availability, active, now)
                    if source is None:
                        continue
                    if source.name in active or any(source.route.resources & s.route.resources
                                                   for s, _ in active.values()):
                        continue
                    # Use the adapter's same bounded batch shape for initial
                    # and trained requests. Comparing a one-piece HTTP probe
                    # against 32-piece batches confounds latency with capacity.
                    bound = source.max_batch
                    batch = [e for e in ordered if e['sha256'] in pending and
                             e['sha256'] in availability[source.name]][:bound]
                    # Leave rare pieces with their chosen source when another
                    # available path would finish them sooner.
                    if probe is not None:
                        batch = probe[1]
                        probe_spent += probe[2]
                        probe = None
                    else:
                        batch = [e for e in batch if choose_source(eligible, e, availability, active, now) is source]
                    if not batch:
                        continue
                    request_deadline = min(deadline, now + source.request_timeout)
                    future = pool.submit(attempt, source, batch, request_deadline)
                    futures[future] = source, batch
                    active[source.name] = source, now + source.seconds(sum(e['bytes'] for e in batch), now)
                    for entry in batch:
                        del pending[entry['sha256']]
                if not futures:
                    raise ValueError('no peer supplied valid chunk batch')
                done, _ = wait(futures, timeout=min(.1, max(0, deadline - time.monotonic())),
                               return_when=FIRST_COMPLETED)
                for future in done:
                    source, batch = futures.pop(future)
                    del active[source.name]
                    check()
                    try:
                        result, elapsed = future.result()
                    except (OSError, ValueError):
                        failed.add(source.name)
                        pending.update((e['sha256'], e) for e in batch)
                        if on_failure is not None:
                            on_failure(source)
                        continue
                    # Storage/progress exceptions propagate rather than causing
                    # a second fetch/commit under a different peer's name.
                    commit(source, result)
                    source.observe_verified(sum(e['bytes'] for e in batch), elapsed, time.monotonic())
        finally:
            for future in futures:
                future.cancel()
