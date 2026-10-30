# Explicit scalar eight-frame trains

The SF5/BW500 source path can admit eight DATA frames in one finite train. It
requires a new immutable policy on both endpoints and the exact MBQ8 capability
from the selected firmware. Four-frame grants, empty capability queries and
legacy synchronous mode keep their previous behavior. The first finite hardware
sample verified a file at 801.424349 useful bytes/s including startup; the target
of at least 3000 B/s remains open. See the
[measured sample and limits](../benchmarks/2026-10-30-sf5-eight-profile/README.md).

This uses the existing scalar handover: prepare one flow frame, seal a fresh
ciphertext, reserve its durable attempt charge, write its descriptor, and wait
for exact QUEUED acceptance before offering the next frame. The native queue
can transmit earlier accepted frames while the host prepares later ones. No
vector handover, speculative credit or staged uncharged ciphertext is added.
The dormant MBQ2 batch-job prototype is separate.

## Bounds and negotiation

Use `meshticulum.bench.burst_protocol.policy_for_depth(8)` when constructing an
explicitly admitted plan. `policy_for_depth()` still selects four. Both peers
must bind the same policy into their original shared route/grant digest. Eight
requires a 1664-byte owner credit ceiling and eight reassembly frames; the
authenticated receiver ACK still establishes actual credit. The existing
32-bit SACK and frozen flow/port support this window without source changes.

The [serial contract](MCU_BURST_PROTOCOL.md) specifies the distinct capability
request and 49-byte OPEN8. A four-only peer cannot silently substitute its
capability. DATA, terminal events and releases keep their existing formats;
the largest scalar command body is 314 bytes within the 320-byte serial bound.

The following bounds stay unchanged:

- 255-byte ciphertext packets; 193 bytes of inner DATA payload at the admitted
  208-byte plaintext ceiling.
- Original host/grant/ARM deadlines and at most 2 seconds of autonomous queue
  authority. A slower profile or insufficient remaining time is refused.
- Checked 5 ms receive dwell between operations and 100 ms reverse listen after
  the selected train or an actual empty tail. RX_DONE and valid-header handling
  retain priority; successful RX restart establishes the floor.
- 64-byte/2 ms host USB write pacing, 2048-byte native input FIFO and 8-slot output
  queue. Output backpressure delays dispatch rather than dropping the bound.
- Fresh-attempt crypto, FULL durable reservation, exact physical settlement,
  sticky UNKNOWN, no debt refunds and physical drain before successful closure.

A released slot does not admit an unlimited streaming train. The worker and
native admission count retain the original train bound until all its records
are released. Eight delayed exact terminal events/replies and eight native
release receipts are retained. A suffix that expires without issue promotes the
last successful completion to an actual 100 ms tail, including across a new
OPEN; failed receive rearm still fences future TX.

## Verification and memory

`test_burst_eight_worker.py` covers exact policy/shape negotiation, old-firmware
refusal, durable eight-attempt debt, partial release, bounded replay caches and
ambiguous writes. `test_burst_eight_native.py` exercises the actual adapter with
the diagnostic profiler both off and on: full trains, delayed RX restart,
empty/expired tails, output pressure, expiry, cancellation, failed rearm and
release/OPEN replay. `test_burst_eight_composition.py` uses authenticated flows,
real journals and a deterministic half-duplex model for DATA/ACK loss and
underfill controls. These are source tests, not RF measurements.

The shared queue has fixed storage for eight slots. The pinned Xtensa compiler
reports 3,848 bytes for `TxBurstQueue`; the host test ABI reports 3,880. Added
release/offer bookkeeping and the optional profiler have separate costs.

The [profile-enabled build record](../provenance/MCU_SF5_EIGHT_PROFILE_FIRMWARE.json)
binds firmware identity `mtb1-bc5858fa0b` to one pinned preparation and successful
builds for both targets, with retained ELF and map hashes:

| Target | Linked static RAM | Application binary |
| --- | ---: | ---: |
| `mtbench_heltec_v4` | 35,204 bytes | 404,096 bytes |
| `mtbench_xiao_s3_wio` | 34,868 bytes | 397,744 bytes |

These are build sizes, not live memory margins or an independent reproduction.
The build record predates installation. Both Heltec units subsequently passed
app-only installation, read-only diagnostics and one finite verified-file run.
After that run, internal free heap exceeded 340,000 bytes on each unit and
loop-stack minimum-free values exceeded 4,600 bytes. Exhaustive worst-case memory
and sustained-throughput qualification remain pending. The idle-only
[memory snapshot](MCU_NATIVE_PROFILING.md#query-only-memory-snapshot) supports
exact-build checks before and after bounded eight-frame work. Configured
external RAM is not evidence of internal stack headroom.

Eight frames amortize the unchanged reverse-listen floor, but scalar USB and
host processing can still leave the native queue empty. Evaluate those costs
with identical useful-file/startup/receipt boundaries and loss schedules. An
ideal timing calculation or passing source model does not establish 3000 B/s.
