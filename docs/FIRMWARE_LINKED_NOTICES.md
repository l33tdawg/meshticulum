# Firmware linked components and notices

The [artifact audit](../provenance/FIRMWARE_LINKED_NOTICE_AUDIT.json), schema `meshticulum.linked-firmware-components.v1`, inventories **49 contributing archives per target** in the final build-05 ELF/map pair. It records archive hashes and retained member names, identifies primary notice texts and preserves unresolved source/license questions. It is an incomplete artifact-specific audit: `full_notice_closure`, `cross_host` and `rf_qualified` remain false.

This advances the [firmware recipe's](FIRMWARE_RECIPE.md) input/build evidence into linked-content review. It does not establish exact source correspondence for every object, a distribution-wide license result or native qualification. This isolated package candidate is `1.0.0a1`; no alpha publication or device action follows from this record.

## What the inventory counts

| Target | Contributing archives | Selected allocated object rows |
| --- | ---: | ---: |
| `mtbench_heltec_v4` | 49 | 4,381 |
| `mtbench_xiao_s3_wio` | 49 | 4,266 |

The filter accepts nonzero map rows for `.o` and `.obj` objects only when their entire range lies within an **allocated ELF section**. Discarded and debug-only rows are omitted. The audit binds each target's exact ELF/map hashes, selected archive paths/hashes and member sets. An archive count is not a count of licenses, complete source mappings or active services.

`retained_nobits_bytes` records allocated memory without corresponding stored image bytes, such as zero-initialized storage. Keep it distinct from image content. Map contributions also do not account for every form of linker padding, metadata or non-archive object in a finished image, so their counts must not become a flash-size or runtime-use claim.

The selected archives include framework/HAL/RTOS, radio/library, cryptographic, C/C++ runtime and SDK contributions. WiFi and Bluetooth objects appear in the retained inventory; their presence does not demonstrate that a WiFi or Bluetooth service is active. Execution and native service state require separate evidence.

## SDK identity is still incomplete

The supplied SDK headers report **4.4.7**, while the actual binary strings include `esp-idf: v4.4.7 38eeba213a` and `v4.4.7-dirty`. These observations do not establish the full precompiled SDK's exact source, configuration or modifications. A release-tag license and matching version macro cannot resolve that dirty-build boundary.

Two specific precompiled archives have direct upstream blob matches:

| Packaged archive | Recorded upstream repository and submodule revision |
| --- | --- |
| `libphy.a` | `espressif/esp-phy-lib` at `dcfdccf6cc2fc02d0886624b7998c890d1a19b28` |
| `libcoexist.a` | `espressif/esp32-wifi-lib` at `f2aae4d44ec7908013066e69d29b9948846c335c` |

For each, the audit records the matching Git-blob SHA-1 plus an independently computed local SHA-256. These direct component-repository matches retain their original scope; the complete fingerprints remain in the [audit JSON](../provenance/FIRMWARE_LINKED_NOTICE_AUDIT.json).

A later [packaged SDK blob review](../provenance/PRIMARY_PACKAGED_SDK_BLOB_REVIEW.json) independently checked every contributing SDK archive against the official [Arduino ESP32 2.0.17 tree](https://github.com/espressif/arduino-esp32/tree/5e19e086c43d0fa5e5a596497ff8f11a0a43f6c2). All **39 distinct archives across the two targets** matched their upstream Git blobs, with local SHA-256 fingerprints also retained. Each target contributes 34 SDK archives. The packaged `tools/sdk/versions.txt` and ESP32-S3 `sdkconfig` also match official upstream blobs.

The SDK version record names ESP-IDF `38eeba213aa695aabfd6d89aa9f5078dbe5a94c3` and Arduino component `d75795f5e27eac7afea33b27a22d826cb4556c99`, alongside its other component revisions. Resolving those labels and matching released archives improves input provenance. It does **not** reconstruct the dirty SDK build, prove every linked member's exact source or licensing terms, inspect every unlinked SDK archive, or establish the native deployed image. Source/build modifications, per-file notices and corresponding-source/relinking closure remain open.

## Retained notice texts

The [`notices/`](../firmware/meshticulum-bench/notices) directory preserves the selected primary and exact-compiler texts. The audit records upstream repository/revision and notice hashes where available. Notice collection and notice applicability remain different review steps.

| Notice evidence | Scope and remaining check |
| --- | --- |
| [ESP-IDF v4.4.7 LICENSE](../firmware/meshticulum-bench/notices/esp-idf-v4.4.7-LICENSE) | Primary release notice; reconcile the actual dirty precompiled SDK and per-file exceptions. |
| [PHY](../firmware/meshticulum-bench/notices/esp-phy-lib-LICENSE), [WiFi](../firmware/meshticulum-bench/notices/esp32-wifi-lib-LICENSE), [Bluetooth](../firmware/meshticulum-bench/notices/esp32c3-bt-lib-LICENSE) | Primary texts from the recorded component revisions. Only PHY/coexist have the direct blob matches above; retain separate member/notice review for the other contributions. |
| [Mbed TLS LICENSE](../firmware/meshticulum-bench/notices/mbedtls-LICENSE) | Text from recorded submodule `2b8e772fc1cb0732cda3bae7d1e9d6f4cfaf63d9`, offering Apache-2.0 or GPL-2.0-or-later. Reconcile the actual linked members, per-file terms and selected licensing basis. |
| [GCC runtime exception](../firmware/meshticulum-bench/notices/toolchain-gcc-COPYING.RUNTIME), [GPLv3](../firmware/meshticulum-bench/notices/toolchain-gcc-COPYING3), [LGPLv3](../firmware/meshticulum-bench/notices/toolchain-gcc-COPYING3.LIB) | Exact pinned compiler-archive texts. Determine their applicability and any exception conditions for each linked runtime member. |
| [Newlib notice collection](../firmware/meshticulum-bench/notices/toolchain-newlib-COPYING.NEWLIB) | Mixed per-file/source notices from the exact compiler archive; no single package label substitutes for member review. |

The framework's overall LGPL text remains separate from HWCDC.cpp's Apache-2.0 per-file grant. Preserve that distinction and the modification record described in [third-party provenance](THIRD_PARTY.md).

## Required before closing the artifact review

- Reconcile all selected archives/members and direct objects with exact source, copyright, per-file notices and any modifications.
- Establish the precompiled SDK's actual build source/configuration, including the observed dirty state; retain unresolved or unavailable mappings.
- Review GNU runtime-exception applicability and newlib's mixed notices against the actual retained members.
- Supply and review corresponding source, modification/build records and any required relinking materials for the intended distribution.
- Inspect the actual source/binary/bundle contents for applicable texts and inventory, then complete the artifact-specific release review.

The [release checklist](RELEASE_READINESS.md) retains these open gates alongside Lantern's native public pilot and private-backend qualification. Same-host build reproduction, archive identity, collected license text and physical behavior each require evidence matching their own scope.
