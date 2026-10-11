# Repository history and the Lantern boundary

Meshticulum began inside SAGE Lantern. Early development commits in this repository therefore contain Lantern adapters, native pilot/provisioning tools and application integration tests. We preserve that development history rather than erase the record of the extraction. Those historical files are visible through old commits and PRs; they are not part of the current Meshticulum package.

The current source tree removes those implementations. Meshticulum owns reusable protocol, cryptography, reliable flow, drivers and verified resources. [SAGE Lantern](https://github.com/l33tdawg/sage-lantern) owns the product code, device/profile policy, protected SAGE integration, provisioning, pilot control, deployment and STL/printing files. Its own public release is planned closer to December/January as the product announcement and Kickstarter preparation approach; readiness determines the date.

The dependency goes one way: **Lantern → Meshticulum**. Current source and distribution checks reject reverse imports and product implementations. The historical layout is provenance, not a supported installation path. Use the current branch and [SDK guide](SDK_GUIDE.md), rather than checking out an old Lantern-containing snapshot to run Meshticulum.

Before accepting this history for publication, we reviewed all retained refs and scanned the 38 development commits. The ten scanner findings were independently verified public deterministic cryptographic test vectors, a method-name literal and a fixed diagnostic canary. A bounded review of 23 versions across 18 removed native files found no credential-valued assignments, recognized credential formats or device IP literals. These are scoped checks, not proof that every possible secret is absent.

[`check_repository_boundary.py`](../tools/check_repository_boundary.py) checks the current reusable package and built artifacts. [`check_public_history.py`](../tools/check_public_history.py) enforces the current committed tree and reports historical product-file versions. Its `--strict-history` option rejects those retained versions when a downstream distribution requires a history with no Lantern code.

Preserving the history does not qualify older implementations for production or carry hardware evidence to a later build. [Performance](PERFORMANCE.md) and [release readiness](RELEASE_READINESS.md) retain the scope of each result and the remaining alpha requirements.
