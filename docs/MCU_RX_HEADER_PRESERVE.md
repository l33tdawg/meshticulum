# Optional continuous-RX header preservation

This source experiment avoids a full receive restart for one narrow LoRa IRQ
snapshot. It is disabled by default. Both supported target images have built
successfully. One subsequent [physical sample](../benchmarks/2026-10-30-sf5-header-preserve-profile/README.md)
verified 23,900 useful bytes at 1,690.381876 B/s including startup. Repeated
capacity and receive-reliability qualification remain open; this observation
does not establish the cause of earlier missing packets.

The normal driver uses continuous receive (`SetRx(0xFFFFFF)`). The application
currently restarts reception after a header-error snapshot without a valid
header. A valid header or completed packet can arrive between the IRQ read and
that restart, whose standby/IRQ-clear sequence can interrupt or discard it.
The optional path clears only the observed header-error bit while preserving
other IRQ bits. It does not change the radio profile, CRC checking, native
queue capacity, attempt debt, original deadlines or the 5/100 ms receive floors.

## Selection and identity

The existing preparation CLI accepts `--rx-header-preserve`; the Python recipe
accepts exact boolean `rx_header_preserve=True`. Omission and explicit `False`
retain the preceding generated header bytes and build-option shape, including
the selected SPI setting. `True` appends a guarded macro block to the generated
eighth application input and records `rx_header_preserve: true` in the source
manifest. The changed header has its own application identity. Both supported
targets use the same explicit feature selection; the independent SPI option
still changes only Heltec's bus clock.

Preparation still hashes exactly eight application inputs. New application
source changes the identity even when the option is disabled; this does not
rewrite historical firmware identities or imply that rebuilt bytes match an
older image. Build validation checks the canonical options, exact header,
application identity and one forced header inclusion for each supported target.
An externally supplied feature macro conflicts with an enabled generated
header; a lone override without its generated configuration marker is rejected
by the application. These checks support the pinned recipe, not arbitrary
unreviewed compiler commands.

## Narrow path and conservative integrity evidence

The selected snapshot is LoRa HEADER_ERR with optionally PREAMBLE_DETECTED and
no other IRQ bits. RX_DONE retains first priority. An already valid header,
GFSK, payload CRC, timeout, mixed or unrecognized bits retain the existing paths.
The optional branch rechecks the original deadline, latches one fixed software
header-error bit and performs a checked `ClearIrqStatus(HEADER_ERR)`. Failure
disarms, fences and attempts containment. No extra IRQ, radio-status read,
diagnostic clock, heap allocation or authority is introduced.

An IRQ bit cannot distinguish two assertions before it is cleared. The software
shadow therefore remains until a checked successful full RX launch clears the
hardware IRQs and starts a new receive lifetime. Failed launch or disarm does
not erase it. Receive processing still calls RadioLib's unchanged `readData`
and retains every CRC/error result. If that call succeeds but the application's
checked LoRa RX_DONE snapshot lacks HEADER_VALID while shadowed, export is
rejected as a header-integrity failure. This prevents a concurrently reasserted
header error from becoming acceptable merely because its shared bit was cleared.
A current valid header makes the old header error irrelevant, matching the
existing driver rule; payload CRC checking remains active.

The MNR1 wire schema and counter indexes remain unchanged. Its historical
`IRQ_RESTART_HEADER_ONLY` name now includes handling a header-only snapshot by
selective clear when this option is enabled. A shadow-based export rejection
uses the existing header-integrity category. These are software categories,
not packet identities, RF causes or exact counts of acquisitions. In particular,
`IRQ_RESTART_HEADER_ONLY` does not prove an actual RX restart or packet drop;
its unchanged name and schema do not make actual restart counts comparable
across firmware versions.

## Source controls

The native tests compile the actual application with a fake driver that models
write-one-to-clear masks and IRQ arrivals after a read or during a clear. They
cover valid headers and completed packets, concurrent CRC and same-bit header
errors, failed clear/read/rearm, original expiry, shadow lifetime, legacy paths,
and profiling on/off. Address and undefined-behavior sanitizers are enabled.
Separate recipe controls cover exact booleans, disabled-header parity, both
targets, eight-input identity and refusal before build-tool invocation when
metadata, headers or forced inclusion drift.

```sh
PYTHONPATH=src python -m pytest -q tests/test_firmware_rx_header_preserve.py tests/test_firmware_rx_header_option.py
```

These tests do not model RF propagation, real demodulator behavior or electrical
SPI faults. Hardware qualification requires a separately reviewed exact image,
original finite authority and retained postflight evidence.

## Target build evidence

The [build record](../provenance/MCU_RX_HEADER_PRESERVE_FIRMWARE.json) binds one
preparation of source commit `bda11e7798a25e6ed7abd9839cc50683e7816a14` to
firmware identity `mtb1-845b22b090`. Native profiling and header preservation
were enabled. The explicit SPI setting selects 8 MHz on Heltec; XIAO retains
its 2 MHz setting. This is the opt-in build, not a change to recipe defaults.

| Target | Firmware bytes | SHA-256 |
| --- | ---: | --- |
| Heltec V4 | 404,496 | `d98391bb4341aecda6b3e3f469875a3c396326afd283f4dce401e02cfa3c9b0b` |
| XIAO ESP32-S3 + Wio SX1262 | 398,176 | `fe949908082e15f1cf5d31c35c2db979d236d1a6db0a3d582b3ecdedab5ccff7` |

The source-manifest SHA-256 is
`904e03e90f6b8a519eaffaef179ceb81fbeb278d1d5a71d02aeb9836e58f996d`;
the build-manifest SHA-256 is
`567a1059cd69821fc7dee9948a3797746d70a092984e4014310b4dc55cb95d9e`.
Both builds returned success with the locked PlatformIO 6.1.19 toolchain.
The record retains all eight application hashes and both targets' binary,
ELF, map, bootloader and partition hashes. It records no flashing or RF
qualification. Successful linking does not establish live heap/stack headroom,
receive reliability, or any throughput improvement.

## First physical observation

The [30 October header-preservation sample](../benchmarks/2026-10-30-sf5-header-preserve-profile/README.md)
used the Heltec image above on both endpoints, with exact SDK `7ac7daf`, the
preceding v6 host policy and 8 MHz SPI. It completed in 14.138817 seconds from
sender process startup through authenticated receipt, with publisher and
whole-file verification. There were 17 DATA retries, compared with 26 in the
preceding v6 sample. The observed rate rose 38.284511%, but the loss pattern
and MCU boots differed; no isolated causal improvement is established.

The receiver recorded 66 header-only classifications, two header read errors
and one invalid reported length; the sender recorded two header-only
classifications. These counters retain the software semantics above. They do
not identify the cause of any missing authenticated packet. Both endpoints
closed and drained, original scope clocks expired, and all prior paid debt
remained retained. The result is below the earlier 2,026.493477 B/s sample and
the above-2,800 B/s checkpoint and repeated 3,000 B/s goal. Published alpha.3
assets are unchanged.
