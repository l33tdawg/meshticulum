# Prospective 1.0.0-alpha readiness

**Status on 2026-10-10: proposal under review, not a release.** [Meshticulum](https://github.com/l33tdawg/meshticulum) is private, its package version remains `0.1.0`, and [PR #1](https://github.com/l33tdawg/meshticulum/pull/1) tracks the draft candidate. The strict [Lantern → Meshticulum boundary](PROJECT_FAMILY.md) is being prepared for review. Final candidate CI and separately owned Lantern regressions need evidence tied to the exact dependency and integration commits. No alpha tag, public release or visibility change is represented here.

## What this alpha would mean

The proposed public alpha is an installable experimental transport/resource package with reviewed boundaries, reproducible artifacts and explicit limitations. Its candidate is prepared for review while the repository remains private. It would include the frozen MT05 flow, reusable bounded radio workers, authenticated TCP source prototype, MR1 public-resource interfaces, verified-piece resume and offline recipient-encrypted envelopes.

[SAGE Lantern](https://github.com/l33tdawg/sage-lantern) owns private SAGE adapters, protected capture and policy, native Linux node/pilot entry points, provisioning, runtime bundles and device/fleet deployment. Those implementations are not distributed in the Meshticulum package. Lantern consumes an explicitly pinned Meshticulum dependency; Meshticulum does not import Lantern or require its SDK.

The TCP prototype has actual loopback controls. Lantern's native entry-point controls use modeled host facts. Earlier physical radio results remain bound to their historical runtime. Neither establishes a new native public LoRa/IP pilot or private application admission. See [IP transport](IP_TRANSPORT.md), [the hardware-pilot boundary](HARDWARE_PILOT.md) and [performance](PERFORMANCE.md).

An alpha does not promise stable wire/API compatibility, autonomous routing, a MeshCore/Reticulum packet gateway or production private dispatch. It also does not complete the active programme. Its hardware, private-backend, benchmark and public-publication gates remain in [ROADMAP.md](ROADMAP.md).

## Proposed version and artifacts

Use Python version **`1.0.0a1`** and, only after readiness review and a release decision, Git tag **`v1.0.0-alpha`** on the same exact commit. The GitHub release title is **`1.0.0-alpha`**. Keep `0.1.0` until that candidate is intentionally prepared; there is no tag to consume now.

Prepare these artifacts for review:

- A wheel and source distribution built from the candidate commit, with SHA-256 hashes, dependency versions, Apache-2.0 license and retained notices. Check installation and canonical imports independently of the SAGE parent checkout.
- A reviewed Meshticulum distribution manifest containing reusable core code and required support files. Exclude Lantern/SAGE adapters, native deployment tools, protected data and product configuration. Any public-only host-runtime archive used for qualification is a separately owned Lantern artifact, with its own exact dependency, source manifest and launcher hash; building it grants no device or dispatch authority.
- Candidate validation records: standalone tests, frozen-flow verification, documentation/benchmark checks, Python CI matrix and installed-artifact smoke checks. Pair them with Lantern's compatible-SDK and native integration records for the exact dependency pin. Record skips, failures, toolchain versions and scope alongside hashes.
- Release notes stating experimental behavior, supported entry points, original-deadline/replay/debt semantics, known limitations and retained benchmark conditions. Link every claimed result to evidence; keep unavailable measurements null.

The [extraction validation record](../provenance/VALIDATION.json) is a historical checkpoint, not proof that a later candidate passed. The current Meshticulum artifact and its paired Lantern integration each require their own records. Moving application code cannot carry qualification forward automatically.

## Reviewer checklist for the source alpha

- [ ] Review covers the exact Meshticulum candidate, one-way dependency and excluded application files, plus the paired Lantern commit for native provisioning, request binding, local quarantine and publisher verification. Pending changes and superseded test runs are distinguished from those candidates.
- [ ] Standalone tests and the Python 3.11–3.13 [CI matrix](../.github/workflows/tests.yml) pass on that commit. Run [`verify_frozen_flow.py`](../tools/verify_frozen_flow.py), [`check_docs.py`](../tools/check_docs.py) and [`verify_benchmark_report.py`](../tools/verify_benchmark_report.py).
- [ ] Lantern's private/native integration controls pass against its documented compatible SDK and exact Meshticulum pin. Report modeled ownership/MockTransport limitations; do not infer native qualification. No private adapter tests or SDK installation extra are required by the Meshticulum package.
- [ ] The Meshticulum wheel and source distribution reproduce their recorded manifests, contain required support files and preserve frozen-flow pins. A paired Lantern host bundle has independent evidence. Inspect all distributed contents for unintended parent-application, private-data or third-party artifacts.
- [ ] Expiry, boot binding, revocation, unknown writes and lifetime debt remain intact through cancellation, restart and fresh child admissions. Public quarantine cannot become a verified piece without request, manifest and publisher checks.
- [ ] Documentation distinguishes kernel acceptance, RF completion, authenticated peer receipt, durable bytes and application acceptance. Historical radio medians are about 1.2 kB/s; 1.5 kB/s remains a target, with matched upstream measurements absent.

## Gates that remain open

Earlier private development commits contain the extracted Lantern implementations. Before public visibility, prepare and review a Meshticulum-only initial history and inspect every retained branch and tag. A clean current tree or wheel does not remove files from Git history. Preserve the historical development snapshot privately; no history rewrite or visibility change is authorized by this migration. The [boundary record](../provenance/REPOSITORY_BOUNDARY.json) identifies the moved implementations.

Lantern owns native execution and deployment; the qualification requirements remain unchanged. Current native ownership and finite public runs still require a fresh coordinated device window, actual C→A LoRa→B IP, bounded cleanup and independent recipient publisher verification. Failure testing must retain debt, admit a fresh LoRa path, reuse only verified pieces under the original deadline, and return to IP only when current verified timing proves it faster.

The private programme additionally requires the real encrypted backend and native owner/consent/revocation lifecycle. Matched MeshCore/Reticulum tests must use comparable hardware, settings, privacy, completion semantics and total request/response/ACK/retry cost. Sustained useful RF goodput and latency remain separate qualification requirements.

A reproducible firmware/BSP recipe, third-party license inventory and release review remain required for the paired Lantern hardware qualification and its distributed artifacts. Meshticulum's own wheel/source notices and package review also remain required before public publication. A candidate prepared in the private repository cannot waive inherited requirements or stand in for the full [roadmap](ROADMAP.md).

The public alpha still needs the coordinated native public pilot, its failure/resume evidence, the paired standalone firmware recipe and complete notices for the exact distributed artifacts. Lantern must record the Meshticulum pin used for that evidence. Private production dispatch and matched upstream performance claims remain gated separately; the alpha must clearly report their absence rather than imply qualification.

When these public-alpha gates are satisfied, notify the user with the exact candidate commit, review link, artifact hashes and validation scope. Preparing the candidate does not authorize changing visibility, creating a tag or publishing packages. See the [draft release notes](../CHANGELOG.md).
