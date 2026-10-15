# Roadmap

Meshticulum is a public experimental transport and resource project. The next milestone is an alpha with deployable SDK and firmware, reliable verified completion, and sustained useful encrypted delivery **above 1,000 B/s**, including sender startup. The optional **4–5 kB/s** stretch belongs after that milestone. There are no promised release dates.

## Current status

The [public repository](https://github.com/l33tdawg/meshticulum) and [project website](https://l33tdawg.github.io/meshticulum/) are live. The checkout uses Python version `1.0.0a1`, but the alpha tag, release and downloadable SDK/firmware assets have **not been published**. You can [install from source](../README.md#install-and-explore) and review the existing examples.

The [TCP credit scheduling change in PR #10](https://github.com/l33tdawg/meshticulum/pull/10) is merged. Its [source CI passed on Python 3.11, 3.12 and 3.13](https://github.com/l33tdawg/meshticulum/actions/runs/38692611149). This checks implementation behavior; the change has no new radio qualification attached to it.

A completed native campaign delivered **100,000 decoded bytes** in five signed MB1 parts from an SX1262 HAT sender to a Heltec V4 MCU receiver. It used GFSK at 250 kb/s, 921.375 MHz and a 0 dBm chip setting. The recipient verified the publisher, all parts and the whole-file SHA-256. All links completed and disarmed, child jobs retired and management clients closed within the original windows.

| Boundary of that same finite run | Useful decoded B/s |
| --- | ---: |
| First DATA-worker call → authenticated flow ACK, summed over five responses | 1,185.6 |
| Sender start → authenticated flow ACK, summed over five responses | 945.7 |
| Request → independent publisher verification, summed over five parts | 296.2 |
| Whole controller, including setup, verification and cleanup | 107.0 |

The DATA interval includes host work, ACKs and retries; it is not measured RF airtime. **The startup-inclusive rate is below the alpha floor**, and one completed campaign does not establish sustained performance or a completion distribution. [Performance and measurement](PERFORMANCE.md#a-complete-100-kb-file-2026-10-15) retains the durations, conditions and sanitized record. Range, measured RF airtime, energy and EIRP remain unavailable.

Earlier native milestones demonstrated [mixed LoRa/IP delivery, loss, verified-piece retention, fresh LoRa resume and return to a locally measured faster IP path](IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return). Their original scope and failed attempts remain part of the record. Current source tests and later firmware builds do not requalify those physical results.

## Work required for alpha

The full [release-readiness checklist](RELEASE_READINESS.md) remains in force. The immediate work is:

1. **Sustained delivery and reliable completion.** Improve startup and host overhead, then repeat useful encrypted transfers on the declared supported hardware and profile. Require complete authenticated delivery and independent content verification within the original deadlines. Report DATA, sender-start, request-to-verification and whole-operation timing, with ACK/retry cost and retained failures. Keep unknown outcomes and lifetime attempt/airtime debt; a restart cannot refund or renew them.
2. **A deployable SDK and firmware path.** Qualify the documented public SDK examples on real hardware, including installation, finite grants, lifecycle, disarm and rollback. Preserve exact source/firmware correspondence and supported-device limits. The completed native MB1 campaign does not by itself qualify the literal public CLI example.
3. **Mixed-carrier and native integration.** Retain real radio/IP, loss, verified-piece resume and measured-return coverage against the exact consuming application dependency. Preserve peer, boot, expiry, replay and accounting bindings. Kernel acceptance, RF completion, peer receipt and application acceptance need their own evidence.
4. **Private admission and security.** Complete the inherited native owner, consent, revocation, encrypted-backend, capture and uncertain-write recovery checks in Lantern. Keep durable replay and the experimental forward-session/PFS qualification gates, including key purpose, lifecycle, compromise behavior and native erasure. Classical default transport and static recipient envelopes do not acquire deployed forward secrecy from source tests. See [forward-session status](FORWARD_SECRECY.md) and [security properties and gaps](COMPARISON.md#security-properties-and-gaps).
5. **Reviewed release artifacts.** Bind standalone and paired integration results to the exact candidate, reproduce and inspect the wheel/source distribution, and retain installation checks. Close the applicable firmware source/deployed-image, linked notices, corresponding-source/relinking and redistribution review for the exact artifacts. Publishing an alpha tag or assets requires a separate release decision.

Compare performance and security with published MeshCore and Reticulum evidence only at stated, comparable boundaries. Keep hardware, profile, payload, encryption, topology and missing measurements visible. Meeting the alpha floor alone does not establish overall superiority; separate upstream benchmark campaigns are not required.

## After alpha

- **More headroom and broader measurements.** Continue toward the broader 1–1.5 kB/s programme goal while improving startup latency, retry cost, completion consistency and resource use. Expand file sizes, topology, range and power measurements under fresh bounded admission.
- **Optional 4–5 kB/s stretch.** Evaluate higher useful encrypted rates on explicitly supported hardware and profiles after the reliability and deployment work. Capability, range and operating cost need new measurements; raw modem bitrate is not application goodput.
- **Richer path selection.** Extend verified-piece reuse and locally measured radio/IP choices. Autonomous routing and MeshCore/Reticulum wire interoperability remain unimplemented; existing finite path demonstrations do not supply those features.
- **Post-quantum feasibility and hybrid research.** Evaluate the separate experimental hybrid prototype's security review, replay/key lifecycle, setup cost, loss recovery and device memory/performance. The default protocols remain classical. PQC is not an alpha prerequisite, a current radio capability or a promised delivery date. [Post-quantum status](POST_QUANTUM.md) records the prototype and its limits.

## Project boundaries

[Meshticulum](https://github.com/l33tdawg/meshticulum) supplies reusable protocols, crypto, drivers, public-resource interfaces, SDK examples and generic radio firmware. [Lantern](https://github.com/l33tdawg/sage-lantern) owns product apps, protected SAGE integration, native policy/provisioning/deployment, and product hardware/enclosure/STL work. The dependency remains **Lantern → Meshticulum**.

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg), within the broader [SAGE project](https://github.com/l33tdawg/sage/). See [project attribution and ownership](PROJECT_FAMILY.md) and [retained repository history](REPOSITORY_HISTORY.md).
