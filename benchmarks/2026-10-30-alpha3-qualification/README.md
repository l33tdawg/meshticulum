# Alpha.3 qualification, 30 October 2026

All five operations completed with verified signed files and authenticated peer receipts.

Five finite B→A USB LoRa transfers used the exact installed alpha.3 candidate,
with host profiling enabled on both endpoints. The four 8,164-byte files form
two pairs in queued/baseline then baseline/queued order; the fifth file contains
100,000 useful bytes in five signed parts. Each case used a fresh random signed
public byte bundle. Current bench distance was **not reconfirmed**; the older
approximately 20 m report does not describe these runs.

| Run | Mode | Useful bytes | Parts | Startup → final receipt (s) | Profiled useful kB/s | Scope → both closed (s) |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 0 | queued | 8,164 | 1 | 7.729350 | 1.0562 | 15.166408 |
| 1 | baseline | 8,164 | 1 | 8.656340 | 0.9431 | 15.937535 |
| 2 | baseline | 8,164 | 1 | 9.046716 | 0.9024 | 15.664650 |
| 3 | queued | 8,164 | 1 | 7.883593 | 1.0356 | 14.692559 |
| 4 | queued | 100,000 | 5 | 98.903315 | 1.0111 | 106.907634 |

Rates use decimal kB/s. Multiply by 8 for kbit/s. Paired queued rate differences:
pair 1 (queued → baseline): +11.99%; pair 2 (baseline → queued): +14.75%. These are two small paired observations, not a sustained-throughput
or broad stability distribution. They compare the two modes within this
candidate, not performance against alpha.2. Profiling adds overhead to every
listed rate.

Application timing uses the sender's monotonic clock, from the first sender
child startup through the final authenticated peer receipt. The 100,000-byte
interval includes the management and setup gaps between all five parts.
Scope-to-closure uses the controller's wall clock, from original scope issue
through both final endpoint RPCs joining after child termination and postflight
status. It includes setup and retirement and is a separate timing boundary.

## Exact candidate and radio

- Source commit: `7b38c1f585786d764a952a2a67cbfcf56af0ad80`.
- Installed wheel SHA-256: `ed3f8d23d4aa37cffa3764214f1d9f6a793e09bfabcd6c5bd0b05b858c263520`.
- All 72 installed runtime files matched the candidate.
- `radio_file.py`: `b8d1c31418f52ce7db0147ef5a66d2e61d733175c9e959849e7e94ac6224b27d`.
- `radio_bundle.py`: `4c834cee0c24b6de8b012089f0cd4abc99ff5e1c6f5d7ae6a4c564e398660f53`.
- Firmware: `mtb1-6cfde1fdd4` on both endpoints;
  firmware-source metadata SHA-256 `68d74f96076d073eb403601217d93d9ee6d9eae0e1390749b9703192582a7ce8`.
- LoRa 921.375 MHz, SF7/BW500 kHz, CR4/5, preamble 8, sync word 0x42,
  requested chip power 0 dBm. This does not establish antenna gain or EIRP.
- Run 0 used a 120-second scope and 90-second radio lease; the following small
  runs used 90/60 seconds. The large file used 240/60 seconds. Native cumulative
  caps remained 1,024 attempts and 300,000,000 estimated airtime µs.
- The helper revision after run 0 persisted the original supervisor clock
  across parts and retained failure-attached profiles. Per-run helper hashes
  are recorded separately; candidate runtime and example bytes stayed fixed.

## Host phase evidence

[RESULTS.json](RESULTS.json) contains all 24 phase aggregates for each endpoint
of every part: count, total, minimum, maximum, invalid, dropped and saturation.
The clock is `time.perf_counter_ns`; intervals are inclusive and overlap.
They cannot be added across labels or subtracted from application elapsed time.
MCU/native counters, host journal estimates and host duration clocks remain
separate measurements. Profiling is diagnostic and never settlement authority.

Physical queue event counts describe only the retained trace tail. Full sender
parts retain at most 512 events, so those submit/release counts do not prove
complete request lifecycle coverage. Aggregate flow counts and current
`pending`/`worker_pending` drain status are separate fields. A null queue
snapshot is not a zero-count measurement; receivers in these runs use the
synchronous ACK path. All host phase aggregates are retained independently.

## C host checks and accounting

Unit C installed the same exact candidate in an isolated normal environment,
verified 72 runtime files and pinned dependencies, and passed an offline signed
287-byte public resource check. Synthetic WAL/FULL journal recovery retained
its two attempts, 300 estimated µs and one recovered UNKNOWN; the fence refused
new work and survived release/reopen. All 8 owned children joined with zero
exit status, and 20 tracked service states/lifetimes were unchanged. C opened
no radio, issued no radio commands and created no grants. This is host evidence,
not C RF or production activation.

The JSON keeps each new host journal total, every native before/after counter,
and the prior logical paid totals plus new spend. Full run allowances remain
reserved without refunds during the campaign. Native boot counters are not the cross-boot logical
host ledger. Original failed scopes, fences and historical unknown actual RF
cost remain retained; historical actual cost is still incomplete.

No new RF loss injection, final-ACK loss, in-flight crash recovery, range,
sleep/energy, mixed-path or private/native/security qualification is claimed.
The older [24 October comparison](../2026-10-24-mcu-queue-lora/) remains a
separate dated result, and this record makes no matched superiority claim
against other protocols.
