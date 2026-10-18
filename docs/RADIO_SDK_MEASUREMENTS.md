# Public SDK radio measurements: 17–18 October 2026

The public SDK completed **two fresh 100,000-byte files at 2.03 and 1.95 kB/s (16.24 and 15.59 kbit/s)** of useful application throughput, from each file's first sender startup through its final authenticated receipt. Both five-part files and their publishers were independently verified. A separate **23,900-byte file reached 2.53 kB/s (20.28 kbit/s)** at the same timing boundary.

The three samples above used source `da247a1bb74601973f0bb72f51c268a412714719`, an Orange Pi 5 Plus SX1262 HAT sender and a Heltec V4 MCU receiver about **one metre apart**. The modem used **GFSK at 250 kbit/s**. These are direct, near-bench GFSK results; they provide no LoRa-modulation, multihop or field-range measurement. No new firmware was flashed for these samples.

The tested [`Gfsk250Profile`](https://github.com/l33tdawg/meshticulum/blob/da247a1bb74601973f0bb72f51c268a412714719/src/meshticulum/bench/gfsk250_profile.py) used **921,375,000 Hz**, **0 dBm configured chip power**, a **250,000 bit/s** rate, **62,500 Hz** frequency deviation, **467,000 Hz** receive bandwidth and a **32-bit** preamble. Configured chip power is not a measurement of EIRP; measured EIRP remains unavailable.

## Larger public files

On **18 October 2026**, source `1f85608e2c5e543130673529e21d838917094c11`
completed fresh whole-file transfers through the same public signed-bundle path
and hardware. These use 11- and 21-part geometries and their own original
520- and 720-second windows. Each input, publisher key, grant set and receiver
state was fresh.

| Useful file | Parts | First sender startup → final authenticated receipt | First DATA → final receipt | Scope issue → both endpoints closed |
| ---: | ---: | ---: | ---: | ---: |
| 250,000 bytes | 11 | 118.420015 s; **2,111.1 B/s (16.889 kbit/s)** | 117.382857 s; 2,129.8 B/s (17.038 kbit/s) | 121.214435 s; **2,062.5 B/s (16.500 kbit/s)** |
| 500,000 bytes | 21 | 225.668497 s; **2,215.6 B/s (17.725 kbit/s)** | 223.949464 s; 2,232.6 B/s (17.861 kbit/s) | 228.292237 s; **2,190.2 B/s (17.521 kbit/s)** |
| 1,000,000 bytes | 42 | 571.484490 s; **1,749.8 B/s (13.999 kbit/s)** | 570.384109 s; 1,753.2 B/s (14.026 kbit/s) | 573.955922 s; **1,742.3 B/s (13.938 kbit/s)** |

The 250 kB receiver independently verified all 11 parts and the complete file.
All 22 endpoint processes exited zero and all 11 authenticated receipt
confirmations were accepted. It used 372 receiver attempts and 1,353 sender
attempts, with one DATA retransmission and no new raw receive drops.

The 500 kB receiver independently verified all 21 parts and the complete file.
All 42 endpoint processes exited zero and all 21 authenticated receipt
confirmations were accepted. It used 736 receiver attempts and 2,700 sender
attempts, with one DATA retransmission and one new raw receive drop. The
original controller stopped at that diagnostic counter change only after the
final authenticated receipt and whole-file verification had succeeded. The
stopped result is retained; the corrected result follows the existing policy
that records receive drops as diagnostics while requiring exact content,
receipts and settled attempts.

The 1 MB receiver independently verified all 42 parts and the complete file.
All 84 endpoint processes exited zero and all 42 authenticated receipt
confirmations were accepted. It used 1,530 receiver attempts and 5,400 sender
attempts, with one DATA retransmission and one additional raw receive drop.
The complete command remained inside its original 1,140-second window.

These larger-file results remain direct near-bench GFSK250 measurements. They
do not establish LoRa modulation, field range, sustained-capacity distribution,
or operation on another installation.

## Useful bytes and elapsed time

Rates use decimal bytes: **1 kB = 1,000 bytes**. Multiply bytes per second by eight for bit rates. The numerator is the original decoded file length, excluding manifests, framing and encoded representation bytes. The elapsed intervals include transport overhead, host work, ACKs and any retries.

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

For the two 100 kB files, the exact sender-start rates were **2,030.2 and 1,949.2 B/s**, or **2.0302 and 1.9492 kB/s**, equivalent to **16.2416 and 15.5936 kbit/s**. Scope-to-closure rates were **1,856.0 and 1,755.7 B/s**, or **1.856 and 1.7557 kB/s**, equivalent to **14.848 and 14.0456 kbit/s**. The 250 kbit/s figure above is the modem link rate; these lower rates are useful application bytes after transport, encryption, verification and retry costs.

The 100 kB intervals span all five sequential parts, including coordination between them. They are whole-file elapsed intervals, rather than sums of selected busy periods. Scope-to-closure includes grant delivery, endpoint startup, coordination, verification and endpoint cleanup. Offline file generation, signing, build and staging happened before scope issue and are excluded from this interval; it does not measure a complete cold deployment. The 23,900-byte sample keeps the default receiver grace: its 20-second wait appears in scope-to-closure time, after the sender has received its authenticated ACK.

## Completion and receiver confirmation

Each 100 kB file used four 23,900-byte parts and one 4,400-byte part. Both endpoints enabled `--confirm-receipt`: after accepting the final flow ACK, the sender transmits an authenticated confirmation that lets the receiver close its ACK grace early. **All ten receiver confirmations were physically accepted across the two files.** Missing or invalid confirmation still leaves the original 20-second fallback; the option does not extend a lease, reset a deadline or refund attempt debt. See the [bundle example](../examples/BUNDLE.md#optional-sender-receipt-confirmation).

The first 100 kB run had **zero DATA retransmissions**; the second had **one**. Both had **zero new receiver drops**. All twenty endpoint children exited successfully, with no forced cleanup and unchanged services and boot identity. The smaller sample had one DATA retransmission and zero new receiver drops. Publisher and whole-file verification were required separately from sender receipt. The two 100 kB inputs have different SHA-256 identities, recorded in the public JSON. Each receiver began with fresh verified state. The common staging pack contained source chunks on both hosts, but receiver acceptance used the bytes delivered through `ServiceTransfer`, publisher verification and whole-file hashing; staged source chunks were not used to accept the received file. Verified earlier parts were reused only within each file's own original window.

## Clean installation and verified-piece continuation

A separate fresh **100,000-byte file completed from the published wheel** on the same near-bench GFSK250 path. The receiver used Python **3.11.2** and the sender **3.14.4**, both Linux aarch64. Each normal virtual environment imported the installed distribution in isolated Python mode, with no editable checkout, SDK zip or system site packages. The check matched all **67 runtime files** to the wheel from source `673eb60d24dc60f67371e24bc5c9caeec3a68508`. Its wheel SHA-256 is `dce5127fed0956e5b3d2cd9ce7dd859a2a4f6af8cdd491bfaf9ba5723e495c1c`.

The environments used hash-pinned wheels for `cryptography 50.0.2`, `PyNaCl 1.6.2`, `cffi 2.1.1` and `pycparser 3.11`. Installation and dependency acquisition finished before the radio window. These dependency versions differ from the earlier source-staged samples; the installed check has its own record.

After part 0 was verified and both endpoint processes had closed, the coordinator paused for **5.010819 seconds on its monotonic clock**, then opened fresh processes for parts 1–4. The receiver reopened and checked the verified prefix, reusing four earlier parts by final assembly. All work retained the original grants and **240-second window**, with no deadline extension or attempt refund. This demonstrates continuation from a completed verified piece. It does not demonstrate resuming an in-flight session after a crash.

| Installed-wheel whole-file boundary | Elapsed seconds, including the pause | Useful decoded B/s |
| --- | ---: | ---: |
| First sender startup → final authenticated receipt | 57.475095 | **1,739.9** |
| First DATA-worker call → final authenticated receipt | 56.690410 | 1,764.0 |
| Scope issue → both endpoints closed | 61.983317 | **1,613.3** |

That installed-wheel file delivered **1,739.9 B/s**, or **1.7399 kB/s and 13.9191 kbit/s**, from sender startup through authenticated receipt. Its scope-to-closure rate was **1,613.3 B/s**, or **1.6133 kB/s and 12.9067 kbit/s**.

All five authenticated receiver confirmations were accepted. The file had one DATA retransmission and zero new raw receive discards. All ten endpoint children exited successfully with no forced cleanup; owned endpoints were closed and disarmed, with services and host lifetimes unchanged. Separate reviews checked the signed publisher manifest, reconstructed file, installed runtime files, original window, journals and endpoint captures. The recorded remote final-file readback matched the input. These checks do not independently reverify raw RF ciphertext, which was not retained; the exact SDK receipts and matching packet hashes provide the RF authentication evidence. Owned-endpoint cleanup does not establish global quiescence.

An earlier installed-wheel trial stopped after **four verified parts / 95,600 bytes** when a harness condition required no change in the raw receive-discard counter; that counter rose from two to three. All eight started children exited successfully, but the fifth part never started and no whole-file completion or rate is claimed. The harness now records discard changes as diagnostics while still requiring authenticated receipts, publisher/hash checks, settled journals and the original window. The successful run used a fresh file and window; the earlier attempt debt remains retained.

[The sanitized installed-SDK record](../provenance/PUBLIC_INSTALLED_SDK_PROOF.json) contains the package and dependency hashes, timing arithmetic, continuation conditions and earlier incomplete attempt. It is separate from the two uninterrupted files in the headline.

## What changed, and what remains

The SDK now checks fresh kernel open-event evidence alongside descriptor, inode, mount and ownership bindings, avoiding repeated scans of every process descriptor during normal packet work. Verified `nodev` IPC namespaces allow ordinary shared-memory file churn without invalidating device ownership. Initial collision scans, existing-alias watches and fault refusal remain. These are host-side changes; these samples do not isolate a firmware speedup.

These startup-inclusive samples exceed the **1 kB/s (8 kbit/s) alpha floor for their stated path**. The installed-wheel run adds a real installation and verified-piece continuation check. These finite files do not establish a sustained-throughput or completion distribution or a guarantee on another installation. The selected SDK/source-build artifacts are published as the [`v1.0.0-alpha` prerelease](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha) from source `673eb60`. Native/private integration, wider recovery and security qualification remain separate programme work.

Measured RF airtime, energy, EIRP and field range remain unavailable. GFSK results do not replace the [earlier LoRa measurements and scoped upstream comparisons](PERFORMANCE.md). No general speed or security ranking against MeshCore or Reticulum follows from these samples.

## Inspect the published record

[Sanitized results and source-capture hashes](../benchmarks/2026-10-17-public-sdk/results.json) contain the measured fields needed to check the arithmetic. The operator captures stay in the lab; hashes and an allowlisted transcription do not independently attest the hardware. The public record excludes grants, addresses, device identifiers, boot values and operational logs.

```sh
python tools/verify_benchmark_report.py benchmarks/2026-10-17-public-sdk/results.json
python tools/verify_benchmark_report.py provenance/PUBLIC_INSTALLED_SDK_PROOF.json
```

See the [roadmap](ROADMAP.md) for the next work and [release readiness](RELEASE_READINESS.md) for the remaining gates.
