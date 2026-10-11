# Device setup and experimental firmware

The Python [SDK](SDK_GUIDE.md) can be explored offline first. A device deployment additionally needs a matching board/image, an exclusive host connection and application-owned radio admission. Meshticulum supplies reusable drivers and a build recipe; [Lantern](https://github.com/l33tdawg/sage-lantern) owns its native device jobs, provisioning, fleet installation and rollback.

There is **no prebuilt alpha firmware release** in this project preview. The recorded primary-input builds reproduce on one macOS arm64 host; they have not established that these fresh outputs match the deployed lab images or are physically qualified. [Firmware validation](../provenance/FIRMWARE_RECIPE_VALIDATION.json) and [release readiness](RELEASE_READINESS.md) record that scope.

## Choose the correct hardware path

| Hardware | Current source path | What must run on the host |
| --- | --- | --- |
| Heltec LoRa 32 V4, ESP32-S3 | `mtbench_heltec_v4` raw coprocessor target | An application with the matching serial protocol, peer keys, finite grant, journals and exclusive worker. |
| Xiao ESP32-S3 with WIO-SX1262 | `mtbench_xiao_s3_wio` raw coprocessor target | The same host responsibilities, with this target's actual board wiring and image. |
| Linux host with supported SX1262 SPI HAT | Generic SPI/GPIO driver; **no MCU image** | Board-specific Linux SPI/GPIO setup and an application-owned HAT worker. |

Matching the radio chip does not establish board compatibility. MeshCore-derived board support is a build input; this application speaks Meshticulum's own raw protocol. Flashing it changes the device application, and does not make an unmodified MeshCore or Reticulum client compatible.

## Build the two ESP32-S3 targets

Follow the [locked firmware recipe](FIRMWARE_RECIPE.md). Its validated host is **macOS arm64, Python 3.12.8 and PlatformIO Core 6.1.19**. Use the pinned CLI requirements, archives and isolated output. Select one target explicitly, or let the recipe build both:

```sh
python3 firmware/meshticulum-bench/prepare_firmware.py build \
  --output-dir build/firmware-work \
  --pio-python build/firmware-cli/bin/python \
  --target mtbench_heltec_v4
```

For the other target, select `mtbench_xiao_s3_wio` in a separately identified preparation/build. The recipe performs compilation, not upload.

Inspect `build/firmware-work/SOURCE.json` and `evidence/BUILD.json` before selecting an artifact. The target outputs are under:

```text
build/firmware-work/project/.pio/build/<target>/
    firmware.bin
    bootloader.bin
    partitions.bin
    firmware.elf
    firmware.map
```

Retain the ELF, map, source/input lock and hashes. `firmware.bin` is an **application image**, not a universal whole-flash image. The `mtb1-9816f92488` tag identifies the application's two source files, not the complete BSP/toolchain or binary.

## Understand the recorded flash layout

The prepared, locked Arduino ESP32 framework's `tools/platformio-build.py` selects these extra images for ESP32-S3. The recorded partition binaries for both targets place `app0` at `0x10000` and `otadata` at `0xe000`:

| Offset | Image from this exact prepared target |
| --- | --- |
| `0x0000` | Target's `bootloader.bin` |
| `0x8000` | Target's `partitions.bin` |
| `0xe000` | Locked framework's `tools/partitions/boot_app0.bin`, 8,192 bytes |
| `0x10000` | Target's `firmware.bin` |

The Heltec V4 configuration uses a **16 MB** layout; the Xiao S3 WIO configuration uses an **8 MB** layout. Their partition-table binaries differ. The retained selector image's SHA-256 is `f94c5d786a7a8fab06ac5d10e33bf37711a6697636dc037559ea19cc410a17f0`.

Espressif's ESP32-S3 [startup documentation](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32s3/api-guides/startup.html) corroborates bootloader offset `0x0`. Its [partition guide](https://docs.espressif.com/projects/esp-idf/en/v4.4.7/esp32s3/api-guides/partition-tables.html) explains that the table determines application/data locations. A changed board configuration or existing OTA selection needs its actual layout checked; these addresses are not a universal recipe for other ESP32 boards.

## Flashing a spare development board

The following **first-install example writes the bootloader, partition table, OTA selector and application**. It is for a spare, matching development board whose layout and data disposition you have reviewed. It changes the existing device firmware. It is not an app-only fleet update, a rollback procedure or a radio grant.

Use the exact esptool package in the prepared recipe (`4.5.1`) with the firmware CLI environment. Close other users of the serial connection and select the actual board's port. The variables below are paths you choose:

```sh
MT_SERIAL_PORT=/dev/your-confirmed-serial-port
MT_TARGET=mtbench_heltec_v4
MT_BUILD=build/firmware-work
MT_IMAGES="$MT_BUILD/project/.pio/build/$MT_TARGET"

build/firmware-cli/bin/python "$MT_BUILD/packages/esptool/esptool.py" \
  --chip esp32s3 --port "$MT_SERIAL_PORT" write_flash \
  0x0000 "$MT_IMAGES/bootloader.bin" \
  0x8000 "$MT_IMAGES/partitions.bin" \
  0xe000 "$MT_BUILD/packages/framework/tools/partitions/boot_app0.bin" \
  0x10000 "$MT_IMAGES/firmware.bin"
```

This is esptool v4's underscore command syntax. Its [official command reference](https://docs.espressif.com/projects/esptool/en/release-v4/esp32/esptool/basic-commands.html#write-binary-data-to-flash-write-flash) defines address/file pairs and directs the user to the SDK's actual layout. Keep the build's flash header/settings; do not substitute a different board's binaries or assume that `firmware.bin` belongs at zero. Newer esptool releases use different command spelling.

After the write, byte verification can use the same address/file pairs with `verify_flash` in place of `write_flash`. [Espressif documents that command](https://docs.espressif.com/projects/esptool/en/release-v4/esp32/esptool/advanced-commands.html#verify-flash-data-verify-flash). Device identity, boot state, protocol and radio behavior still need separate verification. Secure Boot or Flash Encryption configurations require their own compatible installation procedure; this plaintext development example supplies neither signing nor encryption keys.

### Updating a provisioned device

Use Lantern's reviewed installation/rollback procedure for its deployed devices. Its operator must bind the physical device, current image and rollback artifact before writing.

The generic [`flash_app` library](../src/meshticulum/bench/flash_app.py) supports an app-only transaction at `0x10000` after verifying the actual partition table, OTA selector and pinned installed app. It requires one `ota_0` application at that address, no factory application, and an erased or exact retained default selector; an additional `ota_1` partition is allowed. An arbitrary current OTA selection is refused. The recorded fresh tables pass that layout check, but a particular device still needs independent current-selector and rollback-image correspondence.

The transaction retains an exact-sector rollback receipt and compares unchanged regions. It does not supply a native serial/ROM owner or product CLI. MD5 detects write/readback mistakes; artifact SHA-256 pins and reviewed installation supply authenticity. A matching source tag alone cannot establish current-image equivalence.

## Linux SX1262 SPI HAT

A SPI HAT attaches the radio to the Linux host; it does not take an ESP32 `firmware.bin`. The host application configures the actual SPI bus/chip select, GPIO chip/lines, IRQ, reset and RF switch, then supplies the owned transport to the generic [SX1262](../src/meshticulum/bench/sx1262.py) and [Linux HAT](../src/meshticulum/bench/linux_hat.py) adapters.

The repository contains a [draft Orange Pi 5 Plus overlay](../firmware/meshticulum-bench/orangepi5plus-sx1262-hat.dts) for the specified Waveshare SX1262 LoRaWAN/GNSS HAT. It uses physical pin **40** for NSS rather than SPI0 CS0 on pin 24. It is not a Raspberry Pi or universal HAT configuration, and installing an overlay alone does not initialize or authorize the radio.

On the chosen Linux host, the offline ABI helper can check the kernel-header structure sizes and ioctl values without opening a device:

```sh
mkdir -p build
cc firmware/meshticulum-bench/check_linux_abi.c -o build/check-linux-abi
build/check-linux-abi
```

Actual setup must verify the fitted board, GPIO/CS mapping, supported ABI, exclusive ownership and current host/service state. Lantern owns its Linux setup and worker lifecycle. The SDK does not configure those inputs automatically.

## Before a radio service run

A successful flash or ABI check prepares one layer. The host application still needs independently provisioned peer/session keys, finite service/object grants, durable replay/attempt journals, current revocation/ownership checks, regional profile and antenna confirmation, and bounded disarm/cleanup. Retiring a job or restarting a device preserves previously charged debt.

[Architecture](ARCHITECTURE.md) explains the carrier and application boundaries. [Performance](PERFORMANCE.md) records finite observations at their exact source/runtime pins. Importing a driver, completing a build or flashing this development image does not enable private SAGE production traffic or certify an alpha release.
