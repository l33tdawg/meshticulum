# Meshticulum

Use canonical `meshticulum.*` imports. Keep Meshticulum independent of Lantern, the SAGE SDK, protected application storage and production services. The dependency direction is Lantern → Meshticulum.

Private SAGE adapters, capture/inbox/policy implementations, native Linux product entry points, grant provisioning, fleet services and device deployment tools belong to [SAGE Lantern](https://github.com/l33tdawg/sage-lantern), under `software/meshticulum-integration/` and `firmware/meshticulum-bench/`. Do not add those implementations, installation extras or private application tests back to this repository. Keep generic crypto/protocol/resource code, drivers and public service interfaces reusable by other applications.

`src/meshticulum/flow/flow.py` and `flow_port.py` are the accepted frozen candidate. Preserve their pins and current writer ownership; changing them requires an explicit reviewed flow revision. The bridges must preserve module/class identity.

Run the standalone suite and `tools/verify_frozen_flow.py` for transport changes. A test with a modeled worker or synthetic application authority is source evidence only. Lantern owns its compatible-SDK and native integration regressions, pinned to the exact Meshticulum dependency.

Preserve original peer bindings, deadlines, boot identity, replay decisions, unknown writes, revocation and attempt/airtime debt. A later grant or restart does not authorize expired work or refund old debt.

Hardware actions need their own current finite authorization, coordinated device ownership and bounded cleanup. Source imports, builds and installation do not grant RF authority. Device execution and deployment are owned by Lantern; preserve production services and private data.

Keep failures and missing physical measurements visible. Do not publish physical throughput, application delivery or production qualification from a source test result. No version, tag, repository visibility or publication change is implied by boundary work.

See docs/PROJECT_FAMILY.md, docs/EXTRACTION.md, docs/HARDWARE.md and docs/ROADMAP.md for ownership, provenance and open qualification.
