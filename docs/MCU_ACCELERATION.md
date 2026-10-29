# Host-backed MCU acceleration

The source candidate now connects a bounded MCU DATA ciphertext queue to the
serial worker, durable attempt journal and authenticated `BenchFlow` runtime.
The firmware includes the adapter and starts with its queue closed. Existing
plans use the synchronous path; an immutable grant record must opt into bounded
queue handover. Linux retains session keys, AEAD, peer ACK authentication,
credit, retries and application verification.

The candidate now has a **completed finite hardware comparison** as well as
host/native tests and a Heltec V4 build (`mtb1-6cfde1fdd4`). Two independent
preparations on the same pinned macOS-arm64 host produced identical application BIN and ELF bytes. The
[build record](../provenance/MCU_BURST_FIRMWARE.json) binds all six application
inputs, recipe, lock and output hashes; the
[verifier](../tools/verify_burst_firmware.py) checks that source/provenance binding.
That record establishes same-host build reproduction. The finite deployment
and RF comparison below have their own evidence; cross-host reproduction and
wider hardware qualification remain open. The [serial and admission contract](MCU_BURST_PROTOCOL.md)
defines the policy, records, clock assumptions and closure requirements.

## Measured comparison: 24 October 2026

One fixed-order baseline-then-queue pair sent two fresh **8,164-byte signed
files** between Heltec V4 USB radios about **20 m apart**. Both used the same
`mtb1-6cfde1fdd4` firmware and SF7/BW500/CR4:5 profile: preamble 8, sync `0x42`,
921.375 MHz and 0 dBm. Linux retained keys, authenticated ACKs, credit and
retries in both modes.

| Boundary | Synchronous baseline | MCU queue |
| --- | ---: | ---: |
| Sender startup → authenticated receipt | 8.766668 s; **931.254611 B/s** | 7.526076 s; **1,084.761833 B/s** |
| First DATA worker call / queue submission → receipt | 8.013809 s; **1,018.741525 B/s** | 6.900232 s; **1,183.148625 B/s** |
| Receipt → drained endpoint completion | 3.329 ms | **27.456 ms** |

Startup-inclusive goodput increased **16.483915%**. Both publishers and whole
files verified, with zero DATA retries, receive-drop increases or new UNKNOWN
outcomes. Every queued DATA request reached RF_DONE and its retained result was
released; pending physical records were zero at closure. Receipt and drain are
separate observations, including when local RF work has already finished. The
pinned completion gate checked drained STATUS, but its raw reply and RX-rearm
timestamps were not independently retained.

The 46 DATA driver calls totaled **4.983012 s** synchronously and **4.989588 s**
queued. Baseline worker calls totaled **6.156631 s**, including their driver
calls. Queue-accept calls totaled **1.118990 s** and overlapped MCU work; adding
acceptance and driver totals would count overlapping time twice. These results
support reducing the host round trip on the per-packet critical path. They do
not establish CPU-only savings, independently measured RF airtime or faster
modulation.

The measured source is `f06ebeb3e687da4688657311830e95326a0107d2`; the installed
wheel SHA-256 is
`7998a705ab6a5641ecb9191f08e9b63ba7a676612dcfa7455e291e6177149ad0`.
It identifies as `1.0.0a1`; future `1.0.0a2` artifacts need their own source and
installation checks. The [sanitized result](../benchmarks/2026-10-24-mcu-queue-lora/results.json)
retains both samples and exact boundaries. This single pair does not establish
sustained capacity, a completion distribution, loss/disconnect recovery, energy
use, field range or operation on another installation.

### Failures retained before the successful pair

The initial finite rollout timed out and both units were verified restored to
the prior firmware. A later successful installation was followed by a baseline
that exhausted an undersized frozen pacing budget after 45 DATA packets. The
8,827-byte signed response required a 46th DATA packet, so that transfer stayed
failed and fenced; queue mode did not run under that scope.

The scheduler intentionally adds settled estimates to sampled worker usage.
Those overlapping charges were preserved. The failure led to an explicit
budget-exhaustion diagnostic, a cold-transfer feasibility gate and independent
per-operation journal limits alongside absolute native boot caps. The later
pair used fresh independently approved scopes while retaining earlier paid
costs, failed ledgers and unresolved historical references. No restart or
accounting epoch refunded that debt. [The comparison record](../benchmarks/2026-10-24-mcu-queue-lora/README.md)
keeps this sequence visible.

## Earlier measured starting point

The [24 October distance sample](../benchmarks/2026-10-24-lora-distance/results.json)
used source `b5fd674b20fc5d0416fc461dcd562731d0faf0b7` and firmware
`mtb1-020e287255`. Two Heltec V4 USB radios about 20 m apart delivered 8,164 useful
bytes at SF7/BW500/CR4:5, 921.375 MHz and 0 dBm. The receipt was authenticated,
and publisher and whole-file verification passed. There were 46 DATA packets,
no retries and no receive-drop counter increases.

| Measurement | Result |
| --- | ---: |
| Sender startup to authenticated receipt | 9.183714 s; 888.965 B/s |
| First DATA worker call to authenticated receipt | 8.001248 s; 1,020.341 B/s |
| DATA worker calls, summed | 6.134363 s |
| DATA driver calls, summed | 4.984246 s |
| Median DATA worker / driver call | 132.347 / 108.781 ms |
| Median estimated DATA airtime | 99.904 ms |

The worker-minus-driver difference is 1.150117 s. It includes host and boundary
work; the sample does not attribute all of it to one component. Driver elapsed
time includes driver work and is not an independent RF airtime measurement.
These historical measurements do not establish a queue speedup.

A separate CPU-only diagnostic ran 128 durable reserve/NOT_SENT-settle pairs per
endpoint. Sender reserve and settle medians were 0.460 and 0.419 ms; receiver
medians were 0.460 and 0.433 ms. A paired median was not measured. No transport or
radio was opened. Journaling was not established as the main delay.

## What the implementation does

The grant's exact `data_burst` record participates in the original shared
route/grant digest. It selects irrevocably charged carrier handover at queue
acceptance, depth four, a maximum 2,000,000 us MCU hold, 5,000 us between-packet
receive dwell and 100,000 us reverse listen. The full record is in the
[protocol contract](MCU_BURST_PROTOCOL.md#admission-and-ownership). Locally
classified DATA is the only queued lane. ACK snapshots and control remain on
the synchronous host path; the MCU gets no keys and makes no retry decisions.

[`BurstSerialWorker`](../src/meshticulum/bench/burst_worker.py) checks the exact
firmware capability, samples a boot/ARM-bound clock and opens an immutable
context. Every new burst takes a fresh clock sample and narrows its deadline
against the original OPEN cap, ARM end and current host authority end. Clock
mapping assumes at least 0.99 MCU microseconds per host microsecond, rejects RTT
over 50 ms and subtracts a 20 ms margin. It never copies a host timestamp into an
MCU deadline or extends an existing deadline.

A DATA item binds the boot, OPEN token, exact request, ciphertext hash/length,
independently checked airtime estimate and common burst deadline. The firmware
recomputes the hash and estimate. At most four admissions share that deadline;
retained terminal outcomes also occupy the four slots. Pending airtime, 100 ms
start guards and receive cooldown must fit before it. The adapter drains RX
before driver handover, defers to an active header, retains receive dwell and
reverse listen, and reports stable MCU event timestamps. Legacy control waits
for that reverse window rather than preempting it.

The request-keyed host router accepts physical events while other serial
commands wait. [`AttemptJournal`](../src/meshticulum/bench/attempts.py) commits
exact ciphertext hash/length, immutable queue metadata and prepaid debt before
any DATA write. It stores neither ciphertext nor keys. Acceptance leaves the
physical attempt RESERVED. Terminal settlement commits before the exact native
release ACK; a lost ACK reply can replay the same release without new RF or
settlement. Read-only STATUS must confirm no pending, retained, active or
containment work before successful closure.

| Evidence | What it establishes |
| --- | --- |
| `QUEUED` | Charged carrier acceptance; no RF completion |
| `ISSUED` / `STARTED` | Driver handover/start; missing later evidence cannot prove unsent |
| `RF_DONE` | Exact successful local TX_DONE evidence; no authenticated peer receipt |
| `NOT_SENT` | Definite pre-issue cancellation/expiry; debt remains |
| `UNKNOWN` | Unresolved physical operation; sticky fence and checked containment |

Per-role `operation_limits` participate in the grant digest independently of
absolute MCU boot-counter caps. The example and flow adapter reject a journal
that exceeds its admitted operation limits before RF work. This keeps a new
same-boot journal from treating old native counter headroom as a fresh paid
allowance. Cold planning must check native headroom, incremental journal costs
and the unchanged conservative scheduler budget separately.

Physical records survive peer ACKs and transport retirement. Same pending DATA
is suppressed by flow/generation/sequence domain and exact plaintext digest
before another AEAD seal. Ambiguous writes and recovered RESERVED attempts stay
charged and become UNKNOWN/fenced. Later physical evidence can assist cleanup;
it cannot upgrade recovered UNKNOWN, lift the fence or resume ciphertext. Shared
MCU boot debt, mutation highwater and clock watermark survive queue/context
changes. A physical reset or flash still needs separate debt carry and outcome
reconciliation.

## Flow timing and qualification

The [30 October local host profile](../benchmarks/2026-10-30-host-journal/README.md)
found history-length-dependent journal scans on the flow's accounting path.
The unreleased journal change maintains durable totals in each reservation's
existing FULL transaction and reconstructs them from retained evidence on open.
It removes the repeated scans without changing radio or listening policy. The
profile and retained-trace analysis are source/local evidence; the latest
physical comparison remains the 24 October pair above.

The frozen flow modules are unchanged. Their generic `ACCEPTED` result means
carrier disposition. The opt-in adapter uses it after exact queue acceptance;
the synchronous path still returns it after RF_DONE. No second handover callback
occurs on physical completion. Queue-aware retry bounds are constructed before
the authority and scheduler, while ACK coalescing retains its separate original
PHY timing. Physical backlog contributes conservative queued timing, and a
pending logical DATA cannot be retried. An authenticated peer ACK alone cannot
close outstanding physical work.

Source tests exercise the real session cipher, frozen scheduler, journal and
serial worker against a modeled MCU, as well as native queue/adapter paths. They
cover four-item handover, reordered or missing evidence, peer receipt before
physical completion, replay, cancellation, ambiguous writes and durable drain.
Those source controls remain separate from the finite installed-image and RF
comparison above. They supply no additional sustained-capacity, range or
energy measurement.

The successful comparison explicitly admitted the short autonomous-handover
policy within a fresh finite scope: accepted DATA may start before its fixed
deadline after host revocation or USB loss. Instantaneous disconnected
cancellation is therefore not promised. Future hardware work still needs its
own finite authority, preserved attempt/airtime debt and boot-bound clock-rate
observations. Next measurements should repeat and reverse the mode order, then
exercise loss, disconnect and recovery while keeping authenticated receipt,
physical settlement and independent content verification separate.
