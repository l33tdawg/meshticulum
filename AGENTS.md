# Meshticulum

Use canonical `meshticulum.*` imports. Keep the core and public-resource code independent of the optional SAGE SDK and production services.

`src/meshticulum/flow/flow.py` and `flow_port.py` are the accepted frozen candidate. Preserve their pins and current writer ownership; changing them requires an explicit reviewed flow revision. The bridges must preserve module/class identity.

Run the standalone suite and `tools/verify_frozen_flow.py` for transport changes. Private adapters additionally require the compatible local Lantern SDK and their integration controls. A test with a modeled worker or SDK MockTransport is source evidence only.

Preserve original peer bindings, deadlines, boot identity, replay decisions, unknown writes, revocation and attempt/airtime debt. A later grant or restart does not authorize expired work or refund old debt.

Hardware actions need their own current finite authorization, coordinated device ownership and bounded cleanup. Source extraction, imports, help, runtime builds and installation do not grant RF authority. Preserve production services and private data.

Keep failures and missing physical measurements visible. Do not publish physical throughput, application delivery or production qualification from a source test result.

See docs/EXTRACTION.md, docs/HARDWARE.md and docs/ROADMAP.md for inherited provenance and open qualification.
