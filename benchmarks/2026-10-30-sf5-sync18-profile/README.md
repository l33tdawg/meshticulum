# Sync18 and preamble32: first SF5 sample above 2.8 kB/s

One finite B → A transfer verified **23,900 useful file bytes in 8.321713
seconds from sender child-process startup through authenticated receipt:
2,872.004838 useful bytes/s**. Publisher, signed part and whole-file verification
passed, with zero DATA retries. Both endpoints completed and closed, queues
drained, and tracked service lifetimes remained unchanged. Both original
controller deadlines expired before reconciliation; both postflight MCU clock
samples showed unarmed radios after their original remote deadlines.

This is the first completed sample above the strict **2,800 B/s checkpoint**
in this campaign. It is below 3,000 B/s, and one run does not establish repeated
capacity. For this useful file size, 3,000 B/s requires at most 7.966667 seconds;
the measured run is about 355 ms away.

The run kept the SDK/v7 policy, firmware, both MCU boots, eight physical slots,
eight-frame trains, logical window 32 and role budgets from the
[failed preamble32 trial](../2026-10-30-sf5-preamble32-failure/README.md). The
private harness explicitly selected sync word `0x12` instead of `0x42`.
Packs, signatures, sessions and grants were fresh, and all earlier debt stayed
retained. No firmware upgrade or reset occurred. The successful run does not
prove that the sync word caused the preceding receive failure. That failed
trial has no completed goodput value, so no percentage gain is reported.

| Observation | Value |
| --- | ---: |
| Useful / signed response bytes | 23,900 / 24,565 |
| Original / total DATA attempts | 128 / 128 |
| Fast / ordinary retries | 0 / 0 |
| Receiver ACK attempts | 17 |
| Sender receipt-confirmation CONTROL attempts | 1 |
| Process startup | 578,733 µs |
| Invocation to first DATA submit | 272,378 µs |
| Host-observed DATA queue-to-terminal envelope union | 6,595,994 µs |
| Remaining interval without DATA outstanding | 874,608 µs |

The last four rows are disjoint and total **8,321,713 µs** on the sender's
clock. The retained per-part interval is 7.742980 seconds and excludes
578,733 µs of process startup, despite its timing field's `sender_startup`
name. It is not the denominator for this goal. DATA envelopes are not exact RF
airtime; inclusive host and native phase totals overlap.

Both Heltec V4 units used `mtb1-845b22b090`, SF5/BW500, CR4/5, preamble 32,
921.375 MHz, sync word `0x12`, requested chip power 0 dBm and 8 MHz SPI.
The original two-second autonomous hold, USB pacing, 5 ms inter-packet receive
floor and 100 ms reverse-listening floor stayed unchanged. The exact SDK is
`7102952b847ec4cf4a534289bae3e22f6e03a93b`; wheel SHA-256 is
`c37038449b49ef09da99e8ae8ca56b3bf2fe166c372a39ac94ddab9148e79093`.
The private harness source is `5c07c0c10175e69db5f0c2130e58ea4198f3d9d6`.
The Heltec firmware is 404,496 bytes, SHA-256
`d98391bb4341aecda6b3e3f469875a3c396326afd283f4dce401e02cfa3c9b0b`.
Its inherited installation proof combines the pinned source SHA-256 with
ROM-verified programmed-span MD5, not a full new-image SHA-256 readback.

All **498 prior evidence references** and full role allocations remained
retained: receiver 64 attempts / 3,000,000 estimated µs; sender 160 /
8,000,000. This transfer charged receiver 17 attempts / 208,080 native estimated
µs and sender 129 / 4,593,680. Host journal reservations were 245,905 and
5,182,049 estimated µs respectively. Same-boot native cumulative totals reached
85 / 973,840 at the receiver and 422 / 14,663,200 at the sender. Prior-boot
debt and the failed predecessor's full claim also remain retained. Every new
attempt reconciled; UNKNOWN and refunds were zero. Historical completeness
remains false and unknown actual cost remains null.

Neither unit's native receive-drop total increased in this run. Software IRQ
categories and exported packets do not prove peer authentication or exact
RF loss. The header-only category can include selective clearing without an
RX restart. This near-bench sample does not qualify range, energy, exhaustive
runtime memory headroom or superiority over another implementation.

[RESULTS.json](RESULTS.json) retains the sanitized exact result, reconciliation,
firmware, timing and diagnostic pins. All earlier physical records and
published alpha.3 assets remain unchanged. The next source experiment uses
[twelve-frame MCU trains with eight queue slots](../../docs/MCU_TWELVE_TRAIN.md);
its modeled gain and local builds are separate from this measured v7 result.
