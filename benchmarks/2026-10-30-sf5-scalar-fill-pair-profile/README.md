# Scalar fill pair: same-boot SPI8 sample

One finite B → A transfer verified **23,900 useful file bytes in 19.551794
seconds from sender child-process startup through authenticated receipt:
1,222.394221 useful bytes/s**. Publisher, signed part and whole-file verification
passed. Both endpoints drained and closed, protected service lifetimes remained
unchanged, and both original scope clocks expired before reconciliation.

This sample enabled the explicit v6 `scalar_fill_pair` host policy. It retained
the same MCU firmware and boots, 8 MHz SPI setting, useful input, waveform,
eight-frame physical train, logical window 32 and role budgets as the
[preceding v5 SPI8 sample](../2026-10-30-sf5-spi8-profile/README.md). There was
no firmware upgrade or reset between these two transfers. Packs, signatures,
sessions and finite grants were fresh.

The observed rate rose 21.551357% from 1,005.660692 B/s. Both samples used
26 DATA retries, but their loss and recovery patterns differed. This pair does
not isolate a causal gain from v6. It misses the strict above-2,800 B/s checkpoint
and the repeated 3,000 B/s goal.

| Observation | Value |
| --- | ---: |
| Useful / signed response bytes | 23,900 / 24,565 |
| Original / total DATA attempts | 128 / 154 |
| Fast / ordinary retries | 18 / 8 |
| Scalar extra offers | 17 |
| Repair-fill turns / timeout holds | 10 / 2 |
| ACK attempts / authenticated ACK receipts | 35 / 32 |
| Receipt-confirmation CONTROL attempts | 1 |
| Process startup | 431,973 µs |
| Invocation to first DATA submit | 639,332 µs |
| Host-observed DATA queue-to-terminal envelope union | 8,133,556 µs |
| Remaining interval without DATA outstanding | 10,346,933 µs |

The last four rows are disjoint and total 19,551,794 µs on the sender's clock.
The per-part invocation interval is 19.119821 seconds and excludes process
startup. Terminal delivery follows normal settlement and release; these DATA
envelopes are not RF airtime. Inclusive host and native phase totals overlap.
The 17 extra offers show that the new branch ran, not how much time it saved.

Native diagnostics are differences from the preceding v5 postflight on each
unchanged MCU boot. Receiver drop counters increased by 85 and sender counters
by four, all in `IRQ_RESTART_HEADER_ONLY`; other drop categories were zero.
These software categories do not identify individual missing packets or their
RF cause. Sender mean `ISSUE_TX_START_CALL` was 2,206.649 µs across 154 calls;
mean `ISSUED_TO_STARTED` was 2,868.636 µs. The 10.346933 seconds without DATA
outstanding is not all attributable to one timer or removable delay.

Both Heltec V4 units retained `mtb1-d6286a78d3`, SF5/BW500, CR4/5,
preamble 12, 921.375 MHz, sync word `0x42` and requested chip power 0 dBm.
The original 2 s autonomous hold, USB pacing and 5/100 ms receive floors stayed
unchanged. This was a host-policy experiment, not an eight-frame native batch.

Exact host inputs: SDK `7ac7daf02629b5b4cdf23d1cfaadc0152c0ce1dc`, wheel
`0b2466dbb4a7004dd99f2608cba77b9a514292152815fc71747711b5f4909934`.
[RESULTS.json](RESULTS.json) retains the sanitized exact result, reconciliation,
firmware, timing and diagnostic pins. These are candidate observations; the
published alpha.3 assets remain unchanged.

All 364 prior evidence references and full role allocations were retained.
This transfer charged 35 attempts / 383,600 native estimated µs at the receiver
and 155 / 5,329,200 at the sender. The earlier same-boot totals of 38 / 416,480
and 155 / 5,329,200 respectively remain paid. Older pre-SPI8 boot debt also
remains retained. Every new attempt reconciled; UNKNOWN and refunds were zero.
Historical completeness remains false and unknown actual cost remains null.

One sample does not establish repeated capacity, range, energy, exact RF airtime,
upstream superiority or exhaustive runtime memory headroom. See the
[policy and source controls](../../docs/SCALAR_FILL_PAIR.md),
[separate source model](../2026-10-30-sf5-scalar-fill-pair-source/README.md) and
[throughput work](../../docs/THROUGHPUT_PIPELINE.md).
