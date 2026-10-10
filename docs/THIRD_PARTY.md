# Third-party components and build provenance

This is a source-readiness audit for a **future 1.0.0-alpha**, reviewed on 2026-10-10. It identifies current dependency boundaries and remaining firmware provenance work. It is not a final release inventory or authorization to publish a tag, binary or bundle.

Meshticulum's extracted source retains the parent project's [Apache-2.0 license](../LICENSE), [NOTICE](../NOTICE) and [file-by-file extraction record](../provenance/UPSTREAM_SOURCE.json). Third-party components keep their own terms. In the new recipe, HWCDC.cpp and its patch retain that file's Apache-2.0 grant, while the framework's overall LGPL-2.1-or-later text remains separate. Neither label establishes every SDK/runtime component's terms. The [source audit](../provenance/THIRD_PARTY_SOURCE_AUDIT.json) records observed versions and hashes of the license and metadata files used below; it intentionally distinguishes a package's declared license from reviewed license text.

## What the artifacts contain

| Artifact | Included code | Dependency boundary |
| --- | --- | --- |
| Source repository and Python package | Reusable Meshticulum transport, crypto, resources and first-party tests/tooling; the source distribution also carries the explicit firmware recipe and its separately licensed framework patch. No Lantern application adapters or fleet tools. | `pyproject.toml` declares external crypto dependencies. Lantern owns its separate SDK installation extra and application dependency pin. Declaring a dependency does not put its wheel or native libraries inside Meshticulum's package. Inspect the actual wheel/source contents separately. |
| Lantern public host runtime archive (separate repository) | Pinned Meshticulum modules plus Lantern native launcher, pilot modules and source manifest. | The builder does not copy cryptography, PyNaCl, the SAGE SDK, radio firmware, board support or compiler packages into the archive. Crypto dependencies must be supplied separately. The archive must also retain Meshticulum's own LICENSE and NOTICE; inspect the built archive to verify that packaging requirement. |
| Historical Lantern firmware build snapshot | Meshticulum raw-radio application plus a retained, locally patched MeshCore-derived board support tree and copied build libraries. | This material is outside a fresh standalone clone. Its third-party source and licenses are not converted to Apache-2.0 by linking or packaging it. |
| Explicit primary-input firmware preparation | The new recipe fetches/expands 14 locked upstream archives, adds the raw application/configuration and applies one locked framework patch. | Inputs are selected independently of Lantern's donor. Host tool packages, extracted sources and actual linked firmware are different inventories; full distribution closure remains open. |
| Firmware binary or combined device image | Determined by the exact build and linker result. | A source-directory list is insufficient: linked framework, SDK, runtime and library contents need an artifact-specific inventory. No complete future-alpha binary closure has been established by this audit. |

The firmware sources in this repository describe an experimental raw LoRa coprocessor. MeshCore-derived board support is a build input; it does not mean that the Meshticulum application uses MeshCore routing. Reticulum implementation code is not part of this extraction. Lantern fleet configuration, provisioning and application implementations live in [sage-lantern](https://github.com/l33tdawg/sage-lantern); the [migration record](../provenance/REPOSITORY_BOUNDARY.json) preserves their history without distributing their source here.

## Python dependencies

The versions in the last column are observations from the parent development environment, not the future release's resolved dependencies. The project currently permits version ranges for its core dependencies. A combined distribution that supplies wheels must inventory the exact supplied wheels and their native/transitive contents; this table does not review every version allowed by those ranges.

| Component | Use and verified evidence | Observed version |
| --- | --- | --- |
| cryptography | Core dependency. Its local `LICENSE` gives a choice of Apache-2.0 or BSD-3-Clause; both full license files are present and hashed. [Versioned upstream license](https://github.com/pyca/cryptography/blob/50.0.2/LICENSE). | 50.0.2 |
| PyNaCl | Core dependency. Its local package includes Apache-2.0 text and a separate libsodium notice. [Versioned upstream license](https://github.com/pyca/pynacl/blob/1.6.2/LICENSE). | 1.6.2 |
| libsodium notice supplied by PyNaCl | The installed PyNaCl artifact contains `licenses/LICENSE.libsodium.txt`, identifying ISC terms and Frank Denis attribution. That notice is recorded separately from PyNaCl's license. It does not establish the complete contents of a future wheel. | No independent libsodium version verified |
| cffi | Transitive dependency. The observed artifact declares `MIT-0`, and its license text identifies MIT No Attribution with possible per-file exceptions. This finding is specific to the observed artifact. | 2.1.1 |
| pycparser | Transitive dependency. BSD-3-Clause metadata and corresponding local license text are recorded. | 3.0 |
| SAGE agent SDK | Lantern-only application dependency, recorded here as historical audit evidence; not a Meshticulum dependency. Its metadata declares Apache-2.0, but the observed installed artifact supplies no license file. It is not bundled into the public runtime archive; any redistribution of that SDK requires its exact artifact and notices to be reviewed. | 11.19.10+lantern.20260906 |
| httpx | Lantern-only SAGE integration dependency; not a Meshticulum dependency. The observed artifact has BSD-3-Clause metadata and license text. Its transitive closure is outside the core/public bundle audited here. | 0.28.1 |

The crypto libraries use native components. The observations above do not replace the supplied wheel's own notices or establish an OpenSSL, libsodium or platform-runtime version. Source installation and wheel installation can have different native dependency boundaries.

## Retained firmware inputs

Lantern's historical [`prepare_build.py`](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/firmware/meshticulum-bench/prepare_build.py) copies a reconstructed donor from the historical SAGE lab tree, then adds Meshticulum's bench application and target configuration. It explicitly disclaims pristine upstream equivalence. Its `run_build.py` expects that snapshot, an external compiler/framework and the host's PlatformIO executable. Neither helper currently reconstructs all inputs from a fresh standalone checkout. See the [hardware guide](HARDWARE.md).

The following retained-environment observations are historical audit evidence. They are not the input inventory of the new [primary recipe](FIRMWARE_RECIPE.md), even where a version string matches.

| Component | Observed identity and license evidence | Outstanding boundary |
| --- | --- | --- |
| MeshCore-derived donor/BSP | The helper records a basis of [d92964352441e53b93e8667b802e04f6e072b39e](https://github.com/meshcore-dev/MeshCore/commit/d92964352441e53b93e8667b802e04f6e072b39e), dated 2026-08-14. The donor's MIT `license.txt`, copyright 2025 Scott Powell / rippleradios.com, matches [that upstream license](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/license.txt) byte-for-byte. | License equality does not prove source equality. The reconstructed source and local patches still need a complete reproducible provenance record. |
| Donor's nested ed25519 source | `lib/ed25519/license.txt` has a zlib-style three-condition notice, copyright 2015 Orson Peters. | Preserve this distinct notice if that source is supplied. Whether it is linked into a selected bench binary is unverified here. |
| RadioLib | Donor configuration names [6d8934836678d8894e3d556550475b37dce3e2b6](https://github.com/jgromes/RadioLib/tree/6d8934836678d8894e3d556550475b37dce3e2b6). Local metadata reports 7.7.1; the MIT `license.txt`, copyright 2018 Jan Gromeš, matches [the configured upstream license](https://github.com/jgromes/RadioLib/blob/6d8934836678d8894e3d556550475b37dce3e2b6/license.txt) byte-for-byte. | Pin and verify the actual source archive and patches used by a new build, rather than relying on a version string. |
| Arduino ESP32 framework | Local package metadata identifies `3.20017.241212+sha.dcc1105b` and LGPL-2.1-or-later. The local `cores/esp32/Arduino.h` grants those terms; [upstream 2.0.17 license text](https://github.com/espressif/arduino-esp32/blob/2.0.17/LICENSE.md) was checked. | The local package lacks its root license text. Its precompiled SDK components and modifications need separate version, notice and linked-content review. |
| PlatformIO Espressif32 platform | Local package identity is 6.11.0; `platform.json` declares Apache-2.0 and the full local LICENSE is present. | The future recipe must bind the exact platform and its selected package artifacts. |
| Xtensa ESP32-S3 compiler | Local package identifies `8.4.0+2021r2-patch5` and declares GPL-2.0-or-later. Its `share/licenses` tree contains multiple component licenses and GCC runtime-exception text. | The package-level label is not a complete toolchain or firmware-output license inventory. Component/runtime selection and applicable exceptions remain to be resolved for the supplied artifacts. |
| RISC-V compiler package | The helper also copies `8.4.0+2021r2-patch5`, with its own component license tree. | Copying it into a tool snapshot does not establish its use by the two ESP32-S3 bench targets. Avoid treating all copied tools as linked firmware. |
| esptool package | Local package identifies `1.40501.0`, declares GPL-2.0-or-later and includes GPLv2 license text. | Preserve its source and license boundary if supplying the tool. Running an external flashing tool does not make it part of Meshticulum's host archive. |
| SCons package | The helper copies the host's `tool-scons` directory without an immutable recipe pin. Observed package metadata says `4.41101.0` and MIT. | No license file was found in that local package. Metadata alone does not close notice preservation or reproducibility for a redistributed tool snapshot. |
| PlatformIO Core | The retained build environment's installed metadata reports 6.1.19 and Apache terms, with a local LICENSE. | The helper calls a host-home `pio` executable; this audit did not establish that executable's binding to the observed environment. |

The copied library directory contains **29 components**. Their identities and individual license-file hashes are in the [source audit](../provenance/THIRD_PARTY_SOURCE_AUDIT.json). This is a build snapshot, not a statement that all 29 are used by either bench target.

Important distinctions in that snapshot include:

- Adafruit NeoPixel has an LGPL-3.0-or-later grant in its source and LGPLv3 text. Arduino_LPS22HB has an LGPL-2.1-or-later source grant. MicroNMEA supplies LGPLv2.1 text; this audit did not verify an additional “or later” grant.
- Adafruit GFX's local BSD notice has two conditions. Other retained BSD notices must be preserved individually rather than all being labeled with the same SPDX identifier.
- The Crypto library has a reviewed MIT grant in a source header but no top-level license file. Several Adafruit sensor packages also carry license statements in source rather than a root license file.
- Adafruit_VL53L0X's README says MIT, its Adafruit wrapper says BSD, and its ST source carries separate conditions. A single package-level label would hide those differences.
- ArduinoJson, RadioLib, RTC and sensor/display dependencies remain separate works. Sensor-disable flags and copied-directory membership do not prove what survives linking.

## Explicit primary-input recipe

The new [`prepare_firmware.py`](../firmware/meshticulum-bench/prepare_firmware.py) replaces implicit donor/home inputs with an explicit macOS-arm64 recipe. Its [`inputs.lock.json`](../firmware/meshticulum-bench/inputs.lock.json) contains 14 primary archive URLs, lengths, hashes and extraction prefixes. The host CLI versions are pinned separately in [`requirements-firmware.txt`](../firmware/meshticulum-bench/requirements-firmware.txt). An archive pin identifies fetched input bytes; it does not identify every linked component or establish complete distribution notices.

| Input boundary | Selected identity | What remains to review |
| --- | --- | --- |
| Board support | Pristine MeshCore archive at `d92964352441e53b93e8667b802e04f6e072b39e`; this project's raw application/configuration is added separately | Preserve MeshCore's own/nested notices and identify the board/helper objects actually linked. It is not the Lantern donor or a MeshCore routing implementation. |
| Radio and library sources | RadioLib commit `6d8934836678d8894e3d556550475b37dce3e2b6`, plus locked Crypto, RTClib, Melopero RV3028, Adafruit BusIO, CayenneLPP and ArduinoJson archives | Inspect those selected source archives' notices and the target ELF/map. The old 29-directory donor inventory cannot stand in for this seven-library input set. |
| Framework | Arduino ESP32 package `3.20017.241212+sha.dcc1105b` and the explicit HWCDC modification | HWCDC.cpp has an Apache-2.0 per-file grant; the package carries overall LGPL-2.1-or-later text. Per-file exceptions, precompiled SDK libraries and other linked components need separate review. Complete SDK/runtime notice and relinking closure is still open. |
| Platform and tools | Espressif32 6.11.0; explicit Xtensa, RISC-V, SCons and esptool archives; PlatformIO Core 6.1.19 | Host tools are not all firmware-linked. Version pins and package labels do not resolve runtime exceptions or the supplied artifact's complete license inventory. |

The [HWCDC patch](../firmware/meshticulum-bench/patches/hwcdc-2.0.17.patch) modifies `cores/esp32/HWCDC.cpp`. The exact primary archive file's header grants **Apache-2.0** terms, copyright 2015–2024 Espressif Systems (Shanghai) PTE LTD. Retain that attribution and the per-file grant in the modification record. The lock records the archive base/result and patch hashes separately from the official source reference `2ecad233178bfbba9c584ffd9f67e941d84adbb7`; those are not a claim of byte-identical package/Git files.

The [patch attribution note](../firmware/meshticulum-bench/patches/README.md) preserves the per-file exception and modification scope. Earlier metadata inferred patch licensing from the package-level LGPL label; exact source-header review corrected it. The producer now records `framework_patch.license` as Apache-2.0 and keeps the overall text under a separate `framework_license` record.

The framework's [overall LGPL-2.1-or-later text](../firmware/meshticulum-bench/patches/LICENSE.arduino-esp32) is independently hash-checked and copied into the prepared framework as `LICENSE.md`. It must not be substituted for HWCDC.cpp's per-file license, nor does that file's Apache grant relicense the package. A comparison finding one changed retained framework file proves only that scoped comparison, not pristine equivalence of an entire historical build or physical acceptance of the patch.

Preparation records the original archives and prepared source tree; build evidence records logs, ELF/map and output hashes. The first fully fresh attempt failed on pinned RISC-V package registration, and its failures remain retained. The [validation record](../provenance/FIRMWARE_RECIPE_VALIDATION.json) binds the final corrected producer/lock to fresh runs 05/06 for both targets on one pinned macOS-arm64 host. Per target, binary/ELF/bootloader/partitions matched; maps differed. Earlier pristine-board-support controls with copied dependencies produced different binaries and remain separate evidence. Bounded reproduction does not establish cross-host reproducibility, deployed-image equivalence, hardware acceptance or complete firmware licensing. The application's `mtb1-9816f92488` source tag is not a full-build identity. See [the recipe guide](FIRMWARE_RECIPE.md).

## Work needed before distributing firmware or a combined bundle

1. Define the actual artifacts and generate a linked-content inventory tied to their output hashes. The primary recipe's archive/patch pins advance input provenance; the historical source audit must not be relabeled as the final inventory.
2. Validate the standalone primary-input recipe and retain its preparation/build evidence, failures and upstream notices. The older pristine-board-support control used copied dependencies and is a separate result. Document every modification rather than importing a retained Lantern tree or host-home toolchain.
3. Resolve missing license texts and mixed per-file notices for all material actually supplied. Review the selected framework/SDK and toolchain runtime components against the resulting linker map.
4. Provide the corresponding source, modification record and any relinking materials required by the selected licenses for the actual distribution. A top-level Apache-2.0 notice cannot replace upstream terms.
5. Inspect each built source package, host archive and firmware distribution for LICENSE, NOTICE and applicable third-party inventory/notices. For a bundle supplying Python wheels, include those exact wheels' transitive/native notice inventory as well.

The core source-package boundary, a separately owned Lantern public host archive with separately supplied dependencies, and a firmware/toolchain bundle have different inventories. The release review must name which one it has checked. Physical qualification, private backend qualification and publication authorization remain separate gates in the [roadmap](ROADMAP.md).
