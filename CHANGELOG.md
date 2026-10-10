# Release notes

## Planned 1.0.0-alpha — unreleased

Meshticulum is a SAGE Foundation project created and maintained by Dhillon Andrew
Kannabhiran (@l33tdawg). It brings bounded authenticated transfers and verified
resources to a design intended to span LoRa and IP. Its core is independent of
the optional SAGE integration; Lantern-specific application work belongs in a
separate planned repository. See [the project family](docs/PROJECT_FAMILY.md).

The candidate includes:

- A standalone Python package with the accepted reliable flow, explicit peer,
  session and deadline contracts, and durable attempt and replay accounting.
- An authenticated TCP prototype with original-deadline checks, single-use
  admission, held revocation, stream-byte budgets and uncertain-write fencing.
- Finite public MR1 radio/TCP runners, immutable resource contexts and native
  grant preparation. Bridge senders use their actual local public quarantine;
  resource responses come from a verified store and received request.
- Independent request, publisher, manifest and chunk verification before a
  public piece is accepted, with reuse limited to verified pieces.
- Recipient-encrypted envelopes, a compact binary carrier preserving sealed
  content, and optional protected SAGE capture/inbox adapters.
- Architecture diagrams, upstream comparisons, source provenance and historical
  RF results with their conditions and unavailable measurements preserved.

This draft does not assert a released or qualified artifact. Native mixed-path
and failure/resume qualification, standalone firmware reproduction, exact
artifact licensing and release review remain open. Private production dispatch,
MeshCore/Reticulum wire interoperability and matched upstream speed comparisons
are not supported claims. Historical RF medians are about **1.2 kB/s** useful
payload; **1.5 kB/s** remains a target. See [performance](docs/PERFORMANCE.md).

The alpha will permit wire/API changes. Candidate validation and artifact hashes
must be tied to its exact commit before these notes become a release. See
[release readiness](docs/RELEASE_READINESS.md).
