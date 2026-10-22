# Hardware library and deployment boundary

Meshticulum owns reusable radio protocol and driver code. [Lantern](https://github.com/l33tdawg/sage-lantern) owns device integration: Linux jobs, native provisioning, fleet configuration, runtime packaging, installation, firmware flashing, coordinated pilots and rollback. The dependency is **Lantern → Meshticulum**. Importing a Meshticulum driver does not start those application services or acquire their authority.

## What remains in Meshticulum

[`main.cpp`](../firmware/meshticulum-bench/main.cpp) and [`protocol.hpp`](../firmware/meshticulum-bench/protocol.hpp) implement the project-authored raw-radio test coprocessor and its bounded serial protocol. The library provides the [serial worker](../src/meshticulum/bench/serial_worker.py), [SX1262 driver](../src/meshticulum/bench/sx1262.py), [Linux HAT adapter](../src/meshticulum/bench/linux_hat.py) and [HAT worker](../src/meshticulum/bench/hat_worker.py). These consume caller-owned ports, journals, clocks and current authority. The host application selects its device and owns setup, finite leases, containment and cleanup.

Installation, admission, local RF completion, authenticated peer receipt, durable bytes and application acceptance remain separate events. `RF_DONE` requires matching driver evidence for the original boot, request and packet; it does not establish remote storage or publisher verification. Unknown attempts retain their reservations and fence further dispatch. New grants and process cleanup cannot refund old debt.

The [host-native protocol harness](../tests/meshticulum_bench_native) compiles against synthetic Arduino/ESP32 headers and exercises bounded serial input. [`check_linux_abi.c`](../firmware/meshticulum-bench/check_linux_abi.c) supports checking the Linux device ABI. These controls do not qualify a real board's timing, antenna, regional configuration or RF output.

## Board support and a portable build

The [explicit firmware recipe](FIRMWARE_RECIPE.md) uses 14 checksum-pinned primary archives for board support, Arduino/ESP32 framework, RadioLib and selected tools/libraries. It exposes separate fetch, prepare and build actions, with caller-supplied archive/output/CLI paths. Its current build host is macOS arm64; the two targets are Heltec V4 and Xiao S3 WIO. It uses no Lantern donor or home-directory toolchain default and invokes no device action.

Preparation retains source and records the input lock, application files and isolated tree. A build checks those identities again and records logs plus binary/ELF/map hashes. The explicit HWCDC patch retains that file's Apache-2.0 grant and Espressif attribution; the framework package's overall LGPL-2.1-or-later text is retained separately. Neither label describes every linked SDK component. Read the current application tag from your output's `SOURCE.json.identity`; it hashes four application files and is not a whole-binary or BSP identity. The older tag in runs 05/06 remains historical evidence. Compilation, reproducible source selection, bit-identical rebuilds, license closure and physical qualification are separate claims.

The [recorded independent pair](../provenance/FIRMWARE_RECIPE_VALIDATION.json), runs 05/06, compiled the corrected producer's two targets in fresh workspaces on one pinned macOS-arm64 host. Binary, ELF, bootloader and partitions matched per target; maps remained path-dependent. Earlier copied-dependency controls produced different binaries. No output was flashed or RF-qualified as part of those historical validation runs; that record establishes the stated host/input reproduction.

The earlier lab firmware build used a locally patched MeshCore-derived board-support tree. Its [historical build preparation](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/firmware/meshticulum-bench/prepare_build.py) belongs to Lantern. The new recipe starts from a pinned pristine MeshCore archive and adds this project's raw-radio application/configuration; neither use makes this protocol wire-compatible with MeshCore. Earlier controls with copied dependencies and historical firmware hashes must not be relabeled as a fresh primary-only build.

The later [MCU020 proof](../provenance/PUBLIC_MCU020_PROOF.json), bound to candidate `673eb60`, matches the current four application files, recipe and lock to independently prepared application BIN/ELF outputs for both named targets on the recorded macOS-arm64 host. The retained Heltec installation record binds the selected application image to a ROM-MD5 check of its 393,216-byte written span. Later runs retain runtime-tag continuity after a boot change; there is no fresh full-device SHA-256 readback or whole-framework attestation. Matching Xiao build outputs do not establish physical acceptance on that board.

RadioLib, MeshCore-derived board support and platform components keep their upstream licenses. The Meshticulum source license does not relicense those inputs. The published SDK/source-build alpha passed its selected source/license/notice coverage review. Optional prebuilt firmware remains a separate distribution decision, with source/object/relink/notice companions and unresolved vendor-source identities retained for review. Full vendor SDK/compiler source-to-build correspondence and complete notice closure are not asserted. See the [published alpha scope](RELEASE_READINESS.md#published-distribution-sdk-and-firmware-source-build); a host-native stub test cannot close physical or distribution requirements.

## Installed SDK on the measured radio path

The [installed-SDK check](../provenance/PUBLIC_INSTALLED_SDK_PROOF.json) used normal Linux aarch64 virtual environments on the Orange Pi 5 Plus HAT sender and Heltec V4 host receiver, running Python 3.14.4 and 3.11.2 respectively. Both imported the same published wheel in isolated mode, without a source checkout or system site packages; all 67 runtime files matched. A fresh signed 100 kB file completed after a five-second pause between verified parts, at **1.74 kB/s (13.92 kbit/s) including sender startup and the pause**. The original 240-second window and attempt debt were preserved, and all ten children closed successfully.

This is a direct GFSK250 check at about one metre using the retained MCU020 Heltec installation. It does not transfer physical acceptance to Xiao, LoRa modulation, other Linux HAT wiring or a private Lantern integration. The [measurement report](RADIO_SDK_MEASUREMENTS.md#clean-installation-and-verified-piece-continuation) distinguishes the successful run from the earlier incomplete installed trial and states the timing boundaries.

A later A/B check used two ROCK 5B+ hosts with USB-attached Heltec V4 radios
approximately 20 metres apart in separate rooms. Unit B was updated app-only
from older firmware `mtb1-9816f92488` to the current `mtb1-020e287255` image
reproduced from source `c8957e56b017ecd42486acc2c8a46c24cb238d2d`
after pinning the exact prior application range for rollback. The fresh 100,000-byte
signed file completed at **1.776 kB/s including sender startup**, with five
authenticated receipts, exact whole-file verification, **-57.5 to -56.5 dBm**
receiver DATA RSSI and zero MCU receive-drop deltas. The distance was
operator-reported, and this one cross-room sample does not establish field
range, sustained capacity or acceptance of another board/antenna build.
[The A/B record](RADIO_SDK_MEASUREMENTS.md#ab-cross-room-transfer-22-october-2026)
retains the evidence and claim boundaries.

## Where to run Lantern jobs

For an application on your own hardware, the [public file example](../examples/README.md) composes the existing finite workers, crypto, journals and verified chunks without Lantern or SAGE imports. Its [app-only flashing guide](FLASHING.md) uses the pinned esptool interface and a separately checked current rollback. These examples do not provision a fleet or supply native product authority.

The actual finite [radio node and service entry](https://github.com/l33tdawg/sage-lantern/tree/codex/meshticulum-boundary/software/meshticulum-integration/src/lantern_meshticulum/bench), [TCP entry](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/software/meshticulum-integration/src/lantern_meshticulum/ip/node.py) and pilot controller live in Lantern's `software/meshticulum-integration`. Its [firmware/runtime tooling](https://github.com/l33tdawg/sage-lantern/tree/codex/meshticulum-boundary/firmware/meshticulum-bench) owns product bundle builds, provisioning, staging, flashing and rollback. Meshticulum's explicit-input compile recipe does not duplicate those deployment scripts.

Lantern supplies current host/boot/service facts, independent adjacent-link keys, original resource deadlines and its native owner before dispatch. A finite radio job needs an exclusive worker, a separately provisioned grant and bounded disarm. A finite TCP job must authorize socket setup and independently bind the actual endpoints. Building or installing their runtime bundle grants neither radio nor connection authority.

Lantern recorded finite public C→A LoRa→B IP delivery and a separate original-deadline loss/LoRa-resume/measured-IP-return campaign using Meshticulum primitives. [The native checkpoints](IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return) retain their exact source/runtime pins, verification and terminal evidence. Further device execution requires a fresh coordinated window, current process/driver evidence and bounded cleanup. Preserve production services, identities, protected stores and raw failures. The earlier physical [radio benchmark](PERFORMANCE.md) remains bound to its own runtime and settings; it does not qualify a newly built bundle or the full mixed path.
