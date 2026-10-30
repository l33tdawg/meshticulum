# Selective-repair fill hardware sample

The same 23,900 useful file bytes as the [window32 sample](../2026-10-30-sf5-window32-profile/README.md)
verified in **19.537686 seconds including sender process startup through
authenticated receiver receipt: 1,223.276902 useful bytes/s**. The explicit v4
policy permits new DATA to fill the remaining positions of an eight-frame
train after one selective repair, subject to existing credit and admission.
Nine repair-fill turns were recorded. Logical window32, physical queue/train depth eight,
firmware, waveform, listening guards and original budgets were unchanged.
Signing, session and grant identities were fresh.

This sample used 13 DATA retries versus 10 in the earlier window32 sample;
the DATA and ACK loss patterns differed. Its observed rate was 4.20% lower
than the earlier 1,276.92 B/s result. This does not isolate the policy's causal
effect or establish repeated capacity. It misses both the 3,000 B/s goal and
strictly-above-2,800 B/s checkpoint.

| Observation | Result |
| --- | ---: |
| Useful file / authenticated response | 23,900 / 24,565 bytes |
| Logical DATA / DATA attempts / retries | 128 / 141 / 13 |
| Selective fast repairs / repair-fill turns | 9 / 9 |
| ACK attempts / authenticated ACK receipts | 28 / 26 |
| Process startup | 411,689 µs |
| Part invocation to first DATA submission | 589,907 µs |
| Host-observed DATA queue-to-terminal envelope union | 8,156,096 µs |
| Remaining time without a DATA envelope outstanding | 10,379,994 µs |

The four time rows sum to the goal interval. The shorter 19.125997-second part
interval excludes process startup and is not the goal boundary. Terminal
delivery is observed after the worker's normal release flush; the envelopes
are not measured RF airtime. Time without a physical DATA envelope can still
contain logical unacknowledged work. Native timing differences retain only
counts and totals; cumulative minima/maxima cannot be subtracted. Inclusive
timing phases overlap.

Publisher, signed-part and whole-file verification succeeded. Both endpoints
drained and closed, all paid attempts reconciled with no UNKNOWN or fences,
and protected service lifetimes were unchanged. The original scope is closed
and expired on both original controller clocks. Receive-drop diagnostics
increased by 32 on the receiver and zero on the sender; their causes remain
unknown. Earlier debt remains retained and historical unknown cost remains
unknown.

The [sanitized record](RESULTS.json) pins the actual source 79cbcd7 candidate,
wheel, firmware and retained evidence digests. Source-model results remain
separate from this physical sample. Further work examines the late repair
waits and active-pipeline costs before any new finite hardware test.
