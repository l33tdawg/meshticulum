# Release notes

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
performance. Historical RF medians are about **1.2 kB/s** useful payload;
**1.5 kB/s** remains a target. See [performance](docs/PERFORMANCE.md).

The explicit firmware recipe reproduced both target outputs in fresh workspaces
on one pinned build host. Full source/deployed-image correspondence, exact
artifact notices and source/relinking closure, clean public history and release
review remain open. Private native owner/consent and transport still require
qualification. This draft represents no public alpha or production activation;
MeshCore/Reticulum wire interoperability and overall speed/security superiority are unsupported. The competitive goal is faster useful transfers and stronger security than both; comparison uses [published figures and their conditions](docs/PERFORMANCE.md#comparison-with-published-figures), without requiring counterpart benchmark campaigns.

The alpha will permit wire/API changes. Candidate validation and artifact hashes
must be tied to its exact commit before these notes become a release. See
[release readiness](docs/RELEASE_READINESS.md).

This version is prepared in a separate clean local history for review. It has no remote or tag and remains subject to the public-alpha gates. Current private Lantern integration still pins the earlier development Core commit; these artifacts do not establish a new qualified dependency pair.
