# Host phase timings

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
retry and post-expiry physical cleanup. Real phase distributions, target-host
costs and larger-transfer/recovery performance still need fresh bounded
measurements. The last physical queue comparison remains the
[24 October run](MCU_ACCELERATION.md#measured-comparison-24-october-2026).
