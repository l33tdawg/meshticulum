# Transport source update

This update integrates reusable transport work from source commit [`3bffa64`](https://github.com/l33tdawg/meshticulum/commit/3bffa64beaee1718877f93443b7737db4a11b54b) into the project preview. The package remains **0.1.0**. It does not publish `1.0.0-alpha` or carry forward hardware qualification from another source/runtime pair.

| Component | Source now available | Boundary |
| --- | --- | --- |
| Radio | Explicit GFSK125/GFSK250 profiles, matching serial/SPI workers and raw-radio firmware support; bounded ARM deadlines and earlier return to receive after transmit. | Profiles grant no RF authority. Exact firmware, board, native admission and deployment evidence remain application responsibilities. |
| Resources | MB1 byte-bundle encoding, manifest/hash verification, bounded file finalization and transport descriptors; explicitly selected 32 KiB public-response capability. | Source and file tests do not establish native bundle throughput. Existing default MR1 limits remain unchanged. |
| Private protocol | Opaque forwarding of MPF2 records under held carrier authority, with bounded lifetime and one-use idle cleanup delegation. | The relay supplies no endpoint private keys, SAGE policy, device provisioning or production dispatch. |
| Reliable flow | Existing MT05 flow and flow-port source. | Their accepted byte pins remain unchanged. |

The [SDK guide](SDK_GUIDE.md) retains its offline example. [Device setup](DEVICE_SETUP.md) and the [firmware recipe](FIRMWARE_RECIPE.md) distinguish the historical build records from the current application sources. The [post-quantum prototype](POST_QUANTUM.md) remains a separate experiment; this update does not make post-quantum cryptography the default.

Existing [performance records](PERFORMANCE.md) remain bound to their original samples and timing definitions. This source update establishes no new speed comparison, sustained throughput, private native qualification or firmware-release result. The [alpha gates](RELEASE_READINESS.md) remain open.

Meshticulum remains independent of Lantern and the SAGE SDK. [SAGE Lantern](https://github.com/l33tdawg/sage-lantern) owns product policy, private adapters, native jobs, provisioning and deployment. A consuming application must pin the exact integrated commit and verify its required source hashes before repeating its own integration and native checks.
