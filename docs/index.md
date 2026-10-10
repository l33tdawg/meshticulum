# Meshticulum documentation

Meshticulum brings bounded radio transfers, authenticated service boundaries and verified resources into a system intended to span LoRa and IP. This guide starts with the design, then follows the source and the evidence needed to qualify it.

## Understand the project

| Guide | What it explains |
| --- | --- |
| [Why Meshticulum](WHY.md) | The gap we are trying to close and the priorities drawn from embedded radio meshes and secure networking |
| [Architecture](ARCHITECTURE.md) | Component diagrams, service separation, a transfer's lifecycle and the intended mixed-path topology |
| [MeshCore and Reticulum comparison](COMPARISON.md) | The upstream designs, their strengths, and what Meshticulum implements differently |
| [Performance and measurement](PERFORMANCE.md) | Useful encrypted goodput, reported results, exact units and the conditions for a fair comparison |

The project combines design ideas in its own protocol. Existing MeshCore and Reticulum wire interoperability is not implemented. Authentication, resource verification and path scheduling each have their own responsibility; passing one boundary does not establish all the others.

## Run and develop it

Start with [installation and the standalone test command](../README.md#install-and-explore). The core and public-resource modules run without SAGE. Use the canonical `meshticulum.*` namespace in new code.

| Guide | Use it when you need to… |
| --- | --- |
| [Hardware tooling](HARDWARE.md) | Build a public host runtime, understand serial/SX1262 adapters, or prepare a separately authorized device run |
| [Authenticated TCP prototype](IP_TRANSPORT.md) | Understand the explicit IP grants, durable counters, stream-byte budgets and source controls |
| [Optional SAGE integration](SAGE_INTEGRATION.md) | Supply the compatible local SDK and work with protected capture, replay and private inbox admission |
| [Extraction and provenance](EXTRACTION.md) | Trace code to its original snapshot, inspect frozen flow pins or understand package bridges |
| [Repository instructions](../AGENTS.md) | Change transport or integration behavior while preserving its contracts and writer ownership |
| [Frozen flow history](../src/meshticulum/flow/README.md) | Inspect the accepted candidate's revisions, invariants and historical source controls |

The flow history includes references to lab receipts in the original SAGE checkout. Those historical paths are provenance references; they are not artifacts included in a fresh Meshticulum clone.

## Follow qualification

The [roadmap](ROADMAP.md) retains the full programme: finite public-radio qualification, authenticated IP transport, C→A LoRa→B IP, loss and verified-piece resume, return to a locally measured faster path, native private integration and matched MeshCore/Reticulum measurements.

Read [Performance](PERFORMANCE.md) alongside that roadmap. Source controls, modeled workers and loopback transfers provide useful implementation evidence. Physical RF completion, peer receipt, publisher verification and production application acceptance need their respective observations.

For current code, use [the source suite and CI workflow](../.github/workflows/tests.yml). For attribution and redistribution, read [LICENSE](../LICENSE), [NOTICE](../NOTICE) and [extraction notes](EXTRACTION.md).

## Maintain the diagrams

GitHub renders the Mermaid blocks in the Markdown. Every figure also links to an editable `.mmd` source and a rendered SVG for other viewers. Update the Markdown first, then regenerate with `python tools/render_diagrams.py` after installing Mermaid CLI (`mmdc`). Use `--puppeteer-config` when your local Chrome executable needs an explicit path. The rendering theme is in `docs/diagrams/config.json`.

`python tools/check_docs.py` checks local links and source consistency. `python tools/verify_benchmark_report.py` checks the published measurement arithmetic; `python tools/render_benchmark_plot.py` rebuilds its figure with Matplotlib. All operate on source or published metadata and open no hardware.
