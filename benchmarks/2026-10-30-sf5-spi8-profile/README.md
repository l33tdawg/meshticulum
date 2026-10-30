# Heltec 8 MHz SPI comparison sample

One finite B → A transfer verified **23,900 useful file bytes in 23.765471
seconds from sender child-process startup through authenticated receipt:
1,005.660692 useful bytes/s**. Publisher, signed part and whole-file verification
passed. Both endpoints drained and closed, protected services remained unchanged,
and both original scope clocks expired before reconciliation.

The same SDK, wheel, v5 recovery policy, useful file, LoRa waveform and role
budgets were retained from the [2 MHz sample](../2026-10-30-sf5-terminal-retry-profile/README.md).
A genuine firmware upgrade selected 8 MHz for Heltec's radio SPI bus.
The observed file rate fell 50.374344%, with 26 DATA retries versus six.
Loss patterns were uncontrolled; this pair does not isolate a causal SPI effect.
The single sample misses the strict above-2,800 B/s checkpoint and the repeated
3,000 B/s goal.

| Observation | Value |
| --- | ---: |
| Useful / signed response bytes | 23,900 / 24,565 |
| Original / total DATA attempts | 128 / 154 |
| Fast / ordinary retries | 19 / 7 |
| Repair-fill turns | 16 |
| ACK attempts / authenticated ACK receipts | 38 / 31 |
| Receipt-confirmation CONTROL attempts | 1 |
| Process startup | 401,009 µs |
| Invocation to first DATA submit | 320,728 µs |
| Host-observed DATA queue-to-terminal envelope union | 9,026,268 µs |
| Remaining interval without DATA outstanding | 14,017,466 µs |

The last four rows are disjoint and total 23,765,471 µs. The per-part invocation
timing excludes process startup. Host terminal delivery follows normal settlement
and release, so DATA envelopes are not RF airtime. Inclusive phase totals overlap.

The sender's mean native `ISSUE_TX_START_CALL` fell from 3,366.828 to 2,237.156 µs;
mean `ISSUED_TO_STARTED` fell from 4,097.724 to 2,918.058 µs. These are individual
software boundaries, with different call counts and loss patterns. Faster
transmit setup did not establish higher useful-file goodput in this sample.

Receiver diagnostics recorded 61 `IRQ_RESTART_HEADER_ONLY` events and seven
`READ_HEADER` failures. The sender recorded six header-only restarts. Other drop
categories and all native phase failure counts were zero. These categories do
not identify particular missing packets or establish their RF cause.

Both Heltec V4 units ran `mtb1-d6286a78d3`. The waveform remained SF5/BW500,
CR4/5, preamble 12, 921.375 MHz, sync word `0x42`, requested chip power 0 dBm.
Physical train eight, logical window 32, USB pacing and the 5/100 ms receive
floors were unchanged. XIAO's separate target build retains 2 MHz and was not
installed for this experiment.

Exact host inputs: SDK `f168a6a66a15054e909e3d9555ea6e5089c0b14e`, wheel
`13d722a70affe4177b4a8e30be46b455d9820d63d7981ed70452ba1bd5dbd3d3`.
The firmware build originated at source
`3988fce58a64172f335ea68c06ee95075a8fe43a`; later documentation commits do not
relabel that build. [RESULTS.json](RESULTS.json) retains the sanitized exact
build, result, timing and diagnostic pins.

All 330 prior evidence references and full role allocations were retained.
Native charged totals reached 38 attempts / 416,480 estimated µs at the receiver
and 155 / 5,329,200 at the sender. Every attempt reconciled; UNKNOWN and refunds
were zero. Older boot debt remains retained, historical completeness remains
false, and unknown actual cost remains null. The upgrade supplied no RF grant.

This record does not establish repeated capacity, range, energy, exact airtime,
upstream superiority or exhaustive runtime memory headroom. Alpha.3 assets remain
unchanged. See the [SPI recipe experiment](../../docs/MCU_SPI_EXPERIMENT.md),
[separate v6 refill sample](../2026-10-30-sf5-scalar-fill-pair-profile/README.md) and
[throughput work](../../docs/THROUGHPUT_PIPELINE.md).
