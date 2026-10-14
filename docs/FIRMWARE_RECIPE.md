# Explicit raw-radio firmware recipe

This experimental recipe prepares and compiles Meshticulum's raw LoRa coprocessor from explicitly selected, checksum-pinned upstream inputs. It has no Lantern donor, home-directory toolchain default, SSH connection, upload or device command. Lantern owns fleet/deployment tools and native qualification; a build is not radio authority.

**Historical validated scope:** two independent fresh preparations/builds of the earlier two-file application on one macOS arm64 host, with Python 3.12.8 and PlatformIO Core 6.1.19, completed both ESP32-S3 targets. Per target, the application binary, ELF, bootloader and partitions matched byte-for-byte; maps differed because they contain workspace paths. The [validation record](../provenance/FIRMWARE_RECIPE_VALIDATION.json) binds that recipe/lock to runs 05/06 and preserves the first failed attempt. It does not validate the newer four-file LoRa/GFSK application or the updated preparation script. Those exact inputs require fresh build and artifact records. This is reproduction on the recorded host, not cross-host reproducibility, deployed-image equivalence, physical qualification or complete license closure. The current public source preview is `0.1.0`; no prebuilt alpha firmware release is implied. See [device setup](DEVICE_SETUP.md) for target-specific layout and deployment boundaries.

## Pinned inputs and targets

[`inputs.lock.json`](../firmware/meshticulum-bench/inputs.lock.json) records the URLs, archive lengths, SHA-256 values and extraction prefixes of **14 primary-source/registry archives**. They comprise MeshCore board support, RadioLib, the Arduino ESP32 framework, Espressif32 platform, compiler/tool packages and seven library inputs. The selected source identities include:

- MeshCore board support: `d92964352441e53b93e8667b802e04f6e072b39e`.
- RadioLib: `6d8934836678d8894e3d556550475b37dce3e2b6`.
- Arduino ESP32 framework package: `3.20017.241212+sha.dcc1105b`, with the framework patch separately bound to official Arduino ESP32 2.0.17 source.
- Espressif32 platform 6.11.0 and Xtensa ESP32-S3 compiler `8.4.0+2021r2-patch5` for macOS arm64.

| Recipe target | Upstream board environment | Application board ID |
| --- | --- | ---: |
| `mtbench_heltec_v4` | `Heltec_lora32_v4` | 1 |
| `mtbench_xiao_s3_wio` | `Xiao_S3_WIO` | 2 |

The recipe keeps upstream source trees and the notices present in those archives. It copies the current project-authored `main.cpp`, `protocol.hpp`, `gfsk_profile.hpp` and `gfsk250_profile.hpp` into the application, alongside explicit target configuration. It applies no Lantern/MeshCore donor patches. Board support and radio wrapper use do not make the application's raw protocol wire-compatible with MeshCore or Reticulum.

The [`requirements-firmware.txt`](../firmware/meshticulum-bench/requirements-firmware.txt) file pins the observed host CLI package versions. These are external build tools, not a statement that every package is firmware-linked or a hash-locked Python-wheel distribution. The builder independently checks PlatformIO Core's version.

## Fetch, prepare and build

Run from the Meshticulum checkout, on the supported host. Choose your own archive, isolated output and CLI environment locations. The example uses local `build/` paths; the recipe does not search another workspace or the user's home for missing inputs.

```sh
python3 -m venv build/firmware-cli
build/firmware-cli/bin/python -m pip install \
  -r firmware/meshticulum-bench/requirements-firmware.txt

python3 firmware/meshticulum-bench/prepare_firmware.py fetch \
  --archive-dir build/firmware-archives

python3 firmware/meshticulum-bench/prepare_firmware.py prepare \
  --archive-dir build/firmware-archives \
  --output-dir build/firmware-work

python3 firmware/meshticulum-bench/prepare_firmware.py build \
  --output-dir build/firmware-work \
  --pio-python build/firmware-cli/bin/python
```

[`prepare_firmware.py`](../firmware/meshticulum-bench/prepare_firmware.py) exposes three distinct actions:

- `fetch --archive-dir`: downloads the lock's explicit HTTPS archives and checks their lengths/hashes. Existing archives must match; an input mismatch is refused rather than overwritten or accepted as a newer version.
- `prepare --archive-dir --output-dir`: verifies all archives and the framework patch before creating a **new** output directory. It extracts the selected source/tool inputs, applies the hash-bound patch, writes local target configuration and records `SOURCE.json`. An existing output directory is refused.
- `build --output-dir --pio-python`: verifies the recipe/lock and prepared source tree, checks the supplied Python's PlatformIO version, and compiles both listed targets by default. Repeated `--target` selects one or both explicit names. No upload, erase or device action is invoked.

Prepared inputs may not drift between preparation and build. Keep failed downloads, preparation errors and build logs as evidence; do not replace them with a successful status from a different input set. Use a fresh output when deliberately revising inputs. Build logs are create-only, so repeat attempts also need independently identified output/evidence.

The recipe maps its isolated output path to `/meshticulum-build` with `-ffile-prefix-map` and sets `SOURCE_DATE_EPOCH=1786709959` from the locked upstream commit date, `2026-08-14T12:19:19Z`. These reduce path/time variation. The recorded independent pair matched the four artifact types listed below; retain the different maps and do not generalize that result to another host, toolchain or input set.

## The framework patch

[`patches/hwcdc-2.0.17.patch`](../firmware/meshticulum-bench/patches/hwcdc-2.0.17.patch) modifies `cores/esp32/HWCDC.cpp` for bounded USB Serial/JTAG transmit behavior. The lock separately identifies the exact archive file, patch and resulting-file hashes, plus the official Espressif source reference at `2ecad233178bfbba9c584ffd9f67e941d84adbb7`. Preparation checks the archive base and result and applies with no patch fuzz. The package file and upstream Git reference are separate recorded inputs; no byte-identical equivalence between them is implied.

The retained framework comparison found that file to be changed. That is a scoped source comparison, not a claim that an old copied SDK/toolchain was fully pristine or that the patch is physically qualified on either target. The new archive/patch lock supplies a separate provenance path for this recipe.

The exact archive's `HWCDC.cpp` header grants **Apache-2.0** terms and credits Espressif Systems (Shanghai) PTE LTD for 2015–2024. The patch retains that per-file attribution and license; the framework package's overall LGPL label must not be applied to this file. Conversely, this exception does not relicense the whole framework as Apache-2.0.

The [modification and attribution note](../firmware/meshticulum-bench/patches/README.md) records the selected upstream reference and per-file terms. The project [Apache-2.0 text](../LICENSE) supplies that license; retain the Espressif header and modification record with the patch.

The framework's supplementary overall LGPL-2.1-or-later text is [`patches/LICENSE.arduino-esp32`](../firmware/meshticulum-bench/patches/LICENSE.arduino-esp32). Preparation checks its locked hash and copies it to the prepared framework's `LICENSE.md`. Keep package-level text and per-file grants distinct. Review linked SDK/runtime content, notices, corresponding source and any required relinking materials against the actual distribution; full closure remains open. See [third-party provenance](THIRD_PARTY.md).

## What the outputs prove

`SOURCE.json` binds the recipe, input lock, application source, selected archives and prepared tree. `evidence/BUILD.json` records target return codes, log hashes, host identity and hashes/sizes for `firmware.bin`, `firmware.elf`, `firmware.map`, `bootloader.bin` and `partitions.bin` when compilation succeeds. Retain the ELF and map for linked-content review, not only the application binary.

The [validation record](../provenance/FIRMWARE_RECIPE_VALIDATION.json), schema `meshticulum.raw-firmware-validation.v1`, binds the final recipe/lock and the independently prepared runs 05/06. Both targets exited successfully in both runs. Their `firmware.bin`, `firmware.elf`, `bootloader.bin` and `partitions.bin` hashes matched; `firmware.map` hashes did not. The application binary identities are:

| Target | `firmware.bin` bytes | SHA-256 |
| --- | ---: | --- |
| `mtbench_heltec_v4` | 388,368 | `9e3a43f0d5945e13974849008c8c252edf6fd89ba3a541865866266f18e861ef` |
| `mtbench_xiao_s3_wio` | 382,048 | `3c3e78bf99220e45eed5fb9da717422941b7a6c5065ba4fb386c51b66b63e938` |

The first primary-input attempt failed on PlatformIO registration of the pinned RISC-V tool; both target failures and log hashes remain in the record. Registration was corrected in a fresh preparation. Older controls using copied dependencies, and the unnormalized primary iteration, produced different binaries. They are separate evidence rather than equivalent deployed images.

The historical application source tag **`mtb1-9816f92488`** identifies that raw-radio application's two source files; it is not the identity of the full BSP, framework, compiler or binary. The current four-file application produces a different source manifest and derived tag. Use each build's input/source manifests and output hashes for those identities. Compilation alone proves no reproduction; the observed historical pair establishes only its stated scope. It proves no RF behavior, peer/application delivery, sustained goodput or complete upstream notices. Physical/notice qualifications remain false until separately proved.

Fresh native operation belongs to [Lantern](https://github.com/l33tdawg/sage-lantern), under current finite ownership and bounded cleanup. Historical [performance results](PERFORMANCE.md) stay bound to their earlier runtime; they cannot be transferred to these outputs. See [hardware ownership](HARDWARE.md), [release readiness](RELEASE_READINESS.md) and [the roadmap](ROADMAP.md).
