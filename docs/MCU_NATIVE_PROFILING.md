# Bounded native timing diagnostics

`MNP1` measures the MCU work around checked radio receive rearm and queued DATA
dispatch. It is diagnostic source instrumentation, disabled by default. It does
not establish RF throughput or claim the measurements are free of observer cost.
The current RadioLib call is measured as a whole: this does not split its
internal TX staging, FIFO/SPI traffic, PA setup and launch into separate timings.

The firmware recipe's `prepare --native-profile` option enables the collector.
Preparation copies the seven application source files and generates
`native_profile_config.hpp` as the eighth application input. That generated
header is force-included, rejects an external definition of the profiling macro,
and participates in the firmware identity hash. `SOURCE.json` records
`build_options.native_profile`; build checks it against the exact generated
header and application identity. Enabled and disabled preparations therefore
have distinct identities. Neither source preparation nor compilation authorizes
installation or radio operation. Historical firmware records remain evidence
for their original source and build configuration.

The [eight-frame profiling build record](../provenance/MCU_SF5_EIGHT_PROFILE_FIRMWARE.json)
records identity `mtb1-bc5858fa0b`, one pinned preparation and successful Heltec V4
and XIAO S3 WIO builds, including application, ELF and map hashes. It does not
claim independent reproduction, installation, runtime memory margins or RF
qualification. [Build sizes](MCU_EIGHT_TRAIN.md#verification-and-memory) are
separate from the later [same-boot hardware observations](../benchmarks/2026-10-30-sf5-eight-profile/README.md).
That finite sample verified a file but missed the throughput goal; all native
records were exact and reported no failures, invalid clocks or saturation.

## Recorded phases

The collector has twelve fixed records, with no heap allocation, prints or ISR
work. Each record contains 32-bit saturating call, failure and invalid-clock
counts, a flags word, and 64-bit saturating total/minimum/maximum microseconds.
Calls include invalid-clock samples; duration aggregates exclude them. A zero
duration is valid. Counter/total saturation sets flag `1`; a regressing sample
clock sets flag `2`. Either flag makes the aggregate unsuitable for exact timing
comparisons. Failures are counted separately and their valid durations remain in
the aggregate; the result does not provide separate success/failure histograms.

| ID | Phase | Boundary and scope |
| --- | --- | --- |
| 0 | `TX_FINISH_CLEANUP` | `finishTransmit` and the board after-transmit hook, for every handled TX_DONE |
| 1 | `RX_START_CALL` | The checked `radio.startReceive` call, including initial ARM and later receive restarts |
| 2 | `TX_DONE_TO_RX_RETURN` | Inclusive parent: from entering TX_DONE handling before finish/cleanup through receive restart and its pacing hook |
| 3 | `ISSUED_TO_STARTED` | Inclusive parent: the queue's ISSUED timestamp through checked driver launch and STARTED settlement/publication |
| 4 | `ISSUED_PUBLISH` | Offer newly issued queue evidence to the existing bounded output queue |
| 5 | `ISSUE_STANDBY` | The app's checked standby call after claim |
| 6 | `ISSUE_IRQ_REREAD` | The checked IRQ reread after standby |
| 7 | `ISSUE_RX_DRAIN` | Conditional completed-RX drain found by that reread, including its nested checks |
| 8 | `ISSUE_IRQ_CLEAR` | The app's checked clear-IRQ call |
| 9 | `ISSUE_COPY_PREPARE` | Copy sealed bytes into the driver buffer and call the board before-transmit hook |
| 10 | `ISSUE_TX_START_CALL` | The complete RadioLib `startTransmit` call |
| 11 | `STARTED_PUBLISH` | Set the transmitting state and settle/offer STARTED evidence |

Phases 0–2 cover both scalar and queued TX/RX paths; phases 3–11 cover queued
DATA dispatch. Parent phases overlap their children. Never add parent totals to
child totals, or infer disjoint wall time from sums across unlike sample counts.
Successful parent samples include probe overhead and ordinary bookkeeping
between child calls. An early-return parent sample is failed and may include
the original containment cleanup before return. Publication timing means an
offer to the output queue, not physical USB delivery or host acknowledgment.
`RF_DONE` remains local driver evidence, not an authenticated file receipt.

No timing sample creates authority or changes a driver error. The original
standby/IRQ reread race barrier, RX priority, guard durations, deadlines, replay
decisions, boot caps and paid debt remain in the same control order. The receive
floor still begins only after checked receive start succeeds. A default build
instantiates no collector and evaluates no profiling clocks or probe arguments.

## Read-only retrieval

Request opcode `19` and reply opcode `0x95` are separate from MBQ1. Existing MBQ1
capability bytes, request bodies and event records are unchanged. An old or
profiling-disabled firmware has no MNP1 capability and returns a refusal; a
missing reply is not permission to assume support or reset/retry radio work.

Use an empty body for capability discovery, then a one-byte phase ID (`0`–`11`)
for each record. A current boot identity and a positive request ID are required.
The read does not advance the mutation high-water mark. Both capability and
record queries require disarm, no TX/result pending, no queued or retained burst
entries, and no pending containment. Queries never poll the queue, finish
cleanup, release evidence, reset a counter or alter a lease. Output backpressure
can withhold a reply just as it can for other read-only commands.

Each reply begins with the following eight-byte header:

| Bytes | Value |
| --- | --- |
| 0–3 | ASCII `MNP1` |
| 4 | Phase count, `12` |
| 5 | Aggregate width, `40` |
| 6 | `255` for capability, otherwise the requested phase ID |
| 7 | `1`: boot-cumulative microsecond records |

Capability replies end at eight bytes. A phase reply appends big-endian
`calls:u32, failures:u32, invalid:u32, flags:u32, total_us:u64, min_us:u64,
max_us:u64`, for a fixed 48-byte body. The enclosing existing serial frame binds
the reply to its MCU boot and query request. One capability plus twelve records
is a bounded retrieval. There is no reset command, per-packet trace, payload,
peer identity or secret in this diagnostic body.

The standalone codec is `meshticulum.bench.native_profile`; it opens no port and
does not change existing workers. The admitted application's existing exclusive
owner performs retrieval after cleanup. Records are cumulative for that boot,
so an application that needs one-run deltas must capture both boundaries on the
same boot without intervening radio work and retain invalid/saturation flags.

### Query-only memory snapshot

The same request/reply opcodes also accept the single selector byte `0xfe`.
This uses the same fully disarmed/drained/contained gate and additionally
requires a known loop-task handle. Its 24-byte reply is distinct from timing:
ASCII `MNS1`, version byte `1`, unit byte `1` (bytes), two reserved zero bytes,
then four big-endian unsigned 32-bit values in this order:

1. Current free internal, byte-addressable heap.
2. Sum of that heap's per-region minimum free counts since boot. The regional
   minima may have occurred at different times; this is not a measured global
   instantaneous minimum.
3. Largest free block in that internal heap.
4. Minimum free loop-task stack since that task started.

`heap_caps_get_info` selects `MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT`, so external
PSRAM does not inflate these heap figures. This aggregates region observations,
not an atomic snapshot of every allocator in the system. The pinned ESP32-S3
FreeRTOS `task.h` explicitly defines `uxTaskGetStackHighWaterMark` in **bytes**;
the pinned Xtensa `portmacro.h` defines `StackType_t` as `uint8_t`. A compile-time
assertion enforces the latter. No word-to-byte multiplication is applied.

Heap walking and stack scanning occur only inside this explicit idle query,
never in RX/TX dispatch, the IRQ handler or ordinary timing probes. They allocate
no diagnostic buffer dynamically and reset nothing. The stack low-water includes
the query itself and any earlier loop work. The standalone codec exposes
`memory_query` and `NativeMemorySnapshot`; zero headroom is retained, not treated
as successful memory qualification. A linked RAM-size report is not a runtime
heap or stack margin. Qualification needs exact-build snapshots before and after
the admitted worst-case work, including eight retained frames and cleanup.

## Source controls and limits

Native sanitizer tests run the actual firmware loop with profiling enabled and
disabled. They exercise deterministic driver delays, receive priority and the
post-standby RX race, failures before and after claim, uncertain completion,
clock/debt regression, cancellation/expiry, and output backpressure. Additional
controls cover aggregate saturation, strict cross-language decoding, idle-only
queries without mutation/debt changes, disabled probe arguments and clocks, and
the hashed build setting. These are modeled source controls. The linked build
sizes above are complete; observer cost, live memory margins and physical
timings still require fresh exact-build evidence.

## SPI setup candidate

The pinned [Heltec V4 target](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/variants/heltec_v4/target.cpp#L6-L10)
constructs its radio module without overriding SPI settings. The pinned
[RadioLib default](https://github.com/jgromes/RadioLib/blob/6d8934836678d8894e3d556550475b37dce3e2b6/src/BuildOpt.h#L432-L434)
is 2 MHz, MSB-first, mode 0; both the retained four-frame preparation and the
eight-frame profiling build use it. Its
[Arduino HAL](https://github.com/jgromes/RadioLib/blob/6d8934836678d8894e3d556550475b37dce3e2b6/src/hal/Arduino/ArduinoHal.cpp#L102-L109)
transfers one byte at a time, so SPI wire time and per-byte software overhead
both contribute to the complete `ISSUE_TX_START_CALL` measurement.

A separately pinned 4 or 8 MHz board configuration is a candidate experiment
after baseline profiling. It must retain the existing BUSY/status checks,
standby/IRQ race barrier, RX restart, listen floors and USB pacing. Compare
native phase deltas and the same useful-file/startup/receipt boundary before
attributing a gain. No higher SPI clock has been qualified here; changing the
bus clock does not change the admitted RF profile or its airtime.
