# Attribution and the SAGE project family

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project, created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg), with contributions from the wider project community. It is part of the broader [SAGE project](https://github.com/l33tdawg/sage/).

## Repository responsibilities

| Repository | Responsibility |
| --- | --- |
| [SAGE](https://github.com/l33tdawg/sage/) | The main SAGE project: agent memory, governance and core infrastructure. |
| [Meshticulum](https://github.com/l33tdawg/meshticulum) | The independent transport and resource system: contracts, reliable flow, scheduling, authenticated carriers, generic radio interfaces and verified resource transfer. |
| **Lantern-specific repository — planned** | Lantern product apps/UI, provisioning, runtime orchestration, device services and integrations that consume Meshticulum and SAGE. Its URL will be published when the repository is ready. |

Lantern will use Meshticulum as a reusable dependency. Meshticulum's core remains usable by other applications without the Lantern product or SAGE services. The Foundation attribution describes the project's origin and stewardship; it does not make a SAGE service a transport prerequisite.

## Transition status

The Meshticulum extraction is complete. The separate Lantern product repository is still planned. Some historical Lantern-oriented lab tools, compatibility code and optional SAGE integration adapters remain in this repository to preserve source provenance and existing controls during that transition. Their presence does not mean the Lantern product is owned by this repository or that the repository split has already finished.

The exact Lantern repository name, URL and dependency migration will be recorded when that work is completed. Historical source references in [extraction notes](EXTRACTION.md) and [UPSTREAM_SOURCE.json](../provenance/UPSTREAM_SOURCE.json) retain the actual donor snapshot; they are separate from the main SAGE project link.

## Licensing and contributor credit

Meshticulum retains the [Apache-2.0 license](../LICENSE). [NOTICE](../NOTICE) carries SAGE Foundation and maintainer attribution alongside the original SAGE Commons contributor notice. Existing contributor and third-party attribution remains intact. Foundation links and author/maintainer names are also included in the Python package metadata.
