# Source extraction

This repository starts from a reviewed working-tree snapshot of the Meshticulum sources in SAGE Lantern on 2026-10-10. It does not contain the entire SAGE application or its Git history. The parent checkout and its unrelated work were preserved.

`provenance/UPSTREAM_SOURCE.json` records the parent repository, HEAD, dirty snapshot status, original file paths, source hashes, destination paths and hashes of the extracted files. Source attribution, Apache-2.0 LICENSE and upstream NOTICE are retained. Each changed file is identified there; changes made during extraction are namespace and repository path adaptations, test collection adaptations and standalone public-runtime packaging.

The accepted flow and flow port are copied without modification. Their source pins remain:

- `flow.py`: `9a8d4bb3bea2f69581d80ecd31fda7692c2435fe1363be53564d5281485cb857`
- `flow_port.py`: `57596421d916f0c49d61599653b5ab75154d64e5bc806ba1444fa5905ad025ca`

The frozen port retains its relative `meshticulum_contracts` and `meshticulum_scheduler` imports. Small bridge packages alias their child modules to the canonical packages. They preserve the actual class objects used by the scheduler and lease checks. `bench.flow_runtime` now imports the canonical `meshticulum.flow` package rather than constructing a package for a directory with a hyphen.

Offline envelope codecs moved to `meshticulum.privacy`. Their existing schemas, signature domains, authenticated content, bounds and ciphertext are unchanged. The 24-hour window ceiling is a standalone constant with the same value as the original consent ceiling. Changing that ceiling requires protocol and application consent review.

Public resource codecs moved to `meshticulum.resources`. SAGE SDK/storage dependencies are confined to the optional `meshticulum.integrations.sage` adapter. Public service controls are also runnable independently of the private integration fixtures.

The public runtime builder packages the new namespace and preserves the frozen pins, source hashes, finite-grant requirements and private-service refusal. Its archive is a new artifact: an earlier SAGE-side runtime hash must not be relabeled as this build.

Historical comments and specifications may refer to review receipts under the parent repository's `work/` directory. Those receipts, real grants, device captures, secrets, binaries and private content are not copied here. No physical qualification transfers merely because the source has moved.
