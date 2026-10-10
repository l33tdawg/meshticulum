# Release notes

## Planned 1.0.0-alpha — unreleased

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
MeshCore/Reticulum wire interoperability and speed superiority are unsupported.

The alpha will permit wire/API changes. Candidate validation and artifact hashes
must be tied to its exact commit before these notes become a release. See
[release readiness](docs/RELEASE_READINESS.md).
