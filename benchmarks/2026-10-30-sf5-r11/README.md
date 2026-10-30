# Matched R11 selective-repair hardware sample

The same 23,900 useful file bytes as the [first eight-frame sample](../2026-10-30-sf5-eight-profile/README.md)
completed in **22.690930 seconds from sender process startup through authenticated
receiver receipt: 1,053.284286 useful bytes/s**. The publisher, signed part and
whole-file hash verified. The firmware, SF5/BW500 waveform, eight-frame policy
and original role budgets were unchanged; the pack signature, sessions and
grants were fresh. The SDK changed to the reviewed R11 retry-settlement revision.

This sample is 31.43% faster than the earlier 801.42 B/s observation. Loss also
changed from 14 to 10 DATA retries, so the pair does not isolate the retry fix's
causal gain. It misses both 3,000 B/s and the strictly-above-2,800 B/s checkpoint.
It does not establish repeatable capacity or a gain over a four-frame train.

| Observation | Result |
| --- | ---: |
| Useful file / authenticated response | 23,900 / 24,565 bytes |
| Logical DATA / DATA attempts / retries | 128 / 138 / 10 |
| Selective fast repairs | 6 |
| ACK attempts / authenticated ACK receipts | 34 / 32 |
| Process startup | 410,066 µs |
| Part invocation to first DATA submission | 226,719 µs |
| Physical DATA outstanding interval union | 7,928,237 µs |
| Remaining time without physical DATA outstanding | 14,125,908 µs |

The four time rows sum to the goal interval. Logical unacknowledged data can
remain during time without physical DATA outstanding. The shorter part interval
of 22.280864 seconds excludes startup and is not the goal boundary.

Both endpoints drained and closed with no UNKNOWN or accounting fences, and
protected service lifetimes were unchanged. The original scope is closed and
expired. All earlier debt remains retained. Native receive-drop counters rose
by 28 on the receiver and four on the sender; their mixed causes remain unknown.

Same-boot native timing differences give 138 sender ISSUED→STARTED calls totaling
560,270 µs, about 4.060 ms each. The nested TX-start calls total 460,759 µs;
139 TX_DONE→RX-return calls total 188,966 µs, about 1.359 ms each. Inclusive
parent and child phases overlap. Only counts and totals are subtracted from the
cumulative snapshots; per-run extrema cannot be recovered that way. Memory
observations remain margins for this workload, not exhaustive qualification.

The [sanitized record](RESULTS.json) retains exact source, artifact and evidence
digests. Further work separates logical receive credit from physical train
size, then tests the remaining recovery delays with fresh finite operations.
