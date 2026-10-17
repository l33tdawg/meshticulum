# Roadmap

Meshticulum is a public experimental transport and resource project. The next milestone is an alpha with deployable SDK and firmware, reliable verified completion, and sustained useful encrypted delivery **above 1,000 B/s**, including sender startup. The optional **4–5 kB/s** stretch belongs after that milestone. There are no promised release dates.

## Current status

The [public repository](https://github.com/l33tdawg/meshticulum) and [project website](https://l33tdawg.github.io/meshticulum/) are live. The checkout uses Python version `1.0.0a1`, but the alpha tag, release and downloadable SDK/firmware assets have **not been published**. You can [install from source](../README.md#install-and-explore) and review the existing examples.

The measured SDK source, `da247a1`, has [passing source CI on Python 3.11, 3.12 and 3.13](https://github.com/l33tdawg/meshticulum/actions/runs/39028024473). The hardware samples below supply separate physical evidence. A later release candidate needs its own exact source checks and artifacts.

On **17 October 2026**, the public SDK completed **two fresh 100,000-byte files in five signed parts each**, with independent publisher and whole-file verification. A separate fresh **23,900-byte file** completed at the same source, `da247a1bb74601973f0bb72f51c268a412714719`. All three used GFSK at 250 kb/s, from an SX1262 HAT to a Heltec V4 MCU about one metre apart.

| Whole-file boundary | 23,900 bytes: useful B/s | 100 kB run 1: useful B/s | 100 kB run 2: useful B/s |
| --- | ---: | ---: | ---: |
| First sender startup → final authenticated receipt | **2,534.6** | **2,030.2** | **1,949.2** |
| First DATA-worker call → final authenticated receipt | 2,926.0 | 2,076.6 | 1,985.7 |
| Scope issue → both endpoints closed | 701.6 | **1,856.0** | **1,755.7** |

Each 100 kB interval includes all five sequential parts and coordination gaps. All ten receiver confirmations were accepted, and all twenty endpoint children exited successfully. The two files had zero and one DATA retransmissions respectively, with zero new receiver drops. The smaller sample used the default 20-second receiver grace, which appears in its closure time.

**These samples exceed the startup-inclusive alpha floor for the tested path.** Two fresh 100 kB files provide an initial repeat, rather than a sustained-capacity or completion distribution. GFSK is separate from LoRa modulation; the one-metre setup does not measure field range. [The measurement report](RADIO_SDK_MEASUREMENTS.md) records exact durations, source hashes and limits. The [earlier 15 October campaign](PERFORMANCE.md#a-complete-100-kb-file-2026-10-15) remains in the historical record.

A separate [clean installed-wheel check](RADIO_SDK_MEASUREMENTS.md#clean-installation-and-verified-piece-continuation) completed another fresh 100 kB file at **1,739.9 B/s including startup and a five-second pause between verified parts**, or **1,613.3 B/s through endpoint closure**. Both normal virtual environments matched all 67 runtime files to candidate `673eb60`; all five confirmations and all ten endpoint exits passed under the unchanged original window. The record retains the earlier incomplete 95,600-byte installed trial. This establishes the stated installation and verified-piece continuation check, with final selected-artifact review pending.

Earlier native milestones demonstrated [mixed LoRa/IP delivery, loss, verified-piece retention, fresh LoRa resume and return to a locally measured faster IP path](IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return). Their original scope and failed attempts remain part of the record. Current source tests and later firmware builds do not requalify those physical results.

## Alpha review and wider programme

The selected SDK/firmware-source alpha follows the [release-readiness checklist](RELEASE_READINESS.md). The full programme retains all five workstreams below; native/private qualification belongs to the consuming integration and does not become an advertised SDK capability through this release:

1. **Sustained delivery and reliable completion.** Repeat the new complete public SDK file transfers on the declared supported hardware and profile. Require complete authenticated delivery and independent content verification within the original deadlines. Report DATA, sender-start, request-to-verification and whole-operation timing, with ACK/retry cost and retained failures. Keep unknown outcomes and lifetime attempt/airtime debt; a restart cannot refund or renew them.
2. **A deployable SDK and firmware path.** Qualify the documented public SDK examples on real hardware, including installation, finite grants, lifecycle, disarm and rollback. Preserve exact source/firmware correspondence and supported-device limits. The public SDK now has two fresh 100 kB measurements and a clean installed-wheel 100 kB verified-piece continuation check. Final source-build artifact review remains pending; those finite checks do not qualify every installation, supported mode or recovery path.
3. **Mixed-carrier and native integration.** Retain real radio/IP, loss, verified-piece resume and measured-return coverage against the exact consuming application dependency. Preserve peer, boot, expiry, replay and accounting bindings. Kernel acceptance, RF completion, peer receipt and application acceptance need their own evidence.
4. **Private admission and security.** Complete the inherited native owner, consent, revocation, encrypted-backend, capture and uncertain-write recovery checks in Lantern. Keep durable replay and the experimental forward-session/PFS qualification gates, including key purpose, lifecycle, compromise behavior and native erasure. Classical default transport and static recipient envelopes do not acquire deployed forward secrecy from source tests. See [forward-session status](FORWARD_SECRECY.md) and [security properties and gaps](COMPARISON.md#security-properties-and-gaps).
5. **Reviewed release artifacts.** Bind standalone and paired integration results to the exact candidate, reproduce and inspect the wheel/source distribution, and retain installation checks. Close the applicable firmware source/deployed-image, linked notices, corresponding-source/relinking and redistribution review for the exact artifacts. Publishing an alpha tag or assets requires a separate release decision.

Compare performance and security with published MeshCore and Reticulum evidence only at stated, comparable boundaries. Keep hardware, profile, payload, encryption, topology and missing measurements visible. Meeting the alpha floor alone does not establish overall superiority; separate upstream benchmark campaigns are not required.

## After alpha

- **More headroom and broader measurements.** Establish repeated startup-inclusive performance before treating the new finite rates as sustained capacity. Improve startup latency, retry cost, completion consistency and resource use. Expand file sizes, topology, range and power measurements under fresh bounded admission.
- **Optional 4–5 kB/s stretch.** Evaluate higher useful encrypted rates on explicitly supported hardware and profiles after the reliability and deployment work. Capability, range and operating cost need new measurements; raw modem bitrate is not application goodput.
- **Richer path selection.** Extend verified-piece reuse and locally measured radio/IP choices. Autonomous routing and MeshCore/Reticulum wire interoperability remain unimplemented; existing finite path demonstrations do not supply those features.
- **Post-quantum feasibility and hybrid research.** Evaluate the separate experimental hybrid prototype's security review, replay/key lifecycle, setup cost, loss recovery and device memory/performance. The default protocols remain classical. PQC is not an alpha prerequisite, a current radio capability or a promised delivery date. [Post-quantum status](POST_QUANTUM.md) records the prototype and its limits.

## Project boundaries

[Meshticulum](https://github.com/l33tdawg/meshticulum) supplies reusable protocols, crypto, drivers, public-resource interfaces, SDK examples and generic radio firmware. [Lantern](https://github.com/l33tdawg/sage-lantern) owns product apps, protected SAGE integration, native policy/provisioning/deployment, and product hardware/enclosure/STL work. The dependency remains **Lantern → Meshticulum**.

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg), within the broader [SAGE project](https://github.com/l33tdawg/sage/). See [project attribution and ownership](PROJECT_FAMILY.md) and [retained repository history](REPOSITORY_HISTORY.md).
