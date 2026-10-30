# Roadmap

Meshticulum is a public experimental transport and resource project with a published source-build alpha. The active SF5/BW500 goal is repeated reliable completion at **3 kB/s (24 kbit/s)** of useful encrypted file bytes, measured from sender process startup through authenticated receipt with publisher and whole-file verification. **Above 2.8 kB/s (22.4 kbit/s)** remains the net-win checkpoint. Neither is established by the latest physical trial. The longer-term **4 to 5 kB/s (32 to 40 kbit/s)** goal follows that qualification. There are no promised release dates.

## Current status

The first [eight-frame SF5 profiling sample](../benchmarks/2026-10-30-sf5-eight-profile/README.md)
verified 23,900 useful bytes at 801.424349 B/s including startup. Both target
builds passed and both Heltec installations have live memory observations.
The [matched R11 sample](../benchmarks/2026-10-30-sf5-r11/README.md) delivered the
same useful bytes at 1,053.284286 B/s in 22.690930 seconds. Loss differed, so the
pair does not isolate the retry change. Recovery stalls remain; the 3 kB/s goal
is open. The [first logical-window32 sample](../benchmarks/2026-10-30-sf5-window32-profile/README.md)
then verified the same useful bytes at 1,276.917174 B/s in 18.716954 seconds,
keeping the physical train at eight. Its loss pattern also differed. Further
work targets per-repair control holds and completion/retry delays.

The [public repository](https://github.com/l33tdawg/meshticulum) and [project website](https://l33tdawg.github.io/meshticulum/) accompany the [experimental `1.0.0-alpha.3` prerelease](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha.3). The release provides the SDK wheel, normalized source archive and firmware source-build materials. [Release status](RELEASE_READINESS.md) records the exact artifact checks and hardware scope. You can [install from source](../README.md#install-and-explore) or review the release assets.

Alpha.3 removes repeated full-history scans from journal accounting and adds
[opt-in host phase timings](HOST_PROFILING.md) for queue, serial, journal and
flow-loop work. Serial writes recheck their original deadline after writable
readiness. The [local scaling profile](../benchmarks/2026-10-30-host-journal/README.md)
is source-host evidence, separate from the [hardware record](RELEASE_READINESS.md#alpha3-hardware-evidence).
Next work uses those phase measurements to investigate target-host costs while
preserving durable reservations, receive/listening guards and retained debt.

Post-alpha.3 source now has [reviewed SF5 selective recovery](../benchmarks/2026-10-30-sf5-recovery/README.md).
A finite 2 kB hardware file exercised one repair 16.725 ms after selective ACK
proof and completed in 2.554067 seconds including process startup (0.783065 kB/s).
The next performance work targets partial-train ACK waits and host/setup costs,
then repeats identical workloads on SF5 and SF7. Small-file goodput does not
establish a profile comparison or the 2–3 kB/s target. Published release assets
remain unchanged.

The [throughput pipeline work](THROUGHPUT_PIPELINE.md) adds bounded host
readiness wakeups and a dormant, finite MCU batch prototype. The prototype
validates a complete ciphertext vector before committing its native charge;
production flow-owner, journal, serial and firmware integration remains open.
The four-frame ideal of about 2.96 useful kB/s excludes host work, startup and
losses. It is a calculation, not a physical result or evidence that the new
target has been met.

The earlier measured SDK source, `da247a1`, has [passing source CI on Python 3.11, 3.12 and 3.13](https://github.com/l33tdawg/meshticulum/actions/runs/39028024473). The published documentation head passed the same source-control matrix with unchanged runtime and firmware. The hardware samples below supply separate physical evidence. Future release candidates need their own exact source checks and artifacts.

On **24 October 2026**, a fresh fixed-order LoRa comparison at approximately
20 m verified 8,164 bytes in each mode. The opt-in MCU DATA queue raised
sender-start-to-authenticated-receipt goodput from **931.255 to 1,084.762 B/s
(+16.484%)**, with zero retries, receive-drop increases or new UNKNOWN outcomes.
Both modes used the same Heltec firmware and SF7/BW500 profile. All queued
physical results were settled and released; checked endpoint completion followed
receipt by 27.456 ms. This is one pair, with repeated performance, loss and
recovery qualification still open. The [sanitized comparison](../benchmarks/2026-10-24-mcu-queue-lora/README.md)
records the measured source and wheel separately from the published `1.0.0a2`
assets. Runtime correspondence and clean installation passed; this rebuilt wheel
has no fresh RF execution.

Alpha.2 adds the default-closed queue adapter, durable physical-result
tracking, fail-fast budget diagnostics and independent per-operation journal
limits. Keys, authenticated ACKs, credit and retries remain on the host. The
release passed its artifact and release-readiness checks. The
[release record](../provenance/ALPHA2_RELEASE.json) binds the selected assets;
original `v1.0.0-alpha` assets remain unchanged.

The optional [post-quantum source prototype](POST_QUANTUM_PROTOTYPE.md) adds hybrid key establishment and dual signatures without changing current radio, TCP or envelope formats. Adoption requires independent protocol review, persistent replay and key lifecycle integration, and actual device measurements of setup cost, completion and goodput. Its source controls and calculated fragment counts do not qualify a post-quantum network.

On **17 October 2026**, the public SDK completed **two fresh 100,000-byte files in five signed parts each**, with independent publisher and whole-file verification. A separate fresh **23,900-byte file** completed at the same source, `da247a1bb74601973f0bb72f51c268a412714719`. All three used GFSK at 250 kbit/s, from an SX1262 HAT to a Heltec V4 MCU about one metre apart.

| Whole-file boundary | 23,900 bytes: useful B/s | 100 kB run 1: useful B/s | 100 kB run 2: useful B/s |
| --- | ---: | ---: | ---: |
| First sender startup → final authenticated receipt | **2,534.6** | **2,030.2** | **1,949.2** |
| First DATA-worker call → final authenticated receipt | 2,926.0 | 2,076.6 | 1,985.7 |
| Scope issue → both endpoints closed | 701.6 | **1,856.0** | **1,755.7** |

The two 100 kB sender-start rates were **2.03 and 1.95 kB/s**, equivalent to **16.24 and 15.59 kbit/s**. Their scope-to-closure rates were **1.86 and 1.76 kB/s**, or **14.85 and 14.05 kbit/s**. These are useful application rates; the modem link ran at 250 kbit/s.

Each 100 kB interval includes all five sequential parts and coordination gaps. All ten receiver confirmations were accepted, and all twenty endpoint children exited successfully. The two files had zero and one DATA retransmissions respectively, with zero new receiver drops. The smaller sample used the default 20-second receiver grace, which appears in its closure time.

On **18 October 2026**, source `1f85608` extended the verified public-file set to **250 kB, 500 kB and 1 MB** using the same direct GFSK250 HAT/MCU path. Final sender-startup-inclusive useful rates were **2.111, 2.216 and 2.230 kB/s (16.889, 17.725 and 17.838 kbit/s)**; scope-to-closure rates were **2.063, 2.190 and 2.218 kB/s (16.500, 17.521 and 17.742 kbit/s)**. The 11-, 21- and 42-part files all passed independent publisher and whole-file verification. [The larger-file measurements](RADIO_SDK_MEASUREMENTS.md#larger-public-files) retain all attempts, the original host-contended 1 MB sample, the CPU-isolated control and the 500 kB diagnostic reclassification.

All three final files clear **2.0 kB/s** on the whole-file boundary. The original
1 MB run reached 1.750 kB/s while the sender's production LLM occupied CPU4;
excluding that core raised the fresh control to **2.230 kB/s**. Warm workers and
fewer restarts remain useful headroom for larger files and the HTTP path.

Two complete campaigns then produced **six matched LoRa pairs** at SF7/BW500
and SF8/BW500 using the same 8,164-byte payloads. Across all six pairs, median
sender-ARM-to-receipt rates were **1.103 and 0.720 kB/s (8.820 and
5.759 kbit/s)**, making SF8 **1.531×** slower on this near-bench link. All 12
chunks and receipts verified. The MCU's 1,024-attempt cumulative cap stood at
380 attempts after both campaigns, leaving **644 attempts of headroom**, on
one boot generation with zero receive drops. [The profile
record](RADIO_SDK_MEASUREMENTS.md#matched-sf7-and-sf8-lora-profiles) retains the
individual samples, the final and control medians, and the debt accounting.

The HTTP gateway now meets a normal **30-second** browser timeout. Three
consecutive `links` requests fetched `example.com` and `example.org` through a
second unit's internet connection and returned them over the radio in
**17.77–17.99 seconds** wall time, with gateway completion at
**16.81–17.08 seconds**. A bounded 3-second startup gap removed the pre-fix
52.8-second startup-race failure. [The HTTP gateway record](HTTP_GATEWAY.md)
keeps the receipts and limits.

**These samples exceed the startup-inclusive alpha floor for the tested path.** Two fresh 100 kB files provide an initial repeat, rather than a sustained-capacity or completion distribution. GFSK is separate from LoRa modulation; the one-metre setup does not measure field range. [The measurement report](RADIO_SDK_MEASUREMENTS.md) records exact durations, source hashes and limits. The [earlier 15 October campaign](PERFORMANCE.md#a-complete-100-kb-file-2026-10-15) remains in the historical record.

A separate [clean installed-wheel check](RADIO_SDK_MEASUREMENTS.md#clean-installation-and-verified-piece-continuation) completed another fresh 100 kB file at **1.74 kB/s (13.92 kbit/s) including startup and a five-second pause between verified parts**, or **1.61 kB/s (12.91 kbit/s) through endpoint closure**. Both normal virtual environments matched all 67 runtime files to release source `673eb60`; all five confirmations and all ten endpoint exits passed under the unchanged original window. The record retains the earlier incomplete 95,600-byte installed trial. This finite check supports the published source-build alpha and does not establish in-flight crash recovery.

On **22 October**, the same installed wheel completed one **A/B cross-room
GFSK250** run with Heltec V4 endpoints approximately **20 metres apart**. The
five-part 100,000-byte file completed at **1.776 kB/s including sender startup**,
with five authenticated receipts, exact whole-file verification and RSSI from
**-57.5 to -56.5 dBm**. Both MCUs recorded zero receive drops and all ten child
processes exited zero. This is a single operator-reported cross-room sample; it
does not establish a completion distribution, sustained capacity or field range.
[The A/B measurement](RADIO_SDK_MEASUREMENTS.md#ab-cross-room-transfer-22-october-2026)
retains the prior failed authentication attempt and firmware rollback boundary.

Earlier native milestones demonstrated [mixed LoRa/IP delivery, loss, verified-piece retention, fresh LoRa resume and return to a locally measured faster IP path](IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return). Their original scope and failed attempts remain part of the record. Current source tests and later firmware builds do not requalify those physical results.

## Published alpha and wider programme

The selected SDK/firmware-source alpha passed the [release-readiness checklist](RELEASE_READINESS.md) and is published. The full programme retains all five workstreams below; native/private qualification belongs to the consuming integration and does not become an advertised SDK capability through this release:

1. **Sustained delivery and reliable completion.** Repeat the new complete public SDK file transfers on the declared supported hardware and profile. Require complete authenticated delivery and independent content verification within the original deadlines. Report DATA, sender-start, request-to-verification and whole-operation timing, with ACK/retry cost and retained failures. Keep unknown outcomes and lifetime attempt/airtime debt; a restart cannot refund or renew them.
2. **A deployable SDK and firmware path.** Qualify the documented public SDK examples on real hardware, including installation, finite grants, lifecycle, disarm and rollback. Preserve exact source/firmware correspondence and supported-device limits. The public SDK now has two fresh 100 kB measurements and a clean installed-wheel 100 kB verified-piece continuation check. The source-build artifacts are published; those finite checks do not qualify every installation, supported mode or recovery path.
3. **Mixed-carrier and native integration.** Retain real radio/IP, loss, verified-piece resume and measured-return coverage against the exact consuming application dependency. Preserve peer, boot, expiry, replay and accounting bindings. Kernel acceptance, RF completion, peer receipt and application acceptance need their own evidence.
4. **Private admission and security.** Complete the inherited native owner, consent, revocation, encrypted-backend, capture and uncertain-write recovery checks in Lantern. Keep durable replay and the experimental forward-session/PFS qualification gates, including key purpose, lifecycle, compromise behavior and native erasure. Classical default transport and static recipient envelopes do not acquire deployed forward secrecy from source tests. See [forward-session status](FORWARD_SECRECY.md) and [security properties and gaps](COMPARISON.md#security-properties-and-gaps).
5. **Reviewed release artifacts.** Bind standalone and paired integration results to the exact candidate, reproduce and inspect the wheel/source distribution, and retain installation checks. Alpha.3 has its own [release record](RELEASE_READINESS.md); earlier alpha tags and assets remain unchanged. Future distributions and paired integrations still need their own firmware source/deployed-image, notices, corresponding-source/relinking and redistribution review.

Compare performance and security with published MeshCore and Reticulum evidence only at stated, comparable boundaries. Keep hardware, profile, payload, encryption, topology and missing measurements visible. Meeting the alpha floor alone does not establish overall superiority; separate upstream benchmark campaigns are not required.

## After alpha

- **Host-backed MCU acceleration.** The [24 October comparison](MCU_ACCELERATION.md#measured-comparison-24-october-2026)
  measured a 16.484% startup-inclusive goodput gain on one fixed-order LoRa pair.
  The bounded DATA ciphertext queue is opt-in; firmware starts queue-closed and
  an immutable grant delegates a short autonomous handover window. The earlier
  rollout failure and under-budget baseline remain retained, with no old fence
  cleared or debt refunded. Next work is reversed-order repeats, loss and
  disconnect recovery, sustained completion and hardware energy measurements.
- **More headroom and broader measurements.** Establish repeated startup-inclusive performance before treating the new finite rates as sustained capacity. Improve startup latency, retry cost, completion consistency and resource use. Expand file sizes, topology, range and power measurements under fresh bounded admission.
- **Repeated-request and warm-path latency.** The normal 30-second browser
  target is met with a bounded cold-start gap. Reduce repeated-request latency
  with persistent workers or pre-staged grants, and extend the measurement to
  more hardware while keeping fresh grants, verification and original
  deadlines.
- **Optional 4 to 5 kB/s (32 to 40 kbit/s) stretch.** Evaluate higher useful encrypted rates on explicitly supported hardware and profiles after the reliability and deployment work. Capability, range and operating cost need new measurements; raw modem bitrate is not application goodput.
- **Richer path selection.** Extend verified-piece reuse and locally measured radio/IP choices. Autonomous routing and MeshCore/Reticulum wire interoperability remain unimplemented; existing finite path demonstrations do not supply those features.
- **Post-quantum feasibility and hybrid research.** Evaluate the separate experimental hybrid prototype's security review, replay/key lifecycle, setup cost, loss recovery and device memory/performance. The default protocols remain classical. PQC is not an alpha prerequisite, a current radio capability or a promised delivery date. [Post-quantum status](POST_QUANTUM.md) records the prototype and its limits.

## Project boundaries

[Meshticulum](https://github.com/l33tdawg/meshticulum) supplies reusable protocols, crypto, drivers, public-resource interfaces, SDK examples and generic radio firmware. [Lantern](https://github.com/l33tdawg/sage-lantern) owns product apps, protected SAGE integration, native policy/provisioning/deployment, and product hardware/enclosure/STL work. The dependency remains **Lantern → Meshticulum**.

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg), within the broader [SAGE project](https://github.com/l33tdawg/sage/). See [project attribution and ownership](PROJECT_FAMILY.md) and [retained repository history](REPOSITORY_HISTORY.md).
