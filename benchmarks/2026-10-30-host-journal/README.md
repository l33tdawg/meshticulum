# Local host journal profile — 30 October 2026

Repeated accounting reads scanned the complete retained attempt history. The
flow adapter calls `snapshot()` on each step, and each new reservation reads the
same totals. A durable aggregate now provides both reads without scanning past
attempts. This change addresses host work that grows during larger transfers.

## Measurement

These are local SQLite measurements with synthetic history, not radio or file
transfer results. Both captures use macOS arm64, Python 3.14.8, SQLite 3.53.4,
the same profiler, fresh temporary files and WAL with `synchronous=FULL`.
Each case takes 25 latency samples. The baseline precedes the change; the after
capture follows it. This is one fixed-order local comparison, without a claim
about target-device filesystem performance or a stable latency distribution.

| Synthetic retained attempts | Snapshot median before → after | Queued reservation median before → after |
| ---: | ---: | ---: |
| 0 | 3.792 → 3.500 µs | 75.666 → 112.417 µs |
| 1,000 | 20.167 → 3.375 µs | 90.875 → 96.541 µs |
| 10,000 | 166.250 → 3.334 µs | 251.041 → 87.959 µs |

The structural result is independent of those wall timings: at 10,025 retained
attempts, a snapshot fell from 40,126 to 23 SQLite VM instructions; reservation
fell from 40,192 to 126. After the change, both instruction counts stay constant
across the three history sizes. VM diagnostics run separately because their
per-instruction callback perturbs timings. Small-history reservations can cost
more after the change because the same transaction also updates the aggregate.

Fixture history is inserted as synthetic `NOT_SENT` rows in one FULL transaction
and reopened before sampling. Timed reservations and settlements use the real
journal API. They never invoke a transport. The profiler checks exact retained
debt, zero pending attempts, released queue records and unchanged durability.
Setup and opening reconstruction are excluded from the timed operations.

[Baseline](baseline.json) and [after](after.json) record source hashes, environment,
sample ranges, distributions and final accounting. The baseline source is
`05a5dbcf683f445ea7491d0335142d7b55f70675`; the after capture identifies the
modified journal by its SHA-256. Reproduce from a source checkout:

```sh
python tools/benchmarks/profile_attempt_journal.py --histories 0 1000 10000 --samples 25
```

## Durability and compatibility

The aggregate is derived from retained attempts on every open, after owner
binding validation and inside the existing write transaction. Triggers maintain
it in the reservation's FULL commit, together with the request number, attempt
and queue metadata. A failed reservation rolls all of these back. Missing or
malformed live totals refuse further reservations. Settlement, release and
recovery do not reduce debt or lift a fence.

Opening still scans history and now writes the derived totals once. This is an
explicit startup cost, not included in the steady-state timings above. Old
journals migrate on open. Older clients' plain `INSERT` reservations maintain
the totals through the installed trigger. Direct replacement or deletion of
attempt history is unsupported; the aggregate is not protection against
privileged database modification or rollback. No firmware, USB pacing, flow
pins, deadline, ACK/listening policy or journal sync setting changed.

## Retained queue trace

The [queue timing summary](queue-timing.json) analyzes the sender's retained
24 October queued run, not a new execution. It contains all 46 recorded request
lifecycles and reports no missing/conflicting stages. Host queue-call acceptance
has a 21.643 ms median. On the MCU clock, the 34 within-group completion-to-next-
issue gaps total 0.399334 seconds; the 11 between-group gaps total 1.376196 seconds.
Grouping is nominal consecutive groups of four, not measured queue occupancy.

These intervals overlap and must not be added into an end-to-end cost. The
firmware deliberately preserves reverse listening between bursts. Host submit
events are logged after reservation and terminal events after settlement, so
these records cannot separate USB, CPU, journal commit and ACK-wait costs.
Neither this analysis nor the journal profile establishes an RF speed gain.

Analyze a retained single-endpoint result with:

```sh
python tools/benchmarks/mcu_queue_timing.py part-0-unit-b-RESULT.json
```

The tool emits duration aggregates and completeness diagnostics without packet
data, identifiers or absolute timestamps. It rejects aggregate-only benchmark
input and keeps host and MCU clock domains separate. Boot provenance remains a
caller claim. Future measurements need phase timings for host refill, serial
write/admission, terminal settlement and release, followed by repeated physical
comparisons under a fresh finite scope.
