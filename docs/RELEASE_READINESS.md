# Prospective 1.0.0-alpha readiness

**Status on 2026-10-10: proposal under review, not a release.** [Meshticulum](https://github.com/l33tdawg/meshticulum) is private, its package version is `0.1.0`, and [PR #1](https://github.com/l33tdawg/meshticulum/pull/1) is draft. Native public-pilot provisioning, quarantine handling and publisher-verification helpers are being source-tested. Final candidate CI and the optional SAGE regression run still need evidence tied to the exact candidate commit. No alpha tag, public release or visibility change is represented here.

## What this alpha would mean

The proposed public alpha is an installable experimental transport/resource package with reviewed boundaries, reproducible artifacts and explicit limitations. Its candidate is prepared for review while the repository remains private. It would include the frozen MT05 flow, bounded radio workers, authenticated TCP source prototype, MR1 public-resource adapters, verified-piece resume, offline private envelopes and separately optional SAGE adapters.

The TCP prototype has actual loopback controls; native entry-point controls use modeled host facts. Earlier physical radio results remain bound to their historical runtime. Neither establishes the new native public LoRa/IP pilot or private application admission. See [IP transport](IP_TRANSPORT.md), [the hardware pilot](HARDWARE_PILOT.md) and [performance](PERFORMANCE.md).

An alpha does not promise stable wire/API compatibility, autonomous routing, a MeshCore/Reticulum packet gateway or production private dispatch. It also does not complete the active programme. Its hardware, private-backend, benchmark and public-publication gates remain in [ROADMAP.md](ROADMAP.md).

## Proposed version and artifacts

Use Python version **`1.0.0a1`** and, only after readiness review and a release decision, Git tag **`v1.0.0-alpha`** on the same exact commit. The GitHub release title is **`1.0.0-alpha`**. Keep `0.1.0` until that candidate is intentionally prepared; there is no tag to consume now.

Prepare these artifacts for review:

- A wheel and source distribution built from the candidate commit, with SHA-256 hashes, dependency versions, Apache-2.0 license and retained notices. Check installation and canonical imports independently of the SAGE parent checkout.
- A public-only host-runtime archive, exact source manifest and launcher hash. Its private services must remain disabled. Building the archive does not install it, provision a grant, open a connection or arm a radio. No firmware binary or third-party BSP is implied.
- Candidate validation records: standalone and optional-SAGE reports, frozen-flow verification, documentation/benchmark checks, Python CI matrix and installed-artifact smoke checks. Record skips, failures, toolchain versions and scope alongside hashes.
- Release notes stating experimental behavior, supported entry points, original-deadline/replay/debt semantics, known limitations and retained benchmark conditions. Link every claimed result to evidence; keep unavailable measurements null.

The [extraction validation record](../provenance/VALIDATION.json) is a historical checkpoint, not proof that a later candidate passed. New native/publisher helpers and any included optional integration must be covered by the candidate's own records.

## Reviewer checklist for the source alpha

- [ ] PR review covers the exact candidate commit, including native provisioning, request binding, local quarantine and publisher verification. Pending changes and superseded test runs are distinguished from the candidate.
- [ ] Standalone tests and the Python 3.11–3.13 [CI matrix](../.github/workflows/tests.yml) pass on that commit. Run [`verify_frozen_flow.py`](../tools/verify_frozen_flow.py), [`check_docs.py`](../tools/check_docs.py) and [`verify_benchmark_report.py`](../tools/verify_benchmark_report.py).
- [ ] Optional SAGE controls pass against the documented compatible local SDK when the adapter is included. Report modeled ownership/MockTransport limitations; do not infer native qualification.
- [ ] The wheel, source distribution and host bundle reproduce their recorded manifests, contain required support files and preserve frozen-flow pins. Inspect their contents for unintended parent-application, private-data or third-party artifacts.
- [ ] Expiry, boot binding, revocation, unknown writes and lifetime debt remain intact through cancellation, restart and fresh child admissions. Public quarantine cannot become a verified piece without request, manifest and publisher checks.
- [ ] Documentation distinguishes kernel acceptance, RF completion, authenticated peer receipt, durable bytes and application acceptance. Historical radio medians are about 1.2 kB/s; 1.5 kB/s remains a target, with matched upstream measurements absent.

## Gates that remain open

Current native ownership and finite public-run qualification still require a fresh coordinated device window, actual C→A LoRa→B IP, bounded cleanup and independent recipient publisher verification. Failure testing must retain debt, admit a fresh LoRa path, reuse only verified pieces under the original deadline, and return to IP only when current verified timing proves it faster.

The private programme additionally requires the real encrypted backend and native owner/consent/revocation lifecycle. Matched MeshCore/Reticulum tests must use comparable hardware, settings, privacy, completion semantics and total request/response/ACK/retry cost. Sustained useful RF goodput and latency remain separate qualification requirements.

A reproducible standalone firmware/BSP recipe, third-party license inventory and release review remain required before public/production publication. A candidate prepared in the private repository cannot waive those requirements or stand in for the full [roadmap](ROADMAP.md).

The public alpha needs the coordinated native public pilot, its failure/resume evidence, a standalone firmware recipe and complete notices for the exact distributed artifacts. Private production dispatch and matched upstream performance claims remain gated separately; the alpha must clearly report their absence rather than imply qualification.

When these public-alpha gates are satisfied, notify the user with the exact candidate commit, review link, artifact hashes and validation scope. Preparing the candidate does not authorize changing visibility, creating a tag or publishing packages. See the [draft release notes](../CHANGELOG.md).
