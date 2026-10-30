# Host phase timings

Available in Python package `1.0.0a3` and the alpha.3 checkout examples.
The [release record](RELEASE_READINESS.md) identifies the tested artifacts;
the [journal profile](../benchmarks/2026-10-30-host-journal/README.md) covers
separate local measurements on synthetic retained history.

Add `--profile-host` to an already approved `radio_file.py transfer` or
`radio_bundle.py transfer` invocation to include host duration aggregates in
its result. Original grants, deadlines and cleanup govern every action.
Profiling is disabled by default and adds measurement overhead when enabled.

The `host_phase_timings` result is collected after cleanup, including physical
release and disarm. A failed transfer still raises its original exception group;
code catching that group can inspect its `host_phase_timings` attribute.
If diagnostic export fails, `host_phase_timings_unavailable` is set instead.
No new file or diagnostic network endpoint is created.

## Recorded phases

| Phases | Boundary |
| --- | --- |
| `queue_call`, `queue_pump`, `queue_clock` | Whole DATA queue call, initial physical poll/release, and new-burst clock mapping |
| `queue_reserve` | Durable attempt reservation before the serial write |
| `queue_serial_write`, `queue_admission_wait` | Frame encoding/write, then queue admission; admission may ingest terminal events |
| `queue_terminal_settle` | Durable settlement of completion, refusal, cancellation or uncertainty |
| `release_request`, `release_roundtrip`, `release_commit` | Durable command allocation, exact MCU release confirmation, and journal commit |
| `journal_transaction` | Lock acquisition, SQL and COMMIT or ROLLBACK; includes opening reconstruction and cleanup |
| `serial_write`, `serial_wait_writable`, `serial_os_write`, `serial_pacing_sleep` | Full encoded write, readiness wait, syscall/deadline check, and pacing sleep calls |
| `serial_read`, `serial_read_wait`, `serial_os_read` | Full read, readiness wait, and read syscall |
| `flow_step`, `flow_accounting`, `flow_receive`, `flow_dispatch`, `flow_pump` | Flow step and its accounting, receive polling, queued dispatch and flow-pump calls |
| `host_loop_sleep` | The example's actual 5 ms loop sleep calls, outside `flow_step` |

All intervals use `time.perf_counter_ns`. They measure elapsed host time,
including blocking and scheduling. They are not CPU time, MCU time, measured RF
airtime or an authenticated peer receipt. Original authority clocks are not
replaced or sampled by the collector.

Intervals are **inclusive and overlap**. A queue call contains reservation and
serial work; a journal transaction can occur inside several other phases.
Do not add these totals into an end-to-end latency or subtract overlapping
totals to invent a missing component. Pacing branches create no fabricated wait
intervals: only actual sleep/wait calls are measured.

## Format and failure handling

The schema is `meshticulum.host-phase-timing.v1`. Each of its fixed 24 phase
names has `count`, `total_ns`, `min_ns`, `max_ns`, `invalid`, `dropped` and
`saturated`. A valid measured call increments `count` even if that call raises;
counts do not establish successful completion. An empty phase has null
minimum/maximum and zero totals. Invalid clock samples are dropped, not recorded
as zero-duration calls. Counters saturate at `2**63 - 1` and flag saturation.

The collector retains no per-call history, absolute timestamps, request IDs,
packet bytes, hashes, keys or exception text. Ordinary diagnostic faults do not
change transport outcomes. Process cancellation remains effective, and an
error while finishing a measurement cannot replace the original exception.
The disabled path reads no diagnostic clock.

For SDK composition, pass the same `HostPhaseTimings()` instance as
`host_timing=` to the serial channel, serial worker, `AttemptJournal` and
`BenchFlow`. They accept the exact collector type or `None`; there is no callback
or arbitrary-label interface. The checkout examples share it automatically
when enabled. HAT operation records flow/journal phases; USB phases stay empty.

## USB pacing and remaining measurement

The host still limits writes to 64 bytes with a 2 ms inter-write sleep to
protect retained MCU receive buffers. A synthetic full 255-byte queued DATA
frame commonly encodes to 350–359 bytes, requesting five sleeps, or 10 ms,
when each write accepts 64 bytes. Escaping and partial writes can increase that
delay. These are source-model delays, not a decomposition of the prior physical
comparison or evidence that shorter pacing is safe.

The serial writer now rechecks the original deadline after writable readiness
returns and immediately before each write. Late readiness cannot permit another
write. Existing pacing and MCU reverse-listening windows remain intact.

Source tests cover diagnostic failures, cancellation, enabled/disabled wire and
debt parity, encrypted flow composition, refusal, ambiguous writes, release
retry and post-expiry physical cleanup. The [alpha.3 hardware evidence](RELEASE_READINESS.md#alpha3-hardware-evidence)
has its own exact wheel and execution scope. Repeated phase distributions,
larger-transfer recovery and broader target-host performance remain separate
measurement work. The earlier [24 October comparison](MCU_ACCELERATION.md#measured-comparison-24-october-2026)
used a different SDK artifact and had no phase collector.

## Bundle handoff

The [alpha.3 qualification](../benchmarks/2026-10-30-alpha3-qualification/README.md)
already completed two counterbalanced pairs and a five-part 100,000-byte file.
The latter took 98.903315 seconds from first sender startup to final authenticated
receipt, including inter-part management. Its four gaps from a sender receipt
to the next sender child startup were 3.804074, 3.734973, 3.964503 and 3.610442
seconds: **15.113992 seconds, or 15.28% of that interval**. These are ordered
boundaries on the same sender monotonic clock, not differences between
overlapping phase aggregates. The part startup-to-receipt intervals sum to
83.789323 seconds. These observations do not identify the individual cost of
SSH, process creation, environment verification or device acquisition.

The current source checkout's [batch API](../examples/BUNDLE.md#keep-one-device-owner-across-approved-parts)
retains one OS device owner while creating fresh authority and retiring the complete
session for every part. A subsequent [5 kB SF5 run](../benchmarks/2026-10-30-bundle-handoff/README.md)
verified all five out-of-order parts. Its four receipt-to-next-entry gaps totalled
**995.923 ms**, only 4.077 ms below the one-second target. Each stronger
receipt-to-next-DATA gap was below one second, but those gaps totalled
**2.377247 seconds**. These are single-run observations, not a repeatable bound
or an alpha.3 release measurement. Eight DATA retries stretched useful goodput
to **0.133906 kB/s**; 2–3 kB/s remains unproven. Removing just the recorded 15.114
seconds would put that old file near 1.19 useful kB/s; it would not establish
sustained capacity.

A persistent process has no later child-start boundary. Record an explicit
part-entry marker before setup, and report both receipt-to-next-part-entry and
receipt-to-next-first-DATA intervals on that sender's monotonic clock. Retain
first file start through final authenticated receipt and verified publication,
plus original scope issue through both endpoints closed. Do not shift setup
outside the interval to meet the target. Cross-host/controller times remain
separate clock domains. The [five-part cold plan](../benchmarks/2026-10-30-bundle-handoff/COLD_PLAN.md)
specifies source geometry and budget; it supplies no physical result. The
measurement record preserves the failed 50 kB attempt and partial 5 kB attempt,
including exhausted ACK budgets, alongside the successful 5 kB run.

## Qualified SF7 capacity model

At the measured SF7/BW500 kHz, CR4/5, eight-symbol preamble profile, a full
255-byte packet has an estimated 99,904 µs airtime. Its 31-byte wire header,
16-byte authentication tag and 15-byte inner flow header leave 193 transport
payload bytes. Thus even an uninterrupted sequence with no guard, ACK or host
cost has an optimistic ceiling of **1.932 transport kB/s**.

The existing four-frame queue policy includes three 5 ms gaps and a 100 ms
reverse-listen window per full train. Its ideal calculation is
`4 × 193 / (4 × 0.099904 + 3 × 0.005 + 0.100)`, or
**1.500 transport kB/s**. It assumes continuously filled trains and ACKs that
fit the listen windows. An empty queue can trigger the reverse window early,
so underfilled trains can be slower. USB pacing remains 64 bytes/2 ms.

For the old 100,000-byte signed shape, 539 DATA frames plus five receipt
confirmations give 53,778,176 estimated RF µs. Adding 404 within-train gaps and
135 reverse windows gives an ideal 69,298,176 µs, or **1.443 useful kB/s**.
This includes its signed-response overhead but assumes no retries, setup,
extra host delays or queue underfill. It is a source model, not measured
goodput or a guarantee. All rates here use decimal kB.

The immediate headroom is management reuse and keeping admitted four-frame
trains fed while preserving the existing pacing and reverse-ACK guards.
**2–3 useful kB/s requires a faster radio profile and separate qualification**;
it exceeds even the unguarded transport ceiling of this SF7 profile.

## Fastest supported LoRa profile: next measurement

The next source plan selects **SF5/BW500 kHz, CR4/5, preamble 12**, the fastest
LoRa configuration supported by the current public SDK and recorded firmware
source. The firmware requires at least 12 preamble symbols at SF5/6. This is a
new admitted profile, not a measured upgrade to the SF7 result. The source
identity check for `mtb1-6cfde1fdd4` confirms these conditions in `main.cpp`;
it does not itself test the profile on a device.

At SF5, the full 255-byte estimate is 34,640 µs, giving **5.572 transport kB/s**
without guards. The unchanged four-frame policy gives
`772 / (4 × 0.034640 + 3 × 0.005 + 0.100)`, or **3.045 transport kB/s**.
For the planned five 10,000-byte signed parts, 280 DATA frames and five receipt
confirmations estimate 9,671,760 RF µs. Including 210 within-train gaps and
70 reverse windows gives 17,721,760 µs, or **2.821 useful kB/s** ideally.
Host pacing, setup, underfilled trains, ACK behavior and retries can reduce it.

The [50,000-byte cold plan](../benchmarks/2026-10-30-bundle-handoff/COLD_PLAN.md)
uses A→B and order 4, 2, 0, 3, 1. Its waveform, direction, geometry and lifecycle
differ from the old B→A 100,000-byte run, so a future result would not isolate
the handoff improvement or form a paired performance comparison. Faster-profile
range, loss/recovery and sustained capacity remain separate measurements.
