# Meshticulum documentation

Meshticulum brings bounded radio transfers, authenticated service boundaries and verified resources into a system intended to span LoRa and IP. This guide starts with the design, then follows the source and the evidence needed to qualify it. [Experimental alpha.4](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha.4) repairs saturated train12 recovery stalls and adds three repeated physical SF5 samples above 3 kB/s; start with the [SDK installation guide](SDK_GUIDE.md#install-the-alpha-or-checkout).

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
| [Alpha release status](RELEASE_READINESS.md) | Check alpha.4 artifacts and hardware evidence, earlier releases and remaining qualification |
| [Third-party inventory](THIRD_PARTY.md) | Identify runtime and firmware dependencies and outstanding redistribution requirements |
| [Repository instructions](../AGENTS.md) | Change transport or integration behavior while preserving its contracts and writer ownership |
| [Frozen flow history](../src/meshticulum/flow/README.md) | Inspect the accepted candidate's revisions, invariants and historical source controls |

The flow history includes references to lab receipts in the original SAGE checkout. Those historical paths are provenance references; they are not artifacts included in a fresh Meshticulum clone.

## Follow qualification

The [roadmap](ROADMAP.md) separates current evidence, published alpha scope and later work. Alpha 4 records three useful encrypted deliveries above 3 kB/s (24 kbit/s) including sender startup on its declared near-bench SF5 path, with reliable verified completion and a deployable SDK/firmware source-build path. The 250 kB, 500 kB and 1 MB files reach 2.111, 2.216 and 2.230 kB/s whole-file, clearing the owner's 2.0 kB/s floor; a first 1 MB sample measured 1.750 kB/s under heavy sender-host load and stays in the record. Three HTTP gateway browser requests rendered public pages in 17.77–17.99 seconds with a normal 30-second timeout. Sustained larger-file performance, wider mixed/native integration, private security and field qualification remain required. The optional 4 to 5 kB/s (32 to 40 kbit/s) stretch and post-quantum feasibility work belong after the baseline.

Read [Performance](PERFORMANCE.md) alongside that roadmap. Source controls, modeled workers and loopback transfers provide useful implementation evidence. Physical RF completion, peer receipt, publisher verification and production application acceptance need their respective observations.

For current code, use [the source suite and CI workflow](../.github/workflows/tests.yml). For attribution and redistribution, read [LICENSE](../LICENSE), [NOTICE](../NOTICE) and [extraction notes](EXTRACTION.md).

## Maintain the diagrams

GitHub renders the Mermaid blocks in the Markdown. Every figure also links to an editable `.mmd` source and a rendered SVG for other viewers. Update the Markdown first, then regenerate with `python tools/render_diagrams.py` after installing Mermaid CLI (`mmdc`). Use `--puppeteer-config` when your local Chrome executable needs an explicit path. The rendering theme is in `docs/diagrams/config.json`.

`python tools/check_docs.py` checks local links and source consistency. `python tools/verify_benchmark_report.py` checks the published measurement arithmetic; `python tools/render_benchmark_plot.py` rebuilds its figure with Matplotlib. All operate on source or published metadata and open no hardware.


[Throughput pipeline work](THROUGHPUT_PIPELINE.md) records the existing verified
piece behavior, bounded host wakeups and dormant MCU batch prototype. Alpha 4
met the repeated physical goal for three finite files at least 3,000 useful
bytes/s including startup. The [SPI8 sample](../benchmarks/2026-10-30-sf5-spi8-profile/README.md)
records faster native setup alongside lower file goodput under different losses.

[Bounded scalar fill pair](SCALAR_FILL_PAIR.md) describes the explicit v6 policy
and its source controls. The [separate physical sample](../benchmarks/2026-10-30-sf5-scalar-fill-pair-profile/README.md)
verified the same file at 1,222.394221 B/s on unchanged SPI8 firmware and boots;
differing loss patterns prevent an isolated policy comparison.

The [header-preservation physical sample](../benchmarks/2026-10-30-sf5-header-preserve-profile/README.md)
then verified the same file at 1,690.381876 B/s with the same v6 host runtime and
8 MHz SPI, after a genuine firmware upgrade. Different loss and boots prevent
an isolated causal comparison. Its diagnostic header-only category does not
prove an actual RX restart or packet drop. All eight samples remain below the
throughput goal.

[Repeat selective repair](FLOW_R13_REPEAT_RETRY_REVIEW.md) records the accepted
R13/v7 source revision and its fresh-proof requirements. Its first
[physical sample](../benchmarks/2026-10-30-sf5-repeat-retry-profile/README.md)
verified 23,900 useful bytes at 1,577.815726 B/s including startup on the same
firmware and MCU boots as the preceding sample. All 18 DATA retries were
selective; differing loss prevents an isolated policy comparison. All nine
earlier completed samples were below the throughput targets.

The subsequent [32-symbol-preamble trial](../benchmarks/2026-10-30-sf5-preamble32-failure/README.md)
failed during startup on the same SDK/v7 policy, firmware and MCU boots. The
receiver spent 16 startup ACK attempts; the sender sent no DATA and its native
header-only IRQ category rose by 16, with no exported packets. No file completed,
so this trial has no goodput result. Both units were subsequently observed idle
with empty queues and unchanged tracked services. The failed outcome and full reserved
allocation remain retained. The IRQ category can include selective clearing
without an RX restart; the underlying receive failure is unresolved.

The latest [sync18/preamble32 sample](../benchmarks/2026-10-30-sf5-sync18-profile/README.md)
then verified **23,900 useful bytes in 8.321713 seconds including startup:
2,872.004838 B/s**, with zero DATA retries. This clears the above-2,800 B/s
checkpoint once. It kept the SDK/v7 policy, firmware and both MCU boots and
changed the explicit sync word to `0x12`; this does not prove the cause of the
preceding failure. Repeated 3,000 B/s and comparisons against MeshCore or
Reticulum remain unqualified. The v8/v9 source work below was not used in
this physical run.

The final Alpha 4 [repeat qualification](../benchmarks/2026-10-30-sf5-alpha4-repeat-profile/README.md)
completed three fresh exact-wheel SF5 files at **3,149.246, 3,188.156 and
3,192.081 B/s** including sender startup. Each used the repaired train12
policy, accepted 12 authenticated ACKs, verified the whole file, closed both
endpoint children without forced cleanup and had one DATA retransmission.
This clears both the 2.8 kB/s required checkpoint and the 3.0 kB/s stretch
target for three finite near-bench samples. Sustained larger-file throughput,
field range and matched upstream comparisons remain open.

[Terminal repair trains](TERMINAL_REPAIR_TRAIN.md) are now accepted in source as
an explicit v8 policy. After original FINAL handover and authenticated peer
receipt, they group distinct, already proven missing frames into trains of at
most eight packets. The original per-frame accounting and listening floors
remain. Source controls passed, but v8 has no physical qualification result.

[Twelve-frame trains with eight queue slots](MCU_TWELVE_TRAIN.md) describes the
explicit v9 source experiment, its new capability/OPEN binding and paired
source model. It retains the original two-second deadline and receive floors;
no physical qualification or throughput gain is claimed.
