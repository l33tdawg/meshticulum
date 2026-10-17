# Public SDK radio measurements: 17 October 2026

The public SDK completed **two fresh 100,000-byte files at 2,030.2 and 1,949.2 useful B/s**, from each file's first sender startup through its final authenticated receipt. Both five-part files and their publishers were independently verified. A separate **23,900-byte file reached 2,534.6 useful B/s** at the same timing boundary.

All three samples used source `da247a1bb74601973f0bb72f51c268a412714719`, an Orange Pi 5 Plus SX1262 HAT sender and a Heltec V4 MCU receiver about **one metre apart**. The modem used **GFSK at 250 kb/s**. These are direct, near-bench GFSK results; they provide no LoRa-modulation, multihop or field-range measurement. No new firmware was flashed for these samples.

## Useful bytes and elapsed time

Rates use decimal bytes: **1 kB = 1,000 bytes**. The numerator is the original decoded file length, excluding manifests, framing and encoded representation bytes. The elapsed intervals include transport overhead, host work, ACKs and any retries.

| File and receiver retirement | Timing boundary | Elapsed seconds | Useful decoded B/s |
| --- | --- | ---: | ---: |
| 23,900 bytes; default 20-second grace | Sender startup → authenticated receipt | 9.429571 | **2,534.6** |
| Same file | First DATA-worker call → authenticated receipt | 8.168009 | 2,926.0 |
| Same file | Scope issue → both endpoints closed | 34.064723 | 701.6 |
| 100,000 bytes, run 1; authenticated receipt confirmation | First sender startup → final authenticated receipt | 49.255870 | **2,030.2** |
| Same five-part file | First DATA-worker call → final authenticated receipt | 48.154962 | 2,076.6 |
| Same five-part file | Scope issue → both endpoints closed | 53.879126 | **1,856.0** |
| 100,000 bytes, run 2; authenticated receipt confirmation | First sender startup → final authenticated receipt | 51.302845 | **1,949.2** |
| Same second file | First DATA-worker call → final authenticated receipt | 50.360284 | 1,985.7 |
| Same second file | Scope issue → both endpoints closed | 56.957045 | **1,755.7** |

The 100 kB intervals span all five sequential parts, including coordination between them. They are whole-file elapsed intervals, rather than sums of selected busy periods. Scope-to-closure also includes preparation and endpoint cleanup. The 23,900-byte sample keeps the default receiver grace: its 20-second wait appears in scope-to-closure time, after the sender has received its authenticated ACK.

## Completion and receiver confirmation

Each 100 kB file used four 23,900-byte parts and one 4,400-byte part. Both endpoints enabled `--confirm-receipt`: after accepting the final flow ACK, the sender transmits an authenticated confirmation that lets the receiver close its ACK grace early. **All ten receiver confirmations were physically accepted across the two files.** Missing or invalid confirmation still leaves the original 20-second fallback; the option does not extend a lease, reset a deadline or refund attempt debt. See the [bundle example](../examples/BUNDLE.md#optional-sender-receipt-confirmation).

The first 100 kB run had **zero DATA retransmissions**; the second had **one**. Both had **zero new receiver drops**. All twenty endpoint children exited successfully, with no forced cleanup and unchanged services and boot identity. The smaller sample had one DATA retransmission and zero new receiver drops. Publisher and whole-file verification were required separately from sender receipt. The two 100 kB inputs have different SHA-256 identities, recorded in the public JSON. No parts were cached before any run. Verified earlier parts were reused only within each file's own original window.

## What changed, and what remains

The SDK now checks fresh kernel open-event evidence alongside descriptor, inode, mount and ownership bindings, avoiding repeated scans of every process descriptor during normal packet work. Verified `nodev` IPC namespaces allow ordinary shared-memory file churn without invalidating device ownership. Initial collision scans, existing-alias watches and fault refusal remain. These are host-side changes; these samples do not isolate a firmware speedup.

These startup-inclusive samples exceed the **1,000 B/s alpha floor for their stated path**. Two fresh 100 kB files provide an initial repeat, not a sustained-throughput or completion distribution or a guarantee on another installation. Continue repeating complete bundles, check installation and recovery on the declared supported path, and finish the exact artifact, firmware/source/notice and release reviews. Native/private integration and security qualification remain separate. **An alpha has not been accepted or published.**

Measured RF airtime, energy, EIRP and field range remain unavailable. GFSK results do not replace the [earlier LoRa measurements and scoped upstream comparisons](PERFORMANCE.md). No general speed or security ranking against MeshCore or Reticulum follows from these samples.

## Inspect the published record

[Sanitized results and source-capture hashes](../benchmarks/2026-10-17-public-sdk/results.json) contain the measured fields needed to check the arithmetic. The operator captures stay in the lab; hashes and an allowlisted transcription do not independently attest the hardware. The public record excludes grants, addresses, device identifiers, boot values and operational logs.

```sh
python tools/verify_benchmark_report.py benchmarks/2026-10-17-public-sdk/results.json
```

See the [roadmap](ROADMAP.md) for the next work and [release readiness](RELEASE_READINESS.md) for the remaining gates.
