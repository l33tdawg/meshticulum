# Meshticulum documentation

Meshticulum brings bounded radio transfers, authenticated service boundaries and verified resources into a system intended to span LoRa and IP. This guide starts with the design, then follows the source and the evidence needed to qualify it. [Experimental alpha.3](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha.3) adds durable journal accounting improvements and optional host phase timings; start with the [SDK installation guide](SDK_GUIDE.md#install-the-alpha-or-checkout).

## Understand the project

| Guide | What it explains |
| --- | --- |
| [Project preview](PROJECT_PREVIEW.md) | The public source and website scope, measured claims and published alpha |
| [Roadmap](ROADMAP.md) | Current evidence, the published alpha scope and plans after alpha |
| [Repository history](REPOSITORY_HISTORY.md) | Preserved extraction history and the current Meshticulum/Lantern boundary |
| [Attribution and project family](PROJECT_FAMILY.md) | SAGE Foundation stewardship, the main SAGE repository and the separate Lantern product repository |
| [Why Meshticulum](WHY.md) | The gap we are trying to close and the priorities drawn from embedded radio meshes and secure networking |
| [Architecture](ARCHITECTURE.md) | Component diagrams, service separation, a transfer's lifecycle and the intended mixed-path topology |
| [MeshCore and Reticulum comparison](COMPARISON.md) | The upstream designs, their strengths, and what Meshticulum implements differently |
| [Public SDK radio measurements](RADIO_SDK_MEASUREMENTS.md) | Inspect the GFSK files and the 24 October A/B LoRa distance sample |
| [Host-backed MCU acceleration](MCU_ACCELERATION.md) | Review opt-in DATA queueing, durable physical accounting and the measured 24 October comparison |
| [MCU burst protocol](MCU_BURST_PROTOCOL.md) | Inspect immutable handover policy, exact serial records, clock bounds and closure requirements |
| [HTTP over radio](HTTP_GATEWAY.md) | Inspect the one-request text-browser gateway, its 53-second cold path and its limits |
| [Performance and measurement](PERFORMANCE.md) | Useful encrypted goodput, reported results, exact units and the conditions for a fair comparison |
| [Post-quantum status](POST_QUANTUM.md) | The classical default, hybrid prototype, radio overhead and remaining device work |

The project combines design ideas in its own protocol. Existing MeshCore and Reticulum wire interoperability is not implemented. Authentication, resource verification and path scheduling each have their own responsibility; passing one boundary does not establish all the others.

## Run and develop it

Start with [installation and the standalone test command](../README.md#install-and-explore). The core and public-resource modules run without SAGE. Use the canonical `meshticulum.*` namespace in new code.

| Guide | Use it when you need to… |
| --- | --- |
| [Tester guide](TESTER_GUIDE.md) | Try the desktop example, build firmware, choose a flashing path and report a result |
| [Contributing](../CONTRIBUTING.md) | Choose a tester, documentation or offline source contribution and run the review checks |
| [Transport source update](TRANSPORT_SOURCE_UPDATE.md) | Review the GFSK, byte-bundle and opaque-relay source additions and their qualification limits |
| [Signed radio file example](../examples/BUNDLE.md) | Compose the existing MB1 service for five-part 100 kB transfer under independently approved grants |
| [App-only flashing and rollback](FLASHING.md) | Check a compatible current layout and rollback before installing an application on your own board |
| [Hardware tooling](HARDWARE.md) | Build a public host runtime, understand serial/SX1262 adapters, or prepare a separately authorized device run |
| [Third-party backend contract](THIRD_PARTY_BACKEND_CONTRACT.md) | Implement and test a new LoRa backend/profile offline without weakening worker, debt or authority boundaries |
| [Authenticated TCP prototype](IP_TRANSPORT.md) | Understand the explicit IP grants, durable counters, stream-byte budgets and source controls |
| [Lantern's SAGE integration](SAGE_INTEGRATION.md) | Understand the separate owning repository for protected capture, replay and private inbox admission |
| [Extraction and provenance](EXTRACTION.md) | Trace code to its original snapshot, inspect frozen flow pins or understand package bridges |
| [Coordinated public pilot](HARDWARE_PILOT.md) | Follow native provisioning, relay provenance, publisher verification and the remaining physical checks |
| [Alpha release status](RELEASE_READINESS.md) | Check alpha.3 artifacts and hardware evidence, earlier releases and remaining qualification |
| [Third-party inventory](THIRD_PARTY.md) | Identify runtime and firmware dependencies and outstanding redistribution requirements |
| [Repository instructions](../AGENTS.md) | Change transport or integration behavior while preserving its contracts and writer ownership |
| [Frozen flow history](../src/meshticulum/flow/README.md) | Inspect the accepted candidate's revisions, invariants and historical source controls |

The flow history includes references to lab receipts in the original SAGE checkout. Those historical paths are provenance references; they are not artifacts included in a fresh Meshticulum clone.

## Follow qualification

The [roadmap](ROADMAP.md) separates current evidence, published alpha scope and later work. The alpha records useful encrypted delivery above 1 kB/s (8 kbit/s) including sender startup on its declared near-bench path, reliable verified completion and a deployable SDK/firmware source-build path. The later 250 kB, 500 kB and 1 MB files reach 2.111, 2.216 and 2.230 kB/s whole-file, clearing the owner's 2.0 kB/s floor; a first 1 MB sample measured 1.750 kB/s under heavy sender-host load and stays in the record. Three HTTP gateway browser requests rendered public pages in 17.77–17.99 seconds with a normal 30-second timeout. Wider mixed/native integration, private security and field qualification remain required. The optional 4 to 5 kB/s (32 to 40 kbit/s) stretch and post-quantum feasibility work belong after the baseline.

Read [Performance](PERFORMANCE.md) alongside that roadmap. Source controls, modeled workers and loopback transfers provide useful implementation evidence. Physical RF completion, peer receipt, publisher verification and production application acceptance need their respective observations.

For current code, use [the source suite and CI workflow](../.github/workflows/tests.yml). For attribution and redistribution, read [LICENSE](../LICENSE), [NOTICE](../NOTICE) and [extraction notes](EXTRACTION.md).

## Maintain the diagrams

GitHub renders the Mermaid blocks in the Markdown. Every figure also links to an editable `.mmd` source and a rendered SVG for other viewers. Update the Markdown first, then regenerate with `python tools/render_diagrams.py` after installing Mermaid CLI (`mmdc`). Use `--puppeteer-config` when your local Chrome executable needs an explicit path. The rendering theme is in `docs/diagrams/config.json`.

`python tools/check_docs.py` checks local links and source consistency. `python tools/verify_benchmark_report.py` checks the published measurement arithmetic; `python tools/render_benchmark_plot.py` rebuilds its figure with Matplotlib. All operate on source or published metadata and open no hardware.


[Throughput pipeline work](THROUGHPUT_PIPELINE.md) records the existing verified
piece behavior, bounded host wakeups, dormant MCU batch prototype, above
2.8 kB/s acceptance target and 3 kB/s stretch qualification work.
