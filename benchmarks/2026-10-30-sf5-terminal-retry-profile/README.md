# Terminal-retry policy and native RX diagnostics sample

One finite B → A transfer verified **23,900 useful file bytes in 11.793771
seconds from sender child-process startup through authenticated receipt:
2,026.493477 useful bytes/s**. Publisher, signed part and whole-file verification
passed. The original scope expired, both endpoints drained and closed, and
protected service lifetimes remained unchanged. This single sample remains
below the 2,800 B/s checkpoint and repeated 3,000 B/s goal.

The explicit v5 policy was enabled, with logical window 32 and physical train
eight. All six repairs occurred while fresh DATA remained and used the existing
three-receipt selective path, each followed by seven fresh DATA frames. The
new terminal threshold was not exercised. The observed rate is 65.661060%
higher than the previous repair-fill sample, with six DATA retries versus
thirteen and a different loss pattern. Diagnostic firmware also changed.
These samples do not isolate a causal policy gain.

| Observation | Value |
| --- | ---: |
| Useful / signed response bytes | 23,900 / 24,565 |
| Original DATA / total DATA attempts | 128 / 134 |
| Selective / ordinary retries | 6 / 0 |
| ACK attempts / authenticated ACK receipts | 20 / 18 |
| Receipt-confirmation CONTROL attempts | 1 |
| Process startup | 420,612 µs |
| Invocation to first DATA submit | 329,123 µs |
| Host-observed DATA queue-to-terminal envelope union | 7,626,313 µs |
| Remaining interval without DATA outstanding | 3,417,723 µs |

The last four rows are disjoint and total 11,793,771 µs. Physical terminal
delivery follows normal host settlement and release; the envelope is not RF
airtime. The per-part invocation timing excludes process startup and is not
the acceptance metric. Inclusive host/native phases overlap.

Both Heltec V4 units ran diagnostic firmware `mtb1-e18a633806`, with the
unchanged 2 MHz RadioLib SPI setting. The radio profile remained SF5/BW500,
CR4/5, preamble 12, 921.375 MHz, sync word `0x42`, requested chip power 0 dBm.
The 5 ms inter-packet receive and 100 ms reverse-listen floors remained intact.

The new MNR1 counters reported 20 receiver and three sender drops, all in
`IRQ_RESTART_HEADER_ONLY`; every other drop category was zero. Exports were
129 at the receiver and 18 at the sender, matching authenticated DATA plus
confirmation and ACK counts respectively. These software categories cannot
identify the six missing DATA packets or two missing ACKs, or establish their
RF cause. No output-capacity or payload-CRC drop was recorded.

Exact inputs: SDK `f168a6a66a15054e909e3d9555ea6e5089c0b14e`, wheel
`13d722a70affe4177b4a8e30be46b455d9820d63d7981ed70452ba1bd5dbd3d3`.
[RESULTS.json](RESULTS.json) retains sanitized firmware/build pins, counters,
timing phases and verification limits. The genuine firmware upgrade retained
all earlier paid/UNKNOWN history; it granted no RF authority or refund.
Historical completeness remains false and unknown actual cost remains null.

This profiled sample does not establish sustained capacity, range, a paired
four-frame comparison, upstream superiority, exact airtime or energy. Alpha.3
release assets remain unchanged. See [terminal recovery](../../docs/TERMINAL_RETRY.md)
and the [throughput work](../../docs/THROUGHPUT_PIPELINE.md).
