The 30 October 2026 finite bundle-handoff trial failed during its first part. It planned 50,000 useful bytes in five 10,000-byte parts, ordered **4, 2, 0, 3, 1**, from unit A to unit B. Only part 4 started; no part completed and no authenticated part receipt or verified whole file was obtained. There is no handoff-latency or useful-goodput result.

The candidate was `541a3796454291b1d8cb573b5e5e60dd4fc4d1ac`, wheel SHA-256 `cab6694a2004916af169b929b55bc7e3acd5c0971f8af1210428ffbabf346f1d`. Both endpoints verified 72 installed runtime files. The queued run used SF5, 500 kHz bandwidth, CR4/5, preamble 12, 921.375 MHz, requested chip power 0 dBm, a 60-second radio lease, and one original 180-second file scope. Host profiling was enabled. Firmware remained `mtb1-6cfde1fdd4`.

Read-only reconciliation resolves the paid debt that the original failure summary could not report:

| Endpoint | Paid journal attempts | Journal reserved estimate | Native attempt counter | Native estimate counter | Receive-drop delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| A, sender | 34 RF_DONE | 1,329,536 µs | 300 → 334 | 9,619,200 → 10,796,960 µs | +2 |
| B, receiver | 28 RF_DONE | 365,596 µs | 871 → 899 | 85,947,584 → 86,254,464 µs | +7 |

Both part-4 journals are unfenced, with no RESERVED or UNKNOWN rows. A's retained physical trace contains all 34 ordered submit/QUEUED/ISSUED/STARTED/RF_DONE/release lifecycles, and its summed driver estimate matches the native increase of 1,177,760 µs. Its physical and worker pending counts were zero at cleanup. The sender flow recorded five retransmits, 25 received ACK frames and 28 acknowledged frames; it did not complete.

The receiver reached its 28-attempt per-part cap. The source plan counted 56 DATA frames for part 4 but allowed 28 receiver ACK attempts. Exhaustion of that allowance is consistent with the stalled transfer. The retained record identifies the sender failure only as a WireError inside an exception group, and receiver flow diagnostics are unavailable, so a sole cause is not established. The receiver's actual host reservation is 28 µs above the integer cold estimate of 365,568 µs because the deployed floating-point `ceil(estimate * 1.10)` calculation adds one extra microsecond for each of these ACK reservations; the journal's actual value is used above.

The sender child exited with status 1. The controller then stopped its peer; B's supervisor recorded management-stdin EOF, forced cleanup and a terminal child with status 1. Both final bound STATUS gates passed, requiring disarmed, non-transmitting and unfenced state. The tracked service states, service lifetimes and host boots remained unchanged: 20 services on A and 18 on B. This does not claim global system quiescence.

The full allocation remains reserved for all five planned parts: **A, 420 attempts / 20,000,000 µs; B, 140 attempts / 5,000,000 µs**. Refunds are zero. Native boot counters, paid host reservations and scope allocations remain separate. The failed first part and four unstarted parts do not replenish any budget, erase historical debt or authorize an automatic retry.

Initial startup observations are retained: A's child launch to Python process start was 30,898 µs; child launch to first part invocation was 420,445 µs; first part invocation to first queued DATA submission was 704,599 µs. That invocation returned failure after 42,086,329 µs. These are host timings, not a delivery rate. All 24 sender timing phases were present with no invalid, dropped or saturated samples. Receiver flow and profile values remain null. Receipt-to-next-invocation and receipt-to-next-DATA intervals are also null because no handoff occurred.

Direction, waveform, workload and process/ownership lifecycle changed from the earlier SF7 qualification. This failed trial supports no matched speed, sustained throughput or range claim. Receive-drop increments are device diagnostics, not a measured application packet-loss rate.

The original RESULT.json is unchanged (SHA-256 `bf50310b3cd43af48a15a1f9ab2ce12dc07789a8b44e368b3685dd7f16b15a11`). The separate [failure summary](FAILURE_SUMMARY.json) records the endpoint and read-only SQL evidence hashes. Reconciliation added no grants and opened no radio interface. It resolves the missing native/journal observations without rewriting the original failure.
