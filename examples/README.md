# Encrypted files on your own radio hardware

[`radio_file.py`](radio_file.py) composes the existing public SDK. It uses
ChaCha20-Poly1305 DATA and ACK frames, durable nonce/replay state, durable attempt
reservations, bounded reliable flow, and verified file chunks. It imports no
Lantern or SAGE application code. The [17 October public SDK measurements](../docs/RADIO_SDK_MEASUREMENTS.md)
record a fresh 23,900-byte file and two fresh five-part 100,000-byte files on the direct
GFSK250 HAT-to-MCU path at about one metre. Repetition, installation and recovery
on other setups, field range and release qualification remain open. Model
controls alone do not establish physical delivery or useful throughput.

For the 100 kB HAT-sender/MCU-receiver starter, prefer the existing
[signed byte-bundle/MB1 path](BUNDLE.md): five parts instead of seven, using the
existing public 32 KiB service capability. The smaller generic object example
below remains useful for individual chunks and offline verification.

## Hardware and host requirements

Use two Linux hosts you administer, each with an attached antenna suitable for
the frequency and power you independently authorize. The example supports the
existing explicit GFSK250 profile; choosing it does not establish local RF
permission or antenna suitability.

- **Sender:** the existing `LinuxHAT` wiring: Orange Pi 5 Plus, aarch64 Linux,
  SX1262 on SPI0.0 and GPIO3. The driver checks the device tree, chip select,
  1 MHz SPI limit and fixed GPIO lines. Arbitrary HAT wiring is unsupported.
- **Receiver:** Heltec V4, application board ID 1, connected by its stable
  `/dev/serial/by-id/…` path. The USB identity must be `303a:1001` with your
  board's actual serial number. Install and verify the firmware using the
  [app-only flashing procedure](../docs/FLASHING.md). Keep its `SOURCE.json`
  and exact SHA-256 beside the host example.

Run the hardware command as root. `ExclusiveSerial` and `LinuxHAT` require
exclusive ownership and refuse another opener. Arrange that ownership before
running; the example neither stops services nor takes over an occupied device.
The build recipe's macOS-arm64 host is separate from these Linux runtime hosts.

While the SDK holds the device, its process must not fork or pass the device
file descriptors to another process. Keep device aliases inside `/dev` and do
not change device aliases or mounts during a transfer. Host root remains trusted;
these cooperative ownership checks do not isolate hardware from host root.
On Linux, the SDK installs an open-event watch before its initial full procfs
collision scan. It watches existing `/dev` aliases and checks the retained
nodes and directory inventories on every ownership probe. For character/block
radios, normal file churn inside `/dev/shm` or `/dev/mqueue` is exempt only when
Linux reports their covering and nested mounts as `nodev`. The full mount table
is freshly compared on every probe; any mount or flag change refuses ownership.
Existing matching aliases inside those namespaces still have inode/open watches,
including symlinks to devices on other mounts. An open, changed node, lost watch,
queue overflow or probe error latches a refusal. The watch is
closed with the held transport. Kernels without inotify keep a fresh full
procfs scan on every probe. Neither path caches grant or deadline decisions.

Install the checked-out Python package on each host:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/radio_file.py --help
```

Use a pinned checkout and retain its revision. The example is a checkout file,
not an installed console command. Package installation performs no device work.

## Prepare the firmware identity offline

The firmware build's `SOURCE.json` can contain a large source inventory. Make a
compact identity file before passing it to the hardware commands, whose metadata
reader accepts at most 64 KiB. Use the independently checked SHA-256 of the full
manifest:

```sh
mkdir -m 700 firmware-identity
.venv/bin/python examples/radio_file.py firmware-identity \
  --firmware-source SOURCE.json --firmware-source-sha256 "$SOURCE_SHA256" \
  --output firmware-identity/identity.json
sha256sum firmware-identity/identity.json
```

The offline command accepts a regular manifest up to 8 MiB, checks its supplied
hash and application identity, and creates the output once. It retains the
original manifest hash as `source_manifest_sha256`. It does not verify a build,
read a device or establish that the firmware is installed. Keep the full
manifest and build evidence alongside it. In the commands below, set
`FIRMWARE_IDENTITY` to this compact file and `FIRMWARE_IDENTITY_SHA256` to its
SHA-256 (also returned as `firmware_source_sha256` by the command). The output
must be a new file in an owner-only `0700` directory.

## Prepare the file and approve the sessions

The manifest format can describe up to 1 MiB; that is **not** the runnable
capacity of a 240-second radio interval. Twelve or more sequential 20-second
graces cannot fit even with zero DATA time. The hardware command refuses an
impossible total grace bound and rechecks remaining graces against the actual
remaining original monotonic interval before opening hardware or claiming a grant.
Create a public manifest, then transfer it
and its independently checked SHA-256 to the receiver through your trusted
administration channel. A manifest received over an unauthenticated radio is
not a trusted file description.

```sh
.venv/bin/python examples/radio_file.py manifest input.bin > file.json
sha256sum file.json
```

A 100,000-byte file has seven chunks of at most 16,384 bytes. Each chunk needs
its own pair of **already approved** `RunAuthority` directories. Provisioning
and approval belong to your application/operator; this example consumes the
generic SDK contract and supplies no fleet provisioning or permissive authority.
Each directory is owned by the executing root user, mode `0700`, and contains:

| File | Required content | Mode |
| --- | --- | --- |
| `grant.json` | A current `meshticulum.bench-run.v1` plan | `0600` |
| `secret.bin` | The same independently distributed 32-byte pairwise secret at both endpoints | `0600` |
| `active` | Exactly the bytes `active\n` | `0600` |

The [`RunAuthority` contract](../src/meshticulum/bench/authority.py) validates the
complete plan. Your application must set the real host boot UUID from
`/proc/sys/kernel/random/boot_id`, its local unit and peer, the same sender,
initiator/responder, session ID and generation at both endpoints, `radio_modem`
`gfsk250`, and the exact chunk descriptor from `file.json`. Every chunk uses
fresh session ID/generation and distinct state directories. The profile is an
explicit `Gfsk250Profile.to_dict()` value; the local unit differs between peers
but the shared route/profile/object values must agree.

Approve all chunk grants before starting. Use the **same original issued and
expiry epoch microseconds** for every grant in the file. The interval is at most
240 seconds; preparation, startup, DATA, retries, ACK grace and cleanup consume
that interval. Neither a later chunk nor this example creates a new expiry.
Changing a CLI value cannot update the already approved grant.
The cache also durably retains the first file-window record, including the
original host boot and monotonic end. Another invocation with different clocks
or boot refuses. Wall-clock rollback between commands does not create a later
monotonic end. Each owner/worker check still enforces that original end, and the
worker's own deadline is tightened to it. The first MCU identity is retained;
a later MCU reset cannot silently replace its cumulative boot budget.

Supply a separate root-owned `0600` JSON file matching the inert
[`Gfsk250Admission`](../src/meshticulum/bench/admission.py) fields: `country`,
`frequencies_hz`, `powers_dbm`, `min_lease_ms`, `max_lease_ms`,
`max_boot_attempts`, `max_boot_estimated_airtime_us`. The two tuple fields are
JSON arrays. These are limits your application has independently admitted,
not permission inferred from the grant or from a firmware feature reply.
Pass the exact file hash to the command.

A plan without a `radio_modem` marker selects the LoRa profile path instead of
GFSK250. The same commands apply, but the plan's `profile` is the seven-field
`RadioProfile` tuple (frequency, bandwidth, spreading factor, coding rate, chip
power, preamble, sync word), and the admission file supplies the inert
[`RadioAdmission`](../src/meshticulum/bench/admission.py) fields: `country`,
`waveforms` (a JSON array of those seven-field rows), `min_lease_ms`,
`max_lease_ms`, `max_boot_attempts` and `max_boot_estimated_airtime_us`.
The LoRa path refuses an unknown modem marker and never upgrades a profile the
admission did not list.

The public LoRa admission retains a maximum 180-second lease, **1,024 cumulative
attempts** and a **300,000,000-microsecond** estimated-airtime ceiling. The MCU
retains cumulative boot attempt/airtime counters across all chunk sessions. This
direction uses the HAT for DATA and the MCU principally for ACKs. A fresh 100 kB
**MCU sender** needs at least 519 DATA frames; that fits only when at least 519
attempts remain on the current boot. Check the counter before preparing the
plan. Do not reset the MCU or replace old journals to work around a spent
limit.

## Transfer one approved chunk

Create a root-owned `0700` chunk-cache directory on each host. Fill the values
below from your reviewed files and current hardware; they are not example
permissions or preapproved device identities.

Start the receiver first, then start the sender promptly. Both must reach
startup within the existing reliable-flow bound. The `transfer` command arms
its radio and can transmit encrypted DATA **or ACKs**, including on the receiver.

```sh
sudo .venv/bin/python examples/radio_file.py transfer \
  --interface usb --device "$BY_ID" --usb-serial "$USB_SERIAL" \
  --unit "$RECEIVER_UNIT" --grant-directory "$RECEIVER_GRANT" \
  --grant-sha256 "$RECEIVER_GRANT_SHA256" \
  --manifest file.json --manifest-sha256 "$MANIFEST_SHA256" \
  --admission admission.json --admission-sha256 "$ADMISSION_SHA256" \
  --firmware-source "$FIRMWARE_IDENTITY" \
  --firmware-source-sha256 "$FIRMWARE_IDENTITY_SHA256" \
  --file-issued-epoch-us "$ORIGINAL_ISSUED_US" \
  --file-expires-epoch-us "$ORIGINAL_EXPIRES_US" \
  --chunk 0 --chunks "$PRIVATE_CHUNK_CACHE"
```

On the HAT sender, use the corresponding sender grant and add the input file:

```sh
sudo .venv/bin/python examples/radio_file.py transfer \
  --interface hat --unit "$SENDER_UNIT" --grant-directory "$SENDER_GRANT" \
  --grant-sha256 "$SENDER_GRANT_SHA256" --input input.bin \
  --manifest file.json --manifest-sha256 "$MANIFEST_SHA256" \
  --admission admission.json --admission-sha256 "$ADMISSION_SHA256" \
  --firmware-source "$FIRMWARE_IDENTITY" \
  --firmware-source-sha256 "$FIRMWARE_IDENTITY_SHA256" \
  --file-issued-epoch-us "$ORIGINAL_ISSUED_US" \
  --file-expires-epoch-us "$ORIGINAL_EXPIRES_US" \
  --chunk 0 --chunks "$PRIVATE_CHUNK_CACHE"
```

The commands claim their grant once and create `attempts.db` and `wire.db`
without replacing existing files. The receiver verifies and durably commits
the chunk, then continues its original flow for **20 seconds** so a lost final
ACK can be answered. It does not restart that grace on duplicates. Original
grant, application and MCU leases still bound every step. Closure disarms the
worker, retires the wire session and closes owned descriptors; failure to prove
disarm is a failed command. A successful sender result proves authenticated
flow receipt; it does not claim receiver filesystem publication.

Start the next approved chunk pair only after both preceding commands close.
Use indices 0 through 6 for 100,000 bytes; retain the same file manifest and
original file clocks. Coordinate these finite commands through your application
or trusted operator channel. The example adds no automatic discovery, renewal,
retirement shortcut or cross-host orchestration service.

Seven receiver graces alone cost 140 seconds. A 240-second window then leaves
**less than 100 seconds** for 100 kB of DATA, all setup and cleanup. Exactly
1,000 useful B/s is therefore insufficient for this seven-session example.
Source models complete inside the original interval; actual completion and
throughput require measurement on your exact hosts and hardware. Expiry fails
the operation rather than extending a timer.

## Verify and publish the whole file

After all receiver commands close successfully, assemble from the verified
cache into a new file in a root-owned `0700` destination directory:

```sh
sudo .venv/bin/python examples/radio_file.py assemble \
  --manifest file.json --manifest-sha256 "$MANIFEST_SHA256" \
  --chunks "$PRIVATE_CHUNK_CACHE" --output "$NEW_OUTPUT_FILE"
```

Assembly rechecks every chunk and the whole-file SHA-256, then uses a durable
no-replace publication. It opens no radio and grants no RF authority. The output
must not exist. An interrupted transfer may leave verified chunks for a later,
independently approved operation; that does not reuse a nonce or erase spent
attempts. Such a new operation needs a separate file-window record/cache;
independently verify any reused chunk instead of editing the old window.
Keep old grants, session state, journals, errors and unknown outcomes.
Re-running a spent directory refuses instead of silently starting fresh work.
