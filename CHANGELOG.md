# Release notes

## 1.0.0-alpha (`1.0.0a1`) — 2026-10-17

[Download the GitHub prerelease](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha). It is published from source `673eb60d24dc60f67371e24bc5c9caeec3a68508` and contains the tested SDK wheel, normalized source distribution, firmware source-build guide, project notices, public proof records, release manifest and checksums. No prebuilt firmware or third-party dependency wheels are included.

## Post-alpha measurement update — 2026-10-18

Source `1f85608e2c5e543130673529e21d838917094c11` completed fresh
**250,000-, 500,000- and 1,000,000-byte files at 2.111, 2.216 and 2.230 kB/s
(16.889, 17.725 and 17.838 kbit/s) including sender startup** on the near-bench
GFSK250 path. All three clear 2.0 kB/s. The original 1 MB sample measured
1.750 kB/s while its production LLM engine held CPU4; the CPU-isolated repeat
reduced mean part time from 10.97 to 8.66 seconds and raised the whole-file rate
to 2.230 kB/s. The [larger-file
record](benchmarks/2026-10-18-public-sdk-large-files/results.json) retains both
samples, all parts, confirmations, endpoint exits and timing arithmetic.

A matched LoRa comparison at source `2cfcbb3` completed three same-payload
pairs at SF7/BW500 and SF8/BW500. Median sender-ARM-to-receipt rates were
**1.044 and 0.729 kB/s (8.351 and 5.834 kbit/s)**, making SF8 **1.431×** slower
on this near-bench link. All six chunks and authenticated receipts verified;
the final batch moved the MCU from 119 to 208 attempts with no unknown,
fenced or dropped attempts. See the [profile
record](benchmarks/2026-10-18-lora-sf7-sf8/results.json).

A one-request HTTP gateway also carried a bounded request through a
radio-connected endpoint, fetched `example.com` through a second endpoint's
internet access and returned it over the radio. A `links` text browser rendered
the page in 53.3 seconds; the 30-second timeout failed. See the [HTTP gateway
record](docs/HTTP_GATEWAY.md). Neither result changes the immutable
`v1.0.0-alpha` tag or its checksummed assets.

## Alpha contents and measured scope

Meshticulum is a SAGE Foundation project created and maintained by Dhillon Andrew
Kannabhiran (@l33tdawg). It brings bounded authenticated transfers and verified
resources to a design intended to span LoRa and IP. It is independent of SAGE
services and the [SAGE Lantern application](https://github.com/l33tdawg/sage-lantern).
Lantern consumes Meshticulum as a one-way dependency and owns private integration
and native deployment. See [the project family](docs/PROJECT_FAMILY.md).

The candidate includes:

- A standalone Python package with the accepted reliable flow, explicit peer,
  session and deadline contracts, and durable attempt and replay accounting.
- An authenticated TCP prototype with original-deadline checks, single-use
  admission, held revocation, stream-byte budgets and uncertain-write fencing.
- Reusable public MR1 radio/TCP service composition and resource codecs, with
  explicit request/response bindings and application-supplied authority.
- Publisher-signed manifests, content-addressed chunks and verified-piece
  resume, independent of any product's protected application storage.
- Recipient-encrypted envelopes and a compact binary carrier preserving sealed
  content.
- An opt-in [experimental v2 forward-session primitive](docs/FORWARD_SECRECY.md)
  requiring caller-owned, independently generated Noise X25519 statics and pinned Ed25519
  identities. The full exchange and signed confirmations bind both key types and
  context around unchanged LM1/LM2 records. Known converted legacy recipient keys
  and MPF1 are refused; retained v1 ID/role claims stay fenced. Caller key purpose,
  protected lifecycle, full composition, native transport/key erasure and
  performance remain unqualified, with no native/private wiring or automatic
  legacy upgrade.
- A strict repository boundary: Lantern owns its SAGE capture/inbox/policy,
  native resource contexts, grant preparation, Linux node/pilot entry points
  and device/fleet deployment tools. Meshticulum has no reverse dependency.
- Architecture diagrams, upstream comparisons, source provenance and historical
  RF results with their conditions and unavailable measurements preserved.

Lantern recorded finite native public C→A LoRa→B IP request/reply and a separate
loss/LoRa-resume/measured-IP-return campaign. The latter independently verified
four 977-byte pieces under one unchanged expiry; see the [exact execution pins
and scope](docs/IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return).
These service results do not establish sustained capacity or matched upstream
performance. Historical LoRa RF medians are about **1.2 kB/s** useful payload;
they retain their original runtime and conditions. See [performance](docs/PERFORMANCE.md).

On 17 October, the public SDK at `da247a1bb74601973f0bb72f51c268a412714719`
completed two fresh five-part 100 kB files at **2.03 and 1.95 kB/s
(16.24 and 15.59 kbit/s) including sender startup**, or **1.86 and 1.76 kB/s
(14.85 and 14.05 kbit/s) through endpoint closure**.
A separate fresh 23,900-byte file reached **2.53 kB/s (20.28 kbit/s)** through authenticated
receipt. These are near-bench GFSK250 samples at about one metre. This initial
repeat does not establish sustained capacity or field range. See
[the exact measurement scope](docs/RADIO_SDK_MEASUREMENTS.md).

A separate installed-wheel check at `673eb60` completed a fresh 100 kB file
at **1.74 kB/s (13.92 kbit/s) including sender startup and a five-second pause between verified
parts**, or **1.61 kB/s (12.91 kbit/s) through endpoint closure**. Both normal virtual environments
matched all 67 runtime files; all five confirmations and all ten child exits passed.
The [installed-SDK record](provenance/PUBLIC_INSTALLED_SDK_PROOF.json) retains the
earlier incomplete installed trial and exact scope.

The explicit firmware recipe reproduced current application BIN/ELF outputs for
both named targets through locked vendor inputs on the recorded build host.
The retained Heltec application-span check and later runtime-tag continuity
remain distinct from fresh full-device readback and physical Xiao acceptance.
The selected SDK/source-build package passed its content/notice coverage
review and was published as the `v1.0.0-alpha` prerelease. Optional prebuilt
firmware retains its separate distribution checklist. Retained extraction history is disclosed in
[repository history](docs/REPOSITORY_HISTORY.md). Private native owner/consent and transport still require
qualification. The public alpha does not activate private production;
MeshCore/Reticulum wire interoperability and overall speed/security superiority are unsupported. The competitive goal is faster useful transfers and stronger security than both; comparison uses [published figures and their conditions](docs/PERFORMANCE.md#comparison-with-published-figures), without requiring counterpart benchmark campaigns.

The alpha permits wire/API changes. Validation and artifact hashes are tied to
source `673eb60d24dc60f67371e24bc5c9caeec3a68508` and the published release assets. See
[release readiness](docs/RELEASE_READINESS.md).

The prerelease does not establish a qualified Lantern dependency pair or deploy a runtime to devices. Larger files, wider recovery, mixed-path qualification and sustained-performance work continue separately.
