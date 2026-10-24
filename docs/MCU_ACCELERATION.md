# Host-backed MCU acceleration

The source candidate now connects a bounded MCU DATA ciphertext queue to the
serial worker, durable attempt journal and authenticated `BenchFlow` runtime.
The firmware includes the adapter and starts with its queue closed. Existing
plans use the synchronous path; an immutable grant record must opt into bounded
queue handover. Linux retains session keys, AEAD, peer ACK authentication,
credit, retries and application verification.

This is a functional **source candidate**, with host/native tests and a Heltec V4
build (`mtb1-6cfde1fdd4`). Two independent preparations on the same pinned
macOS-arm64 host produced identical application BIN and ELF bytes. The
[build record](../provenance/MCU_BURST_FIRMWARE.json) binds all six application
inputs, recipe, lock and output hashes; the
[verifier](../tools/verify_burst_firmware.py) checks that source/provenance binding.
This is same-host reproduction, without cross-host, deployment or RF qualification.
The [serial and admission contract](MCU_BURST_PROTOCOL.md) defines the exact
policy, records, clock assumptions and closure requirements.

## Measured starting point

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

## What the candidate implements

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

Physical records survive peer ACKs and transport retirement. Same pending DATA
is suppressed by flow/generation/sequence domain and exact plaintext digest
before another AEAD seal. Ambiguous writes and recovered RESERVED attempts stay
charged and become UNKNOWN/fenced. Later physical evidence can assist cleanup;
it cannot upgrade recovered UNKNOWN, lift the fence or resume ciphertext. Shared
MCU boot debt, mutation highwater and clock watermark survive queue/context
changes. A physical reset or flash still needs separate debt carry and outcome
reconciliation.

## Flow timing and qualification

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
They do not qualify an installed image or prove faster delivery, sustained
capacity, range or energy use.

Exact candidate wheel/source artifacts and a concrete rollback/deployment scope
precede hardware work. The owner must explicitly accept the short autonomous-handover policy in
that fresh finite scope: accepted DATA may start before its fixed deadline after
host revocation or USB loss, so instantaneous disconnected cancellation is not
promised. Preflight must preserve preflash attempt/airtime debt and retain
boot-bound clock-rate observations. Compare legacy and queue modes on the same
rebuilt firmware with fresh bounded grants, authenticated receipts and independent
content verification before making any acceleration claim.
