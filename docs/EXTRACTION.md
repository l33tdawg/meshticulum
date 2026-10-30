# Source extraction and repository boundary

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg). The [main SAGE repository](https://github.com/l33tdawg/sage/), [Lantern device/product repository](https://github.com/l33tdawg/sage-lantern) and this transport repository have distinct responsibilities; see [project family](PROJECT_FAMILY.md).

This repository started from a reviewed working-tree snapshot of Meshticulum sources in SAGE Lantern on 2026-10-10. It does not contain the whole SAGE application or its Git history. The parent checkout and unrelated work were preserved. Source attribution, Apache-2.0 [LICENSE](../LICENSE) and inherited [NOTICE](../NOTICE) are retained.

## One-way dependency

**Lantern depends on Meshticulum. Meshticulum does not depend on Lantern.** The core retains generic identities/contracts, cryptography, frozen reliable flow and scheduler, wire/replay/attempt journals, service transfers/public loop, verified resources and serial/SX1262/TCP drivers. Offline envelope codecs belong to `meshticulum.privacy`; public resource codecs belong to `meshticulum.resources`.

The following application implementations belong only in [Lantern's integration package](https://github.com/l33tdawg/sage-lantern/tree/main/software/meshticulum-integration/src/lantern_meshticulum):

- `sage/`: protected inbox, WorkflowStore, contacts, custody, durable capture and native private service policy/runner.
- `bench/node.py`, `bench/service_node.py` and `bench/pilot_*.py`: finite Linux jobs, independently observed host facts, resource permission, native provisioning, quarantine/publisher application handling and fleet/pilot orchestration.
- `ip/node.py`: native finite TCP setup, process lifecycle and device-specific service checks.

Lantern also owns the Python [firmware/runtime build and deployment tools](https://github.com/l33tdawg/sage-lantern/tree/main/firmware/meshticulum-bench). Meshticulum retains its project-authored raw coprocessor/protocol source, native ABI/harness controls and portable board-build boundary. Neither the private adapters nor fleet deployment implementations are duplicated in this package. Lantern imports the generic library and supplies native permissions; the core has no Lantern import or SAGE SDK extra.

## Historical provenance and unchanged protocol bytes

[`provenance/UPSTREAM_SOURCE.json`](../provenance/UPSTREAM_SOURCE.json) records the original parent repository/HEAD, dirty snapshot status, source paths/hashes and initial extraction destinations. It describes that initial snapshot. Entries for adapters subsequently moved to Lantern are historical provenance, not a statement that those files remain in the current Meshticulum tree. Later boundary and validation records must identify the exact reviewed commits in both repositories.

The accepted flow includes the independently reviewed 30 October selective-retry revision described in its [revision record](../src/meshticulum/flow/README.md#revision-10-selective-retry-30-october-2026). The port remains unchanged. Current source pins:

- `flow.py`: `0a2464816e53c239018a4c33af43e927007512018bedb2d558e99f0fad5ac7b8`
- `flow_port.py`: `57596421d916f0c49d61599653b5ab75154d64e5bc806ba1444fa5905ad025ca`

The frozen port retains its relative `meshticulum_contracts` and `meshticulum_scheduler` imports. Small bridge packages alias their child modules to canonical packages and preserve the actual class objects used by scheduler/lease checks. `bench.flow_runtime` imports canonical `meshticulum.flow`; it does not load a duplicate flow implementation from Lantern.

Envelope schemas, historical signature/key-derivation domains, authenticated content, bounds and ciphertext retain their protocol identity. A historical `sage` or `lantern` string inside a cryptographic domain does not import either application; renaming those bytes would change protocol compatibility. The 24-hour envelope ceiling remains the same standalone constant as at extraction. Changing it still requires protocol and application consent review.

Initial extraction changes included namespace/path adaptation, independent test collection and host-runtime packaging. The later repository split moves application integration and deployment ownership to Lantern while preserving generic protocol behavior. A Lantern runtime assembled with this library is a new, separately identified artifact; historical SAGE-side runtime hashes must not be relabeled as that build.

Historical comments/specifications may refer to review receipts under the original checkout's `work/` directory. Those receipts, real grants, device captures, secrets, binaries and private content are not copied here. Code location, a source manifest or a passing test does not transfer physical qualification or production private authority. See [hardware ownership](HARDWARE.md), [architecture](ARCHITECTURE.md) and [remaining qualification](ROADMAP.md).
