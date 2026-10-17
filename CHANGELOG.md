# Release notes

## Project preview — development source

The Meshticulum source, documentation, architecture diagrams and project website are public. The original preview used package version `0.1.0`; this source candidate uses `1.0.0a1` metadata while the alpha remains unreleased. A packaged alpha release will follow its own readiness review; no `v1.0.0-alpha` tag or release is created by this source update. See [the preview scope](docs/PROJECT_PREVIEW.md) and [the current source update](docs/TRANSPORT_SOURCE_UPDATE.md).

## Proposed 1.0.0-alpha (`1.0.0a1`) — unreleased

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
completed two fresh five-part 100 kB files at **2,030.2 and 1,949.2 useful B/s
including sender startup**, or **1,856.0 and 1,755.7 B/s through endpoint closure**.
A separate fresh 23,900-byte file reached **2,534.6 B/s** through authenticated
receipt. These are near-bench GFSK250 samples at about one metre. This initial
repeat leaves sustained capacity, field range and alpha acceptance open. See
[the exact measurement scope](docs/RADIO_SDK_MEASUREMENTS.md).

The explicit firmware recipe reproduced both target outputs in fresh workspaces
on one pinned build host. Full source/deployed-image correspondence, exact
artifact notices and source/relinking closure, current release-tree review and
release approval remain open. Retained extraction history is disclosed in
[repository history](docs/REPOSITORY_HISTORY.md). Private native owner/consent and transport still require
qualification. This draft represents no public alpha or production activation;
MeshCore/Reticulum wire interoperability and overall speed/security superiority are unsupported. The competitive goal is faster useful transfers and stronger security than both; comparison uses [published figures and their conditions](docs/PERFORMANCE.md#comparison-with-published-figures), without requiring counterpart benchmark campaigns.

The alpha will permit wire/API changes. Candidate validation and artifact hashes
must be tied to its exact commit before these notes become a release. See
[release readiness](docs/RELEASE_READINESS.md).

The later alpha candidate remains subject to its own exact source, artifact and release review. Project publication does not establish a new qualified Lantern dependency pair or deploy a runtime to devices.
