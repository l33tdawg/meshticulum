# Release notes

## Unreleased — host-backed MCU queue, 24 October 2026

The next alpha candidate adds a bounded MCU DATA ciphertext queue connected to
the serial worker, durable attempt journal and authenticated flow runtime.
Queue mode requires an immutable opt-in grant; the default path stays
synchronous. Linux retains keys, AEAD, peer ACK authentication, credit and
retries. Queue acceptance, local RF_DONE, authenticated peer receipt and final
physical drain remain separate evidence.

The candidate also adds request-keyed physical-event routing, clock-bound
burst deadlines, exact replay/release checks and recovery fencing. Per-role
operation journal limits are now bound into the grant independently of absolute
MCU boot-counter limits. An exhausted frozen scheduler budget reports a bounded
failure instead of repeatedly admitting refused handles; its conservative
accounting and retained debt are unchanged.

One fixed-order baseline-then-queue comparison sent two fresh **8,164-byte**
signed files between Heltec V4 radios about **20 m apart**. Both used firmware
`mtb1-6cfde1fdd4`, SF7/BW500/CR4:5, sync `0x42`, 921.375 MHz and 0 dBm.
Startup to authenticated receipt improved from **8.766668 s / 931.254611 B/s**
to **7.526076 s / 1,084.761833 B/s**, a **16.483915% goodput gain**.
Both files and publishers verified with zero DATA retries, receive-drop
increases or new UNKNOWN outcomes. Queue mode settled and released all 46 DATA
physical records; drained endpoint completion followed receipt by **27.456 ms**.
This is one paired sample, not sustained-capacity, field-range or production
qualification. See the [sanitized comparison](benchmarks/2026-10-24-mcu-queue-lora/results.json).

The failed rollout and verified restoration are retained. A later baseline
exhausted an undersized pacing budget before the final DATA frame; it remains
failed and fenced. Its diagnostics led to cold-transfer feasibility checks and
separate journal/native limits. The successful pair used fresh finite scopes
while preserving the original paid costs and unresolved historical references.

The measured SDK source is `f06ebeb3e687da4688657311830e95326a0107d2`; its
wheel SHA-256 is
`7998a705ab6a5641ecb9191f08e9b63ba7a676612dcfa7455e291e6177149ad0`.
That wheel still identifies as `1.0.0a1`. A future `1.0.0a2` build requires
its own source/artifact and installation checks; these measurements do not
silently transfer to a changed build. The published `v1.0.0-alpha` tag and
assets remain their original checkpoint.

## Post-alpha cross-room A/B test — 2026-10-22

The published installed wheel from source
`673eb60d24dc60f67371e24bc5c9caeec3a68508` completed one GFSK250 A/B transfer
with Heltec V4 endpoints approximately **20 metres apart in separate rooms**.
Unit B sent a five-part **100,000-byte signed file to Unit A at 1.776 kB/s
including sender startup**. All five authenticated peer receipts and the exact
whole-file SHA-256 passed. Receiver DATA RSSI was **-57.5 to -56.5 dBm**, both
MCU receive-drop deltas were zero, every endpoint process exited zero and no
forced cleanup occurred.

Unit B was first updated app-only from `mtb1-9816f92488` to the current
`mtb1-020e287255` image after its exact 393,216-byte application range was
pinned for rollback. The image was reproduced from source
`c8957e56b017ecd42486acc2c8a46c24cb238d2d`; the SDK wheel remains from
source `673eb60`. One earlier A/B scope failed before DATA because
endpoint-specific boot limits produced different authenticated wire profiles;
that failure remains retained and is not counted as a pass.

Distance is operator-reported. This single cross-room sample does not establish
field range, sustained-capacity distribution, LoRa-profile behavior,
MeshCore/Reticulum interoperability or production readiness. See the
[sanitized result](benchmarks/2026-10-22-ab-cross-room/results.json) and
[issue #43 report](https://github.com/l33tdawg/meshticulum/issues/43#issuecomment-6333261599).

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

Two complete matched LoRa campaigns at source `f895215` produced six pairs at
SF7/BW500 and SF8/BW500. Median sender-ARM-to-receipt rates across all six were
**1.103 and 0.720 kB/s (8.820 and 5.759 kbit/s)**, making SF8 **1.531×** slower
on this near-bench link. All 12 chunks and authenticated receipts verified. The
MCU's 1,024-attempt cumulative cap stood at 380 after both campaigns, leaving
**644 attempts of headroom**, with the same boot generation disarmed and zero
receive drops. The [profile
record](benchmarks/2026-10-18-lora-sf7-sf8/results.json) retains the final and
control medians, individual timings and debt accounting.

Three consecutive HTTP gateway requests now complete inside the normal
**30-second** browser timeout. `links` rendered `example.com` and `example.org`
in **17.77–17.99 seconds** wall time, with gateway completion at
**16.81–17.08 seconds**; both radio directions verified on every run. The
pre-fix 52.8-second startup-race failure remains as the control. See the
[HTTP gateway record](docs/HTTP_GATEWAY.md). None of these results changes the
immutable `v1.0.0-alpha` tag or its checksummed assets.

An interactive `links` session also submitted a real Wiby search and opened a
small result page in about 20 seconds. Google browsing remains outside the
plain-HTTP gateway: `google.com` redirects to HTTPS and the Google search page
is about 92 KB, above the 16 KiB response cap.

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
