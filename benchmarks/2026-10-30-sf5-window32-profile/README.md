# Logical-window32 hardware sample

The same 23,900 useful file bytes as the [R11 sample](../2026-10-30-sf5-r11/README.md)
verified in **18.716954 seconds including sender process startup through
authenticated receiver receipt: 1,276.917174 useful bytes/s**. The explicit v3
policy raised host logical credit/reassembly from eight to 32 frames, with a
6,656-byte owner window. Physical queue depth, train length and ACK coalescing
remained eight; firmware, waveform, listening guards and original budgets were
unchanged. Signing, session and grant identities were fresh.

Both samples used 10 DATA retries, but their positions and ACK losses differed.
This observation is 21.23% faster than the earlier 1,053.28 B/s result; it does
not isolate a causal gain or establish repeated capacity. It misses both the
3,000 B/s goal and strictly-above-2,800 B/s checkpoint.

| Observation | Result |
| --- | ---: |
| Useful file / authenticated response | 23,900 / 24,565 bytes |
| Logical DATA / DATA attempts / retries | 128 / 138 / 10 |
| Selective fast repairs | 9 |
| ACK attempts / authenticated ACK receipts | 32 / 26 |
| Process startup | 414,879 µs |
| Part invocation to first DATA submission | 579,573 µs |
| Physical DATA outstanding interval union | 8,014,859 µs |
| Remaining time without physical DATA outstanding | 9,707,643 µs |

The four time rows sum to the goal interval. Time without physical DATA can
still contain logical unacknowledged work. The shorter part interval excludes
startup and is not the goal boundary. Native timing differences retain only
counts and totals; cumulative extrema cannot be subtracted. Inclusive timing
phases overlap.

Publisher, signed-part and whole-file verification succeeded. Both endpoints
drained and closed, all paid attempts reconciled with no UNKNOWN or fences,
and protected service lifetimes were unchanged. The original scope is closed
and expired. Receive-drop diagnostics increased by 27 on the receiver and
four on the sender; their causes remain unknown. Earlier debt is retained.

The [sanitized record](RESULTS.json) pins source, wheel, firmware and private
evidence digests. Further work investigates per-repair control holds and the
remaining completion/retry delays. The [matched source model](../2026-10-30-sf5-window32-source/README.md)
remains separate from this physical result.
