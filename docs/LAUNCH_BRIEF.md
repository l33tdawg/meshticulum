# Meshticulum 1.0.0-alpha launch brief

The experimental source-build alpha is public. Use this brief for the LinkedIn announcement, next week's Instagram posts and other launch material. The main call to action is to install it, test it on real hardware and report exact results.

## The story

A radio network should carry more than a short message. It should move a signed reference pack, recover missing pieces after a link fails, and use a faster local connection when one becomes available. The author still needs verification. A private message still needs to remain encrypted to its recipient.

Meshticulum is an open experiment in closing that gap. MeshCore's embedded radio work and Reticulum's broader secure networking model shaped some of the priorities. Meshticulum has its own protocol and is not wire-compatible with either.

The reusable core runs without Lantern or the SAGE SDK. The project is created and maintained by Dhillon Andrew Kannabhiran under the SAGE Foundation.

## What it does

- Moves bounded, reliable data over serial radio or Linux SX1262 workers.
- Binds peers, services, objects, authority and original deadlines into each transfer.
- Uses receiver credit, selective acknowledgements, retry accounting and durable journals.
- Moves public resources as signed manifests and content-addressed chunks.
- Verifies the publisher, chunks and reconstructed file before acceptance.
- Reuses verified pieces without treating an unverified cache as trusted content.
- Carries recipient-encrypted, sender-signed private envelopes in a compact binary form.
- Includes an authenticated TCP source path and experimental radio/IP route selection.

The alpha does not provide autonomous routing, MeshCore/Reticulum compatibility, stable wire/API compatibility or production private messaging.

## Headline results

Two fresh five-part 100,000-byte files completed on a direct near-bench GFSK250 HAT-to-MCU link about one metre apart:

- **2.03 and 1.95 kB/s of useful application throughput**, from sender startup through the final authenticated receipt.
- Equivalent to **16.24 and 15.59 kbit/s**.
- Scope-to-both-endpoints-closed rates were **1.86 and 1.76 kB/s**, or **14.85 and 14.05 kbit/s**.

A separate clean installed-wheel run completed a fresh 100,000-byte file at **1.74 kB/s (13.92 kbit/s)** from sender startup, including a deliberate five-second pause between verified parts. Its scope-to-closure rate was **1.61 kB/s (12.91 kbit/s)**.

On 18 October, larger files completed at the same source and near-bench path:

- **250,000 bytes: 2.111 kB/s (16.889 kbit/s)** including sender startup.
- **500,000 bytes: 2.216 kB/s (17.725 kbit/s)**.
- **1,000,000 bytes: 2.230 kB/s (17.838 kbit/s)**, from a repeat with the sender
  pinned away from a CPU core its production LLM engine held near 95%.

All three clear **2.0 kB/s** on the whole-file boundary. The first 1 MB sample
measured **1.750 kB/s** under that host load; both samples stay in the record.
The remaining cost is 42 process starts and about **84.8 seconds** between
part intervals in the pinned repeat. That is the next engineering target, not
a hidden radio limit.

A first one-request HTTP gateway test also fetched `example.com` from a unit
with internet access and carried the response back over the radio. A `links`
text browser rendered it in **53.3 seconds**. The same request with a
**30-second** timeout failed. The gateway is an opt-in application experiment,
not transparent IP routing or HTTPS support.

The GFSK link itself ran at **250 kbit/s**. The figures above are useful application bytes after transport, encryption, verification, acknowledgements and retry costs.

## Scope required in every post

The headline figures apply to the tested direct near-bench GFSK250 path. GFSK250 is not LoRa. The tests do not establish field range, sustained all-path throughput, RF energy, EIRP or superiority over MeshCore or Reticulum.

All ten receiver confirmations were accepted across the two 100,000-byte files. All twenty endpoint children exited successfully. The runs had zero and one DATA retransmissions respectively, with zero new receiver drops. Publisher and whole-file verification passed.

The larger files accepted all 74 receiver confirmations, and all 148 endpoint
children exited zero. Each file had one DATA retransmission; the 250 kB run had
no new raw receive drop, while the 500 kB and 1 MB runs each recorded one.

The installed-wheel run used normal isolated Python environments, matched all 67 runtime files to the published wheel, and accepted all five confirmations. It continued verified work after a 5.011-second pause under the original grants and 240-second window. It does not establish in-flight crash recovery.

## Roadmap

Next measurement and reliability work:

- Repeat the 250 kB, 500 kB and 1 MB results with warm workers and fewer process
  restarts; target above 2.0 kB/s on the whole-file 1 MB boundary.
- Extend the HTTP gateway to a warm session and measure whether a 30-second
  browser path is reachable.
- Measure repeated startup-inclusive delivery, completion consistency, latency, retries and resource use.
- Exercise loss, final-ACK recovery, expiry, replay refusal, interruption and verified-piece resume.
- Preserve peer, boot, deadline, attempt and airtime bindings through restarts and failures.

Mixed carriers and applications:

- Extend real radio/IP testing, loss recovery, verified-piece reuse and measured path changes.
- Qualify consuming applications against an exact Meshticulum dependency.
- Test more hardware, topology, range and power conditions under fresh bounded admission.

Private and security work:

- Complete native owner, consent and revocation qualification in Lantern.
- Qualify encrypted-backend admission, durable capture and uncertain-write recovery.
- Continue experimental forward-session and post-quantum research without presenting either as a deployed alpha capability.

Longer-term work:

- Improve startup latency, retry cost and delivery consistency.
- Evaluate the optional 4 to 5 kB/s (32 to 40 kbit/s) target only after reliability and deployment work.
- Broaden verified-piece reuse and measured radio/IP route selection.

No release dates are promised.

## What we want people to do

- Install the wheel on Python 3.11 or later and run the offline example.
- Build the firmware from the pinned source recipe and report the board, toolchain and hashes.
- Test a file transfer and include timing boundaries, retries, logs and failure details.
- Try different Linux hosts, radios, distances and supported profiles.
- Review the protocol, docs, packaging and security boundaries.
- Open issues with reproducible commands and exact evidence.
- Never publish private keys, device secrets or unrelated private data.

## Short announcement draft

Most mesh radio projects begin with a message. Meshticulum starts with a harder question: can a small radio network carry authenticated messages and verified resources across changing paths, and keep useful work when one path disappears?

We have published Meshticulum 1.0.0-alpha, an experimental Apache-2.0 transport and resource project from SAGE Foundation.

The reusable Python core handles bounded reliable flow, authenticated sessions, signed public resources, encrypted message envelopes and experimental radio/IP path selection. It can run without SAGE Lantern or the SAGE SDK.

On a direct near-bench GFSK250 path, two fresh 100,000-byte files completed at 1.95 to 2.03 kB/s (15.6 to 16.2 kbit/s) of useful application throughput, including sender startup. That is a measured baseline, not a LoRa or field-range claim. It gives us a starting point for harder work: sustained delivery, larger files, loss and recovery, mixed paths, more hardware and clearer security boundaries.

Larger files followed: 250 kB, 500 kB and 1 MB at 2.111, 2.216 and 2.230 kB/s
including startup. A one-request HTTP gateway also rendered a public
page through the radio in 53.3 seconds. The next target is to remove the
process-start cost and make the browser path usable at a normal timeout.

This alpha is for people who like to test the edge of a design. Install the SDK. Run the offline example. Build the firmware. Try a radio path. Break something, then send us the exact hardware, source, commands, logs and timing.

The goal is simple: build a transport people can use and evidence they can check.

## Links and artwork

| Material | Destination |
| --- | --- |
| Alpha release | [Meshticulum 1.0.0-alpha](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha) |
| Project website | [Meshticulum](https://l33tdawg.github.io/meshticulum/) |
| Getting started | [SDK and device setup](https://l33tdawg.github.io/meshticulum/getting-started.html) |
| Roadmap | [Project roadmap](https://l33tdawg.github.io/meshticulum/roadmap.html) |
| Source | [Meshticulum repository](https://github.com/l33tdawg/meshticulum) |
| Measurements | [17 October SDK report](RADIO_SDK_MEASUREMENTS.md) |
| Main project | [SAGE](https://github.com/l33tdawg/sage/) |
| Stewardship | [SAGE Foundation](https://sage-foundation.net/) |
| Product integration | [SAGE Lantern](https://github.com/l33tdawg/sage-lantern) |
| Scalable logo | [SVG](../website/assets/meshticulum-logo.svg) |
| Transparent logo | [1024 px PNG](../website/assets/meshticulum-logo.png) |
| Article share card | [1200 x 630 PNG](../website/assets/social-card.png) |
| Brand colors and artwork source | [Brand guide](../website/assets/BRAND.md) |

Do not describe the alpha as a prebuilt firmware release. The release contains the SDK wheel, normalized source archive, firmware source-build guide and proof materials. Device instructions retain their experimental limits.
