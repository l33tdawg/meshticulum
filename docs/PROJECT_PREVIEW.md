# Project preview

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran](https://github.com/l33tdawg). The source and project website introduce the design, invite review and share measured results while development continues. A packaged `1.0.0-alpha` release will follow its own readiness review.

## Scope of the announcement

The repository contains reusable protocol, crypto, reliable flow, radio/IP drivers and verified public-resource code. It includes architecture diagrams, comparisons with published upstream figures, source tests and benchmark metadata. The development package version is `0.1.0`; there is no published alpha tag or release.

[SAGE](https://github.com/l33tdawg/sage/) supplies the broader agent-memory project. [SAGE Lantern](https://github.com/l33tdawg/sage-lantern) owns product UI, protected SAGE adapters, native device/profile policy, provisioning, pilot control and deployment. Lantern consumes Meshticulum. Those product implementations and private deployment materials belong in Lantern's separate repository.

## Claims supported today

- Meshticulum has its own experimental protocol, combining practical radio-mesh priorities with authenticated networking and verified resources across LoRa and IP. Existing MeshCore/Reticulum wire interoperability is not implemented.
- The latest accepted native public response delivered **16,356 verified bytes at 1,071.3 B/s** from sender start to its final reliable-flow ACK, with zero DATA retries or duplicates. Full request-to-publisher verification took about **80.7 seconds**, equivalent to **202.6 B/s**. These are two boundaries of one finite transfer, not sustained file-transfer capacity.
- That DATA-phase rate is numerically **2.61 times** the **410 B/s** MeshCore test published by this project's creator in [MeshCore issue #3404](https://github.com/meshcore-dev/MeshCore/issues/3404). Both use SF7/BW500; workloads, devices and timing boundaries differ. It is a scoped comparison between our published observations, not a universal speed ranking or an independently certified upstream benchmark.
- An older 30-sample radio experiment recorded about **1.2 kB/s median** useful encrypted goodput. Its historical source and conditions remain separate from the later native service samples.
- Finite native public campaigns demonstrated mixed LoRa/IP delivery and, separately, IP loss, verified-piece retention, freshly admitted LoRa resume and return to a locally measured faster IP path.

[Performance](PERFORMANCE.md) contains the results, units and source references. [The security comparison](COMPARISON.md#security-properties-and-gaps) explains the current boundaries. Better overall speed and security than both upstream projects, native private forward secrecy, autonomous routing, range at faster profiles and successful large-file radio transfers remain goals.

## What follows

The full [qualification roadmap](ROADMAP.md) remains active, including 100 kB and 1 MB file-bundle measurements, sustained performance, latency, native private admission and firmware qualification. Source tests, rendered diagrams and a published website do not complete those gates. [Release readiness](RELEASE_READINESS.md) retains the separate artifact and alpha requirements.

Before the repository becomes public, review the current source/package boundary, retained history, notices and credential-scan scope. Earlier Lantern-containing extraction commits are intentionally preserved as [development history](REPOSITORY_HISTORY.md), while current source and distributions remain separate. The website deploys from the intended public source after review. The [website source](../website/index.html) and [logo](../website/assets/meshticulum-logo.svg) live in this repository.
