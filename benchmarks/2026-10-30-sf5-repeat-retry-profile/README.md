# Repeat selective repair: one physical SF5 sample

One finite B → A transfer verified **23,900 useful file bytes in 15.147523
seconds from sender child-process startup through authenticated receipt:
1,577.815726 useful bytes/s**. Publisher, signed part and whole-file verification
passed. Both endpoints drained and closed, protected service lifetimes remained
unchanged, and both original scope clocks expired before reconciliation.

This sample enabled the accepted [R13/v7 repeat selective repair](../../docs/FLOW_R13_REPEAT_RETRY_REVIEW.md)
policy. It kept the same firmware, MCU boots, 8 MHz SPI, useful input, waveform,
eight-frame physical train, logical window 32 and role budgets as the
[preceding header-preservation sample](../2026-10-30-sf5-header-preserve-profile/README.md).
Packs, signatures, sessions and finite grants were fresh; all earlier debt stayed
paid. No firmware upgrade or reset occurred between these two samples.

The observed rate fell **6.659214%** from 1,690.381876 B/s. DATA retries rose
from 17 to 18, and all 18 used selective recovery. The differing loss and
recovery patterns prevent an isolated estimate of the policy's effect. The
result remains below the earlier 2,026.493477 B/s sample, the strict above-2,800
B/s checkpoint and the repeated 3,000 B/s goal. Model gains remain separate
from physical evidence.

| Observation | Value |
| --- | ---: |
| Useful / signed response bytes | 23,900 / 24,565 |
| Original / total DATA attempts | 128 / 146 |
| Fast / ordinary retries | 18 / 0 |
| Accepted retry proof epochs | 18 |
| Scalar extra offers | 18 |
| Repair-fill turns / timeout holds | 12 / 0 |
| ACK attempts / authenticated ACK receipts | 27 / 25 |
| Receipt-confirmation CONTROL attempts | 1 |
| Process startup | 415,024 µs |
| Invocation to first DATA submit | 897,566 µs |
| Host-observed DATA queue-to-terminal envelope union | 7,832,367 µs |
| Remaining interval without DATA outstanding | 6,002,566 µs |

The last four rows are disjoint and total 15,147,523 µs on the sender's clock.
The per-part invocation interval is 14.732499 seconds and excludes process
startup, despite its retained timing field's `sender_startup` name. The strict
boundary uses the outer endpoint's child-start observation and the final
authenticated receipt. DATA envelopes are not RF airtime; inclusive host and
native phase totals overlap. Neither an epoch count nor a selective retry
count proves that a previously repaired hole needed another early retry.

Native receive-reason differences were 71 receiver classifications: 68
`IRQ_RESTART_HEADER_ONLY` and three `READ_HEADER`. The sender recorded three
header-only classifications. All other drop categories were zero. The
receiver exported 129 packets, including receipt-confirmation CONTROL; the
sender exported 25. These software categories do not identify missing-packet
causes. In particular, header-only handling may retain continuous RX and
does not prove an actual restart or packet drop. Native export is not peer
authentication.

Both Heltec V4 units used `mtb1-845b22b090`, SF5/BW500, CR4/5, preamble 12,
921.375 MHz, sync word `0x42` and requested chip power 0 dBm. The original
2 s autonomous hold, USB pacing and 5/100 ms receive floors stayed unchanged.
The exact SDK is `7102952b847ec4cf4a534289bae3e22f6e03a93b`; its wheel SHA-256
is `c37038449b49ef09da99e8ae8ca56b3bf2fe166c372a39ac94ddab9148e79093`.
The Heltec firmware is 404,496 bytes, SHA-256
`d98391bb4341aecda6b3e3f469875a3c396326afd283f4dce401e02cfa3c9b0b`.
Its inherited installation proof combines the pinned source SHA-256 with
ROM-verified programmed-span MD5, not a full new-image SHA-256 readback.

All 470 prior evidence references and full role allocations were retained.
This transfer charged 27 attempts / 295,920 native estimated µs at the receiver
and 147 / 5,052,080 at the sender. Host journal reservations were 352,539 and
5,704,289 estimated µs respectively. Same-boot cumulative totals reached
52 / 569,920 at the receiver and 293 / 10,069,520 at the sender. Prior-boot
debt also remains retained. Every new attempt reconciled; UNKNOWN and refunds
were zero. Historical completeness remains false and unknown actual cost
remains null.

[RESULTS.json](RESULTS.json) retains the sanitized exact result, reconciliation,
firmware, timing and diagnostic pins. All eight earlier physical records and
published alpha.3 assets remain unchanged. One sample does not establish
repeated capacity, range, energy, exact RF airtime, upstream superiority or
exhaustive runtime memory headroom. Further work addresses recovery turns and
receive reliability while keeping the original finite accounting boundaries.
