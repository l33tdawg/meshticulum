# Attribution and the SAGE project family

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project, created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg), with contributions from the wider project community. It is part of the broader [SAGE project](https://github.com/l33tdawg/sage/).

## Repository responsibilities

| Repository | Responsibility |
| --- | --- |
| [SAGE](https://github.com/l33tdawg/sage/) | The main SAGE project: agent memory, governance and core infrastructure. |
| [Meshticulum](https://github.com/l33tdawg/meshticulum) | Independent protocols, contracts, reliable flow, scheduling, cryptographic envelopes, generic radio/IP carriers and verified resources. |
| [SAGE Lantern](https://github.com/l33tdawg/sage-lantern) | All Lantern product code, apps/UI, STL and printing files, private SAGE adapters, protected inbox/capture, device policy, provisioning, pilot control and deployment. |

The dependency direction is **Lantern → Meshticulum**. Meshticulum remains usable by other applications without Lantern, SAGE services or the SAGE SDK. Foundation attribution describes the project's origin and stewardship; it does not introduce an application dependency.

Lantern's repository is planned to become public closer to December/January alongside product announcement and Kickstarter preparation. Meshticulum can introduce its reusable networking work earlier. Those are separate launches, with readiness determining their dates. [Earlier repository history](REPOSITORY_HISTORY.md) remains visible; current Meshticulum source and distributions exclude Lantern implementations.

## The integration boundary

Lantern owns `software/meshticulum-integration/` for its private adapters and native host integration, and `firmware/meshticulum-bench/` for its device build, bundle, provisioning and pilot tools. Those components consume Meshticulum's protocol and resource APIs. Lantern also owns their integration tests, compatible SDK requirements and production lifecycle policy.

Meshticulum owns the reusable encrypted-envelope codecs, public-resource verification, flow and scheduler contracts, authenticated carrier prototypes and generic drivers. It does not import Lantern modules, operate Lantern fleet services or implement SAGE's protected-store and consent lifecycle. Each application supplies its own admitted authority, storage and service policy through the core interfaces.

The repository split transfers source ownership, not physical qualification. A Lantern deployment must pin its Meshticulum dependency and retain evidence for the exact combined candidate. Earlier radio measurements, modeled private controls and old runtime bundles keep their original scope and hashes. Alpha, private-backend and publication gates remain open as recorded in [release readiness](RELEASE_READINESS.md) and the [roadmap](ROADMAP.md).

Historical references in [extraction notes](EXTRACTION.md) and [UPSTREAM_SOURCE.json](../provenance/UPSTREAM_SOURCE.json) retain the donor snapshot and original layout. They are provenance records rather than instructions to reinstall removed adapters into Meshticulum.

## Licensing and contributor credit

Meshticulum retains the [Apache-2.0 license](../LICENSE). [NOTICE](../NOTICE) carries SAGE Foundation and maintainer attribution alongside the original SAGE Commons contributor notice. Existing contributor and third-party attribution remains intact. Foundation links and author/maintainer names are also included in the Python package metadata.
