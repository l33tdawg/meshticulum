# Project preview

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran](https://github.com/l33tdawg). The source and project website introduce the design, invite review and share measured results while development continues. A packaged `1.0.0-alpha` release will follow its own readiness review.

## Scope of the announcement

The repository contains reusable protocol, crypto, reliable flow, radio/IP drivers and verified public-resource code. It includes architecture diagrams, comparisons with published upstream figures, source tests and benchmark metadata. The original preview used `0.1.0`; the current development package uses `1.0.0a1`. The source and project website are public, while the alpha tag, release and downloadable SDK/firmware assets remain unpublished.

[SAGE](https://github.com/l33tdawg/sage/) supplies the broader agent-memory project. [SAGE Lantern](https://github.com/l33tdawg/sage-lantern) owns product UI, protected SAGE adapters, native device/profile policy, provisioning, pilot control and deployment. Lantern consumes Meshticulum. Those product implementations and private deployment materials belong in Lantern's separate repository.

## Claims supported today

- Meshticulum has its own experimental protocol, combining practical radio-mesh priorities with authenticated networking and verified resources across LoRa and IP. Existing MeshCore/Reticulum wire interoperability is not implemented.
- A completed native GFSK250 campaign delivered **100,000 decoded bytes** in five signed MB1 parts, with publisher and whole-file verification. Its rates were **1,185.6 B/s during DATA**, **945.7 B/s including sender startup**, **296.2 B/s from request to independent publisher verification**, and **107.0 B/s over the whole controller operation**. These are four boundaries of the same finite run; the startup-inclusive rate remains below the alpha floor. [Conditions and timing definitions](PERFORMANCE.md#a-complete-100-kb-file-2026-10-15) accompany the result.
- An earlier accepted native public response delivered **16,356 verified bytes at 1,071.3 B/s** from sender start to its final reliable-flow ACK, with zero DATA retries or duplicates. Full request-to-publisher verification took about **80.7 seconds**, equivalent to **202.6 B/s**. This historical sample does not establish sustained file-transfer capacity.
- That earlier response's sender-start-to-ACK rate is numerically **2.61 times** the **410 B/s** MeshCore test published by this project's creator in [MeshCore issue #3404](https://github.com/meshcore-dev/MeshCore/issues/3404). Both use SF7/BW500; workloads, devices and timing boundaries differ. It is a scoped comparison between our published observations, not a universal speed ranking or an independently certified upstream benchmark.
- An older 30-sample radio experiment recorded about **1.2 kB/s median** useful encrypted goodput. Its historical source and conditions remain separate from the later native service samples.
- Finite native public campaigns demonstrated mixed LoRa/IP delivery and, separately, IP loss, verified-piece retention, freshly admitted LoRa resume and return to a locally measured faster IP path.

[Performance](PERFORMANCE.md) contains the results, units and source references. [The security comparison](COMPARISON.md#security-properties-and-gaps) explains the current boundaries. Better overall speed and security than both upstream projects, native private forward secrecy, autonomous routing, range at faster profiles and sustained large-file performance remain goals.

## What follows

The [roadmap](ROADMAP.md) separates current evidence, alpha work and later research. The immediate target is sustained useful encrypted delivery above **1,000 B/s including sender startup**, reliable completion and deployable SDK/firmware. Repeated larger-file measurements, latency, native private admission and firmware qualification remain open; 4–5 kB/s is an optional later stretch. [Release readiness](RELEASE_READINESS.md) retains the artifact and alpha requirements.

The public repository and website retain their reviewed source/package boundary and attribution. Earlier Lantern-containing extraction commits remain as [development history](REPOSITORY_HISTORY.md), while the current package stays separate. Exact release-tree, artifact, history, notices and unintended-data review still applies before publishing an alpha. The [website source](../website/index.html) and [logo](../website/assets/meshticulum-logo.svg) live in this repository.
