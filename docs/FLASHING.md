# Replace an application on your own ESP32-S3 board

Build the selected target with the [pinned recipe](FIRMWARE_RECIPE.md), then keep
`SOURCE.json`, `evidence/BUILD.json`, the application binary and their SHA-256
values together. `SOURCE.json.identity` is the exact HELLO version expected by
the host. Use the generated application inventory in that file: MCU020 used
four inputs, while the DATA queue application uses six. A historical
documentation tag is not your build identity.
The application identity does not identify the complete linked binary or SDK.

This procedure is for a board you own with an independently known current
ESP32-S3 application and a compatible existing partition/boot layout. It writes
only application sectors at `0x10000`. It does not initialize a blank board,
replace a bootloader, change OTA selection, erase the chip or provision RF
authority. If the layout or rollback checks below refuse, obtain a reviewed
board-specific installation procedure rather than forcing the write.

## Establish ownership and rollback before writing

Use an explicitly selected, exclusive serial port and an exact board/eFuse MAC
record. Stop only your own conflicting application before this procedure; do
not take a port from another service. Retain its old run/attempt records. A ROM
reset changes the MCU boot identity and counters; it does not refund previous
work or allow old sessions to resume.

The following command forms are from the recipe's pinned **esptool 4.5.1**
interface. Use your explicitly selected environment (`$FLASH_PYTHON`), stable
serial path (`$PORT`) and recorded device MAC. Each `timeout` command is finite
(GNU `timeout` syntax); a timeout or nonzero exit is a failed step. This guide
does not run them for you or install a flasher on your system.

```sh
"$FLASH_PYTHON" -m esptool version
timeout -k 5s 20s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset read_mac
timeout -k 5s 20s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset flash_id
"$FLASH_PYTHON" -m esptool image_info "$NEW_APP"
"$FLASH_PYTHON" -m esptool image_info "$KNOWN_CURRENT_APP"
```

Check the actual MAC, detected flash capacity and ESP32-S3 image information.
Use the Heltec target only for a Heltec V4 (board ID 1), and the Xiao target only
for its matching hardware. A MAC alone does not prove the board model. The
current application's host SHA-256 must come from an independently trusted
current-image record, not a convenient old image from another installation.

Choose a **new private `0700` evidence directory**. Retain all failed steps.
Read only the existing partition table first:

```sh
timeout -k 5s 30s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset \
  read_flash 0x8000 0x1000 "$EVIDENCE/partitions.bin"
```

Validate that table using the public API and the *observed* flash size in bytes:

```python
from pathlib import Path
from meshticulum.bench.flash_app import partitions
app, ota = partitions(Path("partitions.bin").read_bytes(), observed_flash_bytes)
print(app, ota)
```

This validator requires one OTA-0 app at `0x10000`, no factory app taking
precedence, an 8 KiB OTA selector, checked table checksum, nonoverlapping plain
partitions and sufficient flash capacity. Read the returned OTA offset/size and
require either all `0xff` bytes or the exact default selector hash
`flash_app.APP0_OTA_SHA256`. Keep the table and selector unchanged. Do not infer
that the new build's partition file describes the already installed layout.

Compute `SPAN = ceil(new_app_bytes / 4096) * 4096`, require both the new span and
known current image to fit the validated app partition, and require ESP32-S3
image headers for both files (`0xe9`, chip ID 9 at bytes 12–13). Read **that
exact span** into a new rollback-sector file:

```sh
timeout -k 5s 180s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset \
  verify_flash 0x10000 "$KNOWN_CURRENT_APP"
timeout -k 5s 180s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset \
  read_flash 0x10000 "$SPAN" "$EVIDENCE/app-before.bin"
timeout -k 5s 180s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset \
  verify_flash 0x10000 "$EVIDENCE/app-before.bin"
```

`verify_flash` compares the ROM MD5 with the pinned host bytes. MD5 detects a
range mismatch; it does not authenticate an image or establish device SHA-256.
Record the backup's exact length and SHA-256. Preserve bytes after the new image
in its final sector when constructing the payload:

```python
from hashlib import sha256
from pathlib import Path
new = Path("firmware.bin").read_bytes()
before = Path("app-before.bin").read_bytes()
assert len(before) == (len(new) + 4095) // 4096 * 4096
payload = new + before[len(new):]
with open("app-payload.bin", "xb") as output:
    output.write(payload)
    output.flush()
    import os
    os.fsync(output.fileno())
print(len(payload), sha256(payload).hexdigest())
```

Flush and `fsync` the backup, payload and retained evidence files and their
directory before writing the device. A failed sync refuses the write; having
printed a hash is not evidence that a rollback copy survived a power loss.

Retain the new image's source/build hash, backup hash, payload hash, MAC,
partition/selector hashes and original state before any write. Keep sufficient
time and exclusive ownership to restore the backup if writing or boot fails.
For an application integrating a pinned ROM adapter, the existing
[`flash_app.plan/apply/Receipt/rollback`](../src/meshticulum/bench/flash_app.py)
performs durable phase recording, ROM outside-range MD5 checks and exact-range
rollback; those APIs do not supply a fleet/device owner.

## Write, verify, boot and inspect

Write the single app range; do not add a bootloader, partition or OTA file to
this command. Do not use `erase_flash`, `--erase-all`, `--force`, encryption
overrides or an upload recipe that replaces the layout.

```sh
timeout -k 5s 180s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset \
  write_flash --flash_mode keep --flash_freq keep --flash_size keep \
  0x10000 "$EVIDENCE/app-payload.bin"
timeout -k 5s 180s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after no_reset \
  verify_flash 0x10000 "$EVIDENCE/app-payload.bin"
```

Re-read and compare the partition table and OTA selector before booting. The
manual CLI route verifies the listed ranges; it does not claim the API
transaction's outside-range MD5 evidence. If the write, verification or layout
comparison fails, retain the evidence and restore the checked exact-range
backup while still owning the same board. Recheck its identity and layout,
write `app-before.bin` at `0x10000` with the same `keep` options, then
`verify_flash` both `app-before.bin` and `KNOWN_CURRENT_APP`. A failed restore is
an unresolved recovery condition, not success. Preserve OTA, NVS, bootloader
and old accounting records.

After a successful verified write, boot through the same explicitly selected
esptool connection:

```sh
timeout -k 5s 20s "$FLASH_PYTHON" -m esptool \
  --chip esp32s3 --port "$PORT" --no-stub --after hard_reset run
```

For the Heltec GFSK250 receiver, obtain its application serial path again.
First use the [offline firmware-identity step](../examples/README.md#prepare-the-firmware-identity-offline)
to create a compact identity from the full `SOURCE.json` and its independently
checked SHA-256. Hardware commands accept at most 64 KiB of metadata; a full
build manifest can exceed that limit. Keep the full manifest and build evidence.
Set `FIRMWARE_IDENTITY` to the compact file and `FIRMWARE_IDENTITY_SHA256` to
that compact file's checked SHA-256, then run the public read-only inspection:

```sh
sudo .venv/bin/python examples/radio_file.py inspect \
  --device "$BY_ID" --usb-serial "$USB_SERIAL" \
  --firmware-source "$FIRMWARE_IDENTITY" \
  --firmware-source-sha256 "$FIRMWARE_IDENTITY_SHA256"
```

It checks exact HELLO board/version, current-boot GFSK250 capabilities and the
unconfigured post-boot STATUS, then closes the port. It sends no ARM, TX or
DISARM and consumes no run grant. Require the expected firmware identity, a
fresh boot, and disarmed/not-transmitting/unfenced status. A later configured
GFSK status is intentionally outside this post-boot check. A failed new-app
boot/inspection requires rollback with the retained same-board backup and
verification of the old app; it does not authorize an RF test.

Flashing and source/model checks do not establish range, sustained goodput,
radio delivery, complete dependency notices or release qualification. Continue
with the [encrypted file example](../examples/README.md) only under separately
approved current grants and exclusive ownership.
