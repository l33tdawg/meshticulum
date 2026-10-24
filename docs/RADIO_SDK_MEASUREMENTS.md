# Public SDK radio measurements: 17–24 October 2026

The latest [host-backed MCU queue comparison](#host-backed-mcu-queue-comparison-24-october-2026)
verified two fresh 8,164-byte files over an approximately 20 m LoRa link.
Startup-inclusive goodput rose from **931.3 to 1,084.8 B/s (+16.48%)** in one
baseline-then-queue pair. Its exact source, boundaries and retained failures are
separate from the earlier GFSK and LoRa samples below.

The public SDK completed **two fresh 100,000-byte files at 2.03 and 1.95 kB/s (16.24 and 15.59 kbit/s)** of useful application throughput, from each file's first sender startup through its final authenticated receipt. Both five-part files and their publishers were independently verified. A separate **23,900-byte file reached 2.53 kB/s (20.28 kbit/s)** at the same timing boundary.

The three samples above used source `da247a1bb74601973f0bb72f51c268a412714719`, an Orange Pi 5 Plus SX1262 HAT sender and a Heltec V4 MCU receiver about **one metre apart**. The modem used **GFSK at 250 kbit/s**. These are direct, near-bench GFSK results; they provide no LoRa-modulation, multihop or field-range measurement. No new firmware was flashed for these samples.

The tested [`Gfsk250Profile`](https://github.com/l33tdawg/meshticulum/blob/da247a1bb74601973f0bb72f51c268a412714719/src/meshticulum/bench/gfsk250_profile.py) used **921,375,000 Hz**, **0 dBm configured chip power**, a **250,000 bit/s** rate, **62,500 Hz** frequency deviation, **467,000 Hz** receive bandwidth and a **32-bit** preamble. Configured chip power is not a measurement of EIRP; measured EIRP remains unavailable.

## Larger public files

On **18 October 2026**, source `1f85608e2c5e543130673529e21d838917094c11`
completed fresh whole-file transfers through the same public signed-bundle path
and hardware. These use 11- and 21-part geometries and their own original
520- and 720-second windows. Each input, publisher key, grant set and receiver
state was fresh.

| Useful file | Parts | First sender startup → final authenticated receipt | First DATA → final receipt | Scope issue → both endpoints closed |
| ---: | ---: | ---: | ---: | ---: |
| 250,000 bytes | 11 | 118.420015 s; **2,111.1 B/s (16.889 kbit/s)** | 117.382857 s; 2,129.8 B/s (17.038 kbit/s) | 121.214435 s; **2,062.5 B/s (16.500 kbit/s)** |
| 500,000 bytes | 21 | 225.668497 s; **2,215.6 B/s (17.725 kbit/s)** | 223.949464 s; 2,232.6 B/s (17.861 kbit/s) | 228.292237 s; **2,190.2 B/s (17.521 kbit/s)** |
| 1,000,000 bytes, original | 42 | 571.484490 s; 1,749.8 B/s (13.999 kbit/s) | 570.384109 s; 1,753.2 B/s (14.026 kbit/s) | 573.955922 s; 1,742.3 B/s (13.938 kbit/s) |
| 1,000,000 bytes, CPU-isolated | 42 | 448.473933 s; **2,229.8 B/s (17.838 kbit/s)** | 447.266757 s; 2,235.8 B/s (17.886 kbit/s) | 450.903081 s; **2,217.8 B/s (17.742 kbit/s)** |

The 250 kB receiver independently verified all 11 parts and the complete file.
All 22 endpoint processes exited zero and all 11 authenticated receipt
confirmations were accepted. It used 372 receiver attempts and 1,353 sender
attempts, with one DATA retransmission and no new raw receive drops.

The 500 kB receiver independently verified all 21 parts and the complete file.
All 42 endpoint processes exited zero and all 21 authenticated receipt
confirmations were accepted. It used 736 receiver attempts and 2,700 sender
attempts, with one DATA retransmission and one new raw receive drop. The
original controller stopped at that diagnostic counter change only after the
final authenticated receipt and whole-file verification had succeeded. The
stopped result is retained; the corrected result follows the existing policy
that records receive drops as diagnostics while requiring exact content,
receipts and settled attempts.

Both 1 MB receivers independently verified all 42 parts and the complete file.
Both runs had 84 endpoint processes exit zero and all 42 authenticated receipt
confirmations were accepted inside the original 1,140-second window.

The original 1 MB run used 1,530 receiver attempts and 5,400 sender attempts,
with one DATA retransmission and one additional raw receive drop. Its per-part
time averaged 10.97 seconds while the sender host's production LLM engine was
holding CPU4 at roughly 95%. A fresh CPU-isolated control excluded that core
from the benchmark process: per-part time fell to 8.66 seconds, and the
whole-file rate rose from 1,749.8 to **2,229.8 useful B/s**, clearing the owner's
2.0 kB/s floor. The control used 1,471 receiver attempts and 5,400 sender
attempts, reserving 5,873,703 and 56,270,665 microseconds of estimated airtime
respectively, with two DATA retransmissions and two additional raw receive
drops. The MCU advanced from 4,584 to 6,055 attempts and from 14,019,936 to
18,021,056 reserved-airtime microseconds; firmware, boot identity and USB
identity were unchanged.

### Where the 1 MB time went

The whole-file interval is not only radio payload time. In the original run,
the sum of the 42 per-part sender-startup-to-receipt intervals is
**460.672429 s**; the remaining **110.812061 s** is coordination between parts.
The first six parts averaged **8.590 s** each; parts 6–41 averaged **11.365 s**
each while CPU4 was held by the production LLM engine.

The data-only interval, from the first DATA-worker call to the final receipt,
was **398.221307 s**, or **2,511.2 B/s (2.5112 kB/s; 20.089 kbit/s)**. The
equivalent data-only rates for the 250,000- and 500,000-byte files were
**2,938.4 B/s** and **3,303.1 B/s**.

With the sender-side benchmark excluded from CPU4, the fresh 1 MB control
completed at **448.473933 s** through authenticated receipt and **450.903081 s**
through endpoint closure. Its 42 part intervals summed to **363.705645 s** and
the remaining interval between parts to **84.768288 s**. This is
**2,229.8 B/s whole-file** and
demonstrates that the file path itself clears 2.0 kB/s under the declared
near-bench conditions. Warm workers and fewer process restarts remain useful
headroom but are no longer required to cross the floor.

The exact parts, confirmations, endpoint exits and timing arithmetic are in the
[sanitized larger-file record](../benchmarks/2026-10-18-public-sdk-large-files/results.json).

These larger-file results remain direct near-bench GFSK250 measurements. They
do not establish LoRa modulation, field range, sustained-capacity distribution,
or operation on another installation.

## Matched SF7 and SF8 LoRa profiles

On **18 October 2026**, source
`f8952155146f821be1eefebd10664141484a22b9` completed two matched-profile
campaigns on the same direct near-bench path. Each campaign used three fresh
8,164-byte payloads at both SF7/BW500 and SF8/BW500. Both profiles used 500 kHz
bandwidth, CR4/5, 921.375 MHz, 0 dBm chip power, an eight-symbol preamble and
sync word `0x42`. All 12 receiver chunks and all 12 authenticated receipts
verified.

| Run | SF7 sender ARM → receipt, median | SF7 useful rate | SF8 sender ARM → receipt, median | SF8 useful rate | SF8 / SF7 |
| --- | ---: | ---: | ---: | ---: | ---: |
| **Combined, six pairs** | **7.404932 s** | **1.102508 kB/s (8.820067 kbit/s)** | **11.340178 s** | **0.719918 kB/s (5.759345 kbit/s)** | **1.531×** |
| Final campaign, three pairs | 7.285935 s | 1.120515 kB/s (8.964121 kbit/s) | 11.316693 s | 0.721412 kB/s (5.771297 kbit/s) | 1.553× |
| Control campaign, three pairs | 7.648962 s | 1.067334 kB/s (8.538675 kbit/s) | 11.363662 s | 0.718430 kB/s (5.747443 kbit/s) | 1.486× |

At the receiver ARM-to-verified boundary, the combined medians were
**8.600729 s at SF7 (949.222 B/s)** and **12.157059 s at SF8 (671.544 B/s)**.
Including sender process startup through receipt, the combined medians were
**7.675360 s** and **11.602588 s**. The control campaign was within 4.7% of the
final campaign's SF7 rate and 0.4% of its SF8 rate.

The final campaign used 85 receiver attempts and 264 sender attempts; the
control used 87 and 264. Across both, the receiver journals total **172
attempts** and the sender journals **528**, with no fenced or
recovered-unknown attempts. The pre-campaign MCU status was boot generation 39
with **208 attempts** and **9,156,480 microseconds** of reserved estimated
airtime. The read-only post-campaign status is the same boot, **380 attempts**
and **16,719,744 microseconds**, disarmed with zero receive drops. The
172-attempt advance matches the journals.

The public LoRa admission caps cumulative boot attempts at **1,024**. The
baseline left 816 attempts; the control used 87, leaving 729 for the final
campaign; the final used 85, leaving **644 attempts of headroom**. The
reserved-airtime counter advanced by 7,563,264 microseconds, leaving
43,280,256 under the campaign's 60,000,000-microsecond profile cap. The
receiver journals' reservation sum is 8,491,660 microseconds; the firmware
counter and journal reservations use different accounting bases, and neither is
an RF-energy measurement.

An earlier setup in the campaign rebooted the MCU from boot generation 38 to 39
without a logged USB reset; the cause remains unknown. Both complete campaigns
are continuous within boot generation 39.

The [sanitized comparison
record](../benchmarks/2026-10-18-lora-sf7-sf8/results.json) retains every
payload hash, timing boundary, attempt journal and the post-campaign status.
This is a finite near-bench comparison, not a completion distribution or a
field-range measurement. The one-chunk harness verifies the exact resource
bytes but does not invoke whole-file assembly, and no RF airtime, energy or EIRP
was measured.

## Useful bytes and elapsed time

Rates use decimal bytes: **1 kB = 1,000 bytes**. Multiply bytes per second by eight for bit rates. The numerator is the original decoded file length, excluding manifests, framing and encoded representation bytes. The elapsed intervals include transport overhead, host work, ACKs and any retries.

| File and receiver retirement | Timing boundary | Elapsed seconds | Useful decoded B/s |
| --- | --- | ---: | ---: |
| 23,900 bytes; default 20-second grace | Sender startup → authenticated receipt | 9.429571 | **2,534.6** |
| Same file | First DATA-worker call → authenticated receipt | 8.168009 | 2,926.0 |
| Same file | Scope issue → both endpoints closed | 34.064723 | 701.6 |
| 100,000 bytes, run 1; authenticated receipt confirmation | First sender startup → final authenticated receipt | 49.255870 | **2,030.2** |
| Same five-part file | First DATA-worker call → final authenticated receipt | 48.154962 | 2,076.6 |
| Same five-part file | Scope issue → both endpoints closed | 53.879126 | **1,856.0** |
| 100,000 bytes, run 2; authenticated receipt confirmation | First sender startup → final authenticated receipt | 51.302845 | **1,949.2** |
| Same second file | First DATA-worker call → final authenticated receipt | 50.360284 | 1,985.7 |
| Same second file | Scope issue → both endpoints closed | 56.957045 | **1,755.7** |

For the two 100 kB files, the exact sender-start rates were **2,030.2 and 1,949.2 B/s**, or **2.0302 and 1.9492 kB/s**, equivalent to **16.2416 and 15.5936 kbit/s**. Scope-to-closure rates were **1,856.0 and 1,755.7 B/s**, or **1.856 and 1.7557 kB/s**, equivalent to **14.848 and 14.0456 kbit/s**. The 250 kbit/s figure above is the modem link rate; these lower rates are useful application bytes after transport, encryption, verification and retry costs.

The 100 kB intervals span all five sequential parts, including coordination between them. They are whole-file elapsed intervals, rather than sums of selected busy periods. Scope-to-closure includes grant delivery, endpoint startup, coordination, verification and endpoint cleanup. Offline file generation, signing, build and staging happened before scope issue and are excluded from this interval; it does not measure a complete cold deployment. The 23,900-byte sample keeps the default receiver grace: its 20-second wait appears in scope-to-closure time, after the sender has received its authenticated ACK.

## Completion and receiver confirmation

Each 100 kB file used four 23,900-byte parts and one 4,400-byte part. Both endpoints enabled `--confirm-receipt`: after accepting the final flow ACK, the sender transmits an authenticated confirmation that lets the receiver close its ACK grace early. **All ten receiver confirmations were physically accepted across the two files.** Missing or invalid confirmation still leaves the original 20-second fallback; the option does not extend a lease, reset a deadline or refund attempt debt. See the [bundle example](../examples/BUNDLE.md#optional-sender-receipt-confirmation).

The first 100 kB run had **zero DATA retransmissions**; the second had **one**. Both had **zero new receiver drops**. All twenty endpoint children exited successfully, with no forced cleanup and unchanged services and boot identity. The smaller sample had one DATA retransmission and zero new receiver drops. Publisher and whole-file verification were required separately from sender receipt. The two 100 kB inputs have different SHA-256 identities, recorded in the public JSON. Each receiver began with fresh verified state. The common staging pack contained source chunks on both hosts, but receiver acceptance used the bytes delivered through `ServiceTransfer`, publisher verification and whole-file hashing; staged source chunks were not used to accept the received file. Verified earlier parts were reused only within each file's own original window.

## Clean installation and verified-piece continuation

A separate fresh **100,000-byte file completed from the published wheel** on the same near-bench GFSK250 path. The receiver used Python **3.11.2** and the sender **3.14.4**, both Linux aarch64. Each normal virtual environment imported the installed distribution in isolated Python mode, with no editable checkout, SDK zip or system site packages. The check matched all **67 runtime files** to the wheel from source `673eb60d24dc60f67371e24bc5c9caeec3a68508`. Its wheel SHA-256 is `dce5127fed0956e5b3d2cd9ce7dd859a2a4f6af8cdd491bfaf9ba5723e495c1c`.

The environments used hash-pinned wheels for `cryptography 50.0.2`, `PyNaCl 1.6.2`, `cffi 2.1.1` and `pycparser 3.11`. Installation and dependency acquisition finished before the radio window. These dependency versions differ from the earlier source-staged samples; the installed check has its own record.

After part 0 was verified and both endpoint processes had closed, the coordinator paused for **5.010819 seconds on its monotonic clock**, then opened fresh processes for parts 1–4. The receiver reopened and checked the verified prefix, reusing four earlier parts by final assembly. All work retained the original grants and **240-second window**, with no deadline extension or attempt refund. This demonstrates continuation from a completed verified piece. It does not demonstrate resuming an in-flight session after a crash.

| Installed-wheel whole-file boundary | Elapsed seconds, including the pause | Useful decoded B/s |
| --- | ---: | ---: |
| First sender startup → final authenticated receipt | 57.475095 | **1,739.9** |
| First DATA-worker call → final authenticated receipt | 56.690410 | 1,764.0 |
| Scope issue → both endpoints closed | 61.983317 | **1,613.3** |

That installed-wheel file delivered **1,739.9 B/s**, or **1.7399 kB/s and 13.9191 kbit/s**, from sender startup through authenticated receipt. Its scope-to-closure rate was **1,613.3 B/s**, or **1.6133 kB/s and 12.9067 kbit/s**.

All five authenticated receiver confirmations were accepted. The file had one DATA retransmission and zero new raw receive discards. All ten endpoint children exited successfully with no forced cleanup; owned endpoints were closed and disarmed, with services and host lifetimes unchanged. Separate reviews checked the signed publisher manifest, reconstructed file, installed runtime files, original window, journals and endpoint captures. The recorded remote final-file readback matched the input. These checks do not independently reverify raw RF ciphertext, which was not retained; the exact SDK receipts and matching packet hashes provide the RF authentication evidence. Owned-endpoint cleanup does not establish global quiescence.

An earlier installed-wheel trial stopped after **four verified parts / 95,600 bytes** when a harness condition required no change in the raw receive-discard counter; that counter rose from two to three. All eight started children exited successfully, but the fifth part never started and no whole-file completion or rate is claimed. The harness now records discard changes as diagnostics while still requiring authenticated receipts, publisher/hash checks, settled journals and the original window. The successful run used a fresh file and window; the earlier attempt debt remains retained.

[The sanitized installed-SDK record](../provenance/PUBLIC_INSTALLED_SDK_PROOF.json) contains the package and dependency hashes, timing arithmetic, continuation conditions and earlier incomplete attempt. It is separate from the two uninterrupted files in the headline.

## A/B cross-room transfer: 22 October 2026

One fresh Level 4 run used **Unit B as sender and Unit A as receiver**, both
Heltec LoRa 32 V4 boards running firmware `mtb1-020e287255`, approximately
**20 metres apart in separate rooms**. The distance is operator-reported and was
not independently measured. Both endpoints used the published wheel
`dce5127fed0956e5b3d2cd9ce7dd859a2a4f6af8cdd491bfaf9ba5723e495c1c`
from source `673eb60d24dc60f67371e24bc5c9caeec3a68508`.

The transfer delivered **100,000 decoded bytes** in five signed parts with
whole-file SHA-256
`65d19fca4bd31ce9db664e4d0457b1d677132d4c4463d5aeb5c40352170938f1`.
All five authenticated peer receipts and the final whole-file check passed.

| Boundary | Result |
| --- | ---: |
| First sender startup → final authenticated receipt | 56.311923 s; **1,775.822857 B/s (1.776 kB/s)** |
| First DATA-worker call → final authenticated receipt | 55.554563 s; **1,800.032159 B/s** |
| Scope issue → both endpoints closed | 71.051173 s |
| Receiver DATA RSSI | **-57.5 to -56.5 dBm**; median -57.0 dBm across 539 frames |
| MCU receive-drop delta | A **0**; B **0** |

All ten endpoint processes exited zero with no forced cleanup. Attempt journals
were complete and unfenced: A used 162 attempts / 646,866 microseconds; B used
544 attempts / 5,659,198 microseconds. Tracked service lifetimes and endpoint
boot identities were unchanged.

Unit B was first updated app-only from `mtb1-9816f92488` to the current
`mtb1-020e287255` image
`eca7772095cc9c9e63c9f6e6d9fe48ba3c376cd6bfef557ffcc441621a846fa2`.
That firmware image was reproduced from source
`c8957e56b017ecd42486acc2c8a46c24cb238d2d`, separate from the installed
wheel source `673eb60`.
The exact prior 393,216-byte application range was retained for rollback with
SHA-256
`b143884e63e7ad3694a72de8beff241c8b9918618bc91bfbab8a04caf2d71059`.

An earlier A/B scope failed before any DATA transfer because endpoint-specific
boot ceilings produced different cryptographic wire profiles. That consumed
scope is retained and is not a pass. The successful run used one identical wire
profile while checking each endpoint's independent boot baseline.

The harness also allowed the exact GFSK250 USB serial sender rather than only
the existing Linux HAT sender. The published wheel remained exact; this narrow
example adaptation is not part of the wheel and does not change protocol code.

[The sanitized A/B result](../benchmarks/2026-10-22-ab-cross-room/results.json)
retains the verified hashes, timing, attempts, RSSI samples and firmware
rollback boundary. This is one cross-room GFSK250 sample. It does not establish
field range, sustained capacity, LoRa-profile behavior, another installation or
a completion distribution.

## A/B LoRa distance sample — 24 October 2026

One fresh **8,164-byte signed file** passed from a Unit B Heltec V4 USB radio
to a Unit A Heltec V4 USB radio at an operator-reported **approximately 20 m
across two rooms**, with antennas fitted to both. Unit C was excluded.
Both coprocessors reported `mtb1-020e287255`. The profile was SF7/BW500,
CR4/5, 921.375 MHz, 0 dBm, preamble 8 and sync word `0x42`.

| Boundary | Duration | Useful throughput |
| --- | ---: | ---: |
| Sender startup → authenticated peer receipt | 9.183714 s | **0.889 kB/s** |
| First DATA-worker call → authenticated peer receipt | 8.001248 s | 1.020 kB/s |
| Scope issue → both endpoints closed | 16.681269 s | 0.489 kB/s |

The receiver independently verified the publisher and whole-file hash. All 46
DATA frames arrived without retransmissions; both receive-drop deltas were
zero, both child processes exited zero and cleanup needed no forced action.
Receiver DATA RSSI was -74 to -70 dBm, median -73; SNR was 10 to 12.2 dB,
median 11. The startup-inclusive result is **below the 1 kB/s target**.

The installed wheel was built from source `b5fd674b20fc5d0416fc461dcd562731d0faf0b7`
with SHA-256 `9f67613630aae4d781894d1886b6254d8c929fd97e473caa2f350ce09a57ecb5`.
The campaign adapted the example's USB sender guard and accepted Unit B's
preflight-observed firmware identity; source-manifest equivalence for that
identity was not proved by this run. The earlier wheel-staging failure,
expired unconsumed scope and pre-RF LoRa-admission failure remain recorded.

[The sanitized result](../benchmarks/2026-10-24-lora-distance/results.json)
retains exact timing arithmetic, paid debt, signal summaries, adaptations
and failures. It also separates a later CPU-only journal diagnostic from RF
evidence. DATA worker intervals summed to 6.134363 s and coprocessor driver
intervals to 4.984246 s; the 1.150117 s difference needs further attribution.
Driver intervals include driver work and do not independently measure RF airtime.
The sender's CPU-only median reservation and settlement times were 0.460 and
0.419 ms respectively. These observations motivate the
[host-backed MCU acceleration work](MCU_ACCELERATION.md). This earlier sample
does not measure a queue speedup; the later pair below has its own source and
measurement pins. This single sample does not establish sustained
capacity, field range or a completion distribution.

## Host-backed MCU queue comparison: 24 October 2026

A fresh finite comparison ran the synchronous baseline first and MCU queue
mode second on two Heltec V4 USB radios, B sending to A, at the same
operator-reported approximately **20 m** placement. Both endpoints used
`mtb1-6cfde1fdd4`, SF7/BW500/CR4:5, preamble 8, sync `0x42`, 921.375 MHz and
0 dBm. Each mode sent a fresh **8,164-byte useful file**, represented by an
8,827-byte signed response. Each required 46 DATA packets and one authenticated
receipt-confirmation CONTROL packet; the receiver sent 18 ACK packets.

| Timing boundary | Baseline | Queue mode |
| --- | ---: | ---: |
| Sender startup → authenticated receipt | 8.766668 s; **931.254611 B/s** | 7.526076 s; **1,084.761833 B/s** |
| First DATA worker call / queued submission → receipt | 8.013809 s; **1,018.741525 B/s** | 6.900232 s; **1,183.148625 B/s** |
| Receipt → drained endpoint complete | 3.329 ms | **27.456 ms** |
| Receipt → sender CLI complete | 129.734 ms | 178.999 ms |
| Scope issue → both endpoints closed | 20.301626 s | 21.350294 s |

Startup-inclusive useful goodput improved **16.483915%**, with 14.151237% less
elapsed time. DATA-boundary goodput improved **16.138254%**. That boundary
starts at a host worker call or queued submission, not an independently
measured RF start. Authenticated receipt is observed immediately after ACK
application, before the physical drain gate. Scope-to-closure also includes
management, startup and cleanup; queue mode did not improve that boundary in
this pair.

Both whole files and their publishers verified. Both runs had zero DATA
retries, new receive drops or new UNKNOWN outcomes, all four endpoint children
exited zero and no forced cleanup was needed. Queue mode retained 46 each of
QUEUED, ISSUED, STARTED and RF_DONE events, followed by 46 exact result releases;
no physical records remained at closure. The pinned completion gate checked
drained STATUS; its raw reply and RX-rearm timestamps were not independently
retained. Historical UNKNOWN references and
paid costs stayed retained and are not claimed resolved by these successes.

The MCU driver totals for DATA were **4.983012 s baseline** and **4.989588 s
queued**. Baseline DATA worker calls totaled **6.156631 s**, including the
4.983012 s driver total. Their 1.173619 s difference includes host/transport
work and is not CPU-only time. Queue acceptance calls totaled **1.118990 s**
while MCU RF work continued; acceptance and driver totals overlap and must
not be added. The result is consistent with reducing serial handoff delays
while preserving the same radio profile. Independent RF airtime, energy and
EIRP were not measured.

The tested SDK source was `f06ebeb3e687da4688657311830e95326a0107d2`, with wheel
SHA-256 `7998a705ab6a5641ecb9191f08e9b63ba7a676612dcfa7455e291e6177149ad0`.
This measured wheel identifies as `1.0.0a1`. Any future `1.0.0a2` build has a
separate artifact identity and requires its own checks.

The rollout history remains part of the record. The first deployment attempt
timed out and both units were verified restored to the prior image. A later
installation succeeded, but its baseline exhausted a 10,000,000 µs virtual
pacing budget after 45 DATA frames, before the final DATA frame; queue mode
was not attempted under that failed scope. Frozen accounting intentionally
adds settled estimates to sampled worker usage. The failure led to cold-plan
checks, an explicit exhaustion diagnostic and grant-bound per-operation
journal limits independent of absolute MCU boot caps. The next fresh pair
passed while retaining earlier debt and the failed ledger's sticky fence.

[The sanitized comparison](../benchmarks/2026-10-24-mcu-queue-lora/results.json)
retains exact timing, accounting, source pins and failures. The queue is
explicit opt-in; Linux retains keys, AEAD, ACK authentication, credit, retries
and verification. This single fixed-order pair does not establish sustained
capacity, a completion distribution, loss/disconnect recovery, field range,
energy use or a MeshCore/Reticulum speed ranking.

## What changed, and what remains

The SDK now checks fresh kernel open-event evidence alongside descriptor, inode, mount and ownership bindings, avoiding repeated scans of every process descriptor during normal packet work. Verified `nodev` IPC namespaces allow ordinary shared-memory file churn without invalidating device ownership. Initial collision scans, existing-alias watches and fault refusal remain. Those earlier samples measure host-side changes. The later MCU comparison
above holds the firmware/profile fixed and measures the opt-in handoff path.

The earlier GFSK startup-inclusive samples exceed the **1 kB/s (8 kbit/s) alpha floor for their stated path**. The installed-wheel run adds a real installation and verified-piece continuation check. These finite files do not establish a sustained-throughput or completion distribution or a guarantee on another installation. The selected SDK/source-build artifacts are published as the [`v1.0.0-alpha` prerelease](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha) from source `673eb60`. Native/private integration, wider recovery and security qualification remain separate programme work.

Measured RF airtime, energy, EIRP and field range remain unavailable. GFSK results do not replace the [earlier LoRa measurements and scoped upstream comparisons](PERFORMANCE.md). No general speed or security ranking against MeshCore or Reticulum follows from these samples.

## Inspect the published record

[Sanitized results and source-capture hashes](../benchmarks/2026-10-17-public-sdk/results.json) contain the measured fields needed to check the arithmetic. The operator captures stay in the lab; hashes and an allowlisted transcription do not independently attest the hardware. The public record excludes grants, addresses, device identifiers, boot values and operational logs.

```sh
python tools/verify_benchmark_report.py benchmarks/2026-10-17-public-sdk/results.json
python tools/verify_benchmark_report.py provenance/PUBLIC_INSTALLED_SDK_PROOF.json
```

See the [roadmap](ROADMAP.md) for the next work and [release readiness](RELEASE_READINESS.md) for the remaining gates.
