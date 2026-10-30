# Header preservation: one physical SF5 sample

One finite B → A transfer verified **23,900 useful file bytes in 14.138817
seconds from sender child-process startup through authenticated receipt:
1,690.381876 useful bytes/s**. Publisher, signed part and whole-file verification
passed. Both endpoints drained and closed, protected service lifetimes remained
unchanged, and both original scope clocks expired before reconciliation.

This sample followed a genuine firmware upgrade enabling the optional
[continuous-RX header-preservation path](../../docs/MCU_RX_HEADER_PRESERVE.md).
It kept the same v6 host runtime, 8 MHz SPI setting, useful input, waveform,
eight-frame physical train, logical window 32 and role budgets as the
[preceding scalar-fill pair sample](../2026-10-30-sf5-scalar-fill-pair-profile/README.md).
The upgrade created new MCU boots; all earlier debt remained retained. Packs,
signatures, sessions and finite grants were fresh.

The observed rate rose **38.284511%** from 1,222.394221 B/s. DATA retries fell
from 26 to 17, but the loss patterns differed. This pair does not isolate a
causal firmware benefit. The result is below the earlier
[2,026.493477 B/s sample](../2026-10-30-sf5-terminal-retry-profile/README.md),
the strict above-2,800 B/s checkpoint and the repeated 3,000 B/s goal.

| Observation | Value |
| --- | ---: |
| Useful / signed response bytes | 23,900 / 24,565 |
| Original / total DATA attempts | 128 / 145 |
| Fast / ordinary retries | 15 / 2 |
| Scalar extra offers | 18 |
| Repair-fill turns / timeout holds | 14 / 0 |
| ACK attempts / authenticated ACK receipts | 25 / 21 |
| Receipt-confirmation CONTROL attempts | 1 |
| Process startup | 430,709 µs |
| Invocation to first DATA submit | 268,619 µs |
| Host-observed DATA queue-to-terminal envelope union | 7,590,718 µs |
| Remaining interval without DATA outstanding | 5,848,771 µs |

The last four rows are disjoint and total 14,138,817 µs on the sender's clock.
The per-part invocation interval is 13.708108 seconds and excludes process
startup. Terminal delivery follows normal settlement and release; these DATA
envelopes are not RF airtime. Inclusive host and native phase totals overlap.
The interval without DATA outstanding is not all attributable to one timer
or removable delay.

Native diagnostic differences from the new firmware's initial postinstall
observations were 69 receiver drop-category increments and two sender increments.
The receiver recorded 66 `IRQ_RESTART_HEADER_ONLY`, two `READ_HEADER` and one
`INVALID_REPORTED_LENGTH`; the sender recorded two `IRQ_RESTART_HEADER_ONLY`.
All other drop categories were zero. The receiver exported 128 packets and the
sender 21; native export alone is not peer authentication.

On this firmware, the historical `IRQ_RESTART_HEADER_ONLY` category includes
selective header-error clearing that may preserve continuous RX. It **does not
prove an actual RX restart or packet drop**. The unchanged MNR1 schema does not
make actual restart counts comparable across firmware versions. These are
software classifications; they do not identify the cause of a missing
authenticated packet.

Both Heltec V4 units used `mtb1-845b22b090`, SF5/BW500, CR4/5,
preamble 12, 921.375 MHz, sync word `0x42` and requested chip power 0 dBm.
The original 2 s autonomous hold, USB pacing and 5/100 ms receive floors stayed
unchanged. The experiment changed receive handling; it did not enable the
subsequent R13/v7 repeat-repair source policy.

Exact host inputs: SDK `7ac7daf02629b5b4cdf23d1cfaadc0152c0ce1dc`, wheel
`0b2466dbb4a7004dd99f2608cba77b9a514292152815fc71747711b5f4909934`.
The Heltec firmware is 404,496 bytes, SHA-256
`d98391bb4341aecda6b3e3f469875a3c396326afd283f4dce401e02cfa3c9b0b`.
Its installation proof combines the pinned source SHA-256 with ROM-verified
programmed-span MD5, not a full new-image SHA-256 readback.
[RESULTS.json](RESULTS.json) retains the sanitized exact result, reconciliation,
firmware, timing and diagnostic pins. Published alpha.3 assets remain unchanged.

All 458 prior evidence references and full role allocations were retained.
This transfer charged 25 attempts / 274,000 native estimated µs at the receiver
and 146 / 5,017,440 at the sender. Host journal reservations were 326,425 and
5,665,185 estimated µs respectively. Prior-boot totals of 73 / 800,080 native
estimated µs at the receiver and 310 / 10,658,400 at the sender remain paid,
along with older history. Every new attempt reconciled; UNKNOWN and refunds
were zero. Historical completeness remains false and unknown actual cost
remains null.

One sample does not establish repeated capacity, range, energy, exact RF airtime,
upstream superiority or exhaustive runtime memory headroom. The prior seven
physical samples remain unchanged. See the
[firmware option and source controls](../../docs/MCU_RX_HEADER_PRESERVE.md) and
[throughput work](../../docs/THROUGHPUT_PIPELINE.md).
