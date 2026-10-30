# Optional continuous-RX header preservation

This source experiment avoids a full receive restart for one narrow LoRa IRQ
snapshot. It is disabled by default. It has no target build or physical loss or
throughput qualification, and does not establish the cause of earlier missing
packets.

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
not packet identities, RF causes or exact counts of acquisitions.

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
