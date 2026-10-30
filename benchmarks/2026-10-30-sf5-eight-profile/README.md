# First eight-frame SF5 hardware sample

One finite B→A transfer verified its publisher, signed part and whole-file hash
on firmware `mtb1-bc5858fa0b`, with host and native profiling enabled. It delivered
23,900 useful bytes in **29.821904 seconds from sender process launch through
authenticated receiver receipt: 801.424349 useful bytes/s**. This missed both
the 3,000 B/s goal and the strictly-above-2,800 B/s checkpoint.

The [sanitized record](RESULTS.json) pins the SDK, wheel, firmware, private
evidence digests and exact measurement boundaries. This is one sample, with no
matched four-frame run or sustained-capacity qualification.

| Observation | Result |
| --- | ---: |
| Useful file / authenticated transfer response | 23,900 / 24,565 bytes |
| Logical DATA frames / DATA attempts | 128 / 142 |
| DATA retries / receipt-confirmation CONTROL attempts | 14 / 1 |
| Receiver ACK attempts / sender ACK receipts | 41 / 39 |
| Process startup | 410,880 µs |
| Part invocation to first DATA submission | 324,584 µs |
| Union of intervals with physical DATA outstanding | 8,133,697 µs |
| Remaining time with no physical DATA outstanding | 20,952,743 µs |

The four time rows sum to the full 29,821,904 µs. Physical outstanding work is
distinct from logical unacknowledged data. The shorter part-invocation interval
of 29.411024 seconds excludes process startup and is not the goal boundary.

Fourteen DATA ciphertext hashes were absent from the receiver's authenticated
log; the successful fresh retries preserved nonce and replay rules. The logs
do not establish where or why those attempts were lost. Receiver receive-drop
diagnostics increased by 47 and sender diagnostics by one; these counters do
not identify RF loss causes. All journals reconciled without UNKNOWN or fences,
both endpoints drained and closed, and protected service lifetimes were unchanged.
The original scope is closed and expired; old boot debt remains retained.

Native timing is measured at software call boundaries. The sender's 142
ISSUED→STARTED records total 573,427 µs, or about 4.038 ms each; the nested driver
start call totals 472,332 µs. Its 143 TX_DONE→RX-return records total 192,938 µs,
or about 1.349 ms each. Parent and child phases overlap and must not be added.
These costs are much smaller than the time without physical DATA outstanding.
Three lost selective repairs fell back to roughly 4.7-second waits; source
review traced their timeout doubling to the same retry-settlement path used by
ordinary timeouts. A separately reviewed recovery change remains work in progress.

Both same-boot diagnostic snapshots passed the disarmed/drained gate. After
the run, internal free heap was 340,352 bytes on the receiver and 340,496 on the
sender; their largest free blocks were 303,092 bytes. Loop-stack minimum-free
values were 4,632 and 4,648 bytes. These are observed margins for this workload,
not exhaustive worst-case memory qualification. Region minimum-free sums are
not simultaneous global minima. Exact RF airtime, energy and EIRP remain
unmeasured.
