# SF5 bundle handoff: 30 October 2026

The 30 October 2026 finite SF5 trial completed **5,000 useful bytes** from unit B to unit A in five 1,000-byte parts, ordered **4, 2, 0, 3, 1**. All five sender authenticated receipts and receiver receipt confirmations were recorded. The receiver verified the publisher and whole-file hash, matching the local 5,000-byte input: `a712713c8c65bd6fdadc4a0a6b95375fa78c6de780e125ae951ef9878f6ad572`.

The startup-inclusive sender interval was **37.339657 seconds**, from SDK child launch to the last authenticated part receipt. Useful goodput was **133.905890 B/s (0.133906 decimal kB/s)**. This denominator includes initial process startup and every handoff; final whole-file verification and endpoint cleanup are separate checks. The run had **eight sender retransmits** and native receive-drop counter increases of **A +22 / B +12**. It was not loss-free.

The four handoff intervals were measured entirely on the sender's monotonic clock:

| Transition | Receipt → next transfer invocation | Receipt → next queued DATA submission |
| --- | ---: | ---: |
| 4 → 2 | 176.867 ms | 380.730 ms |
| 2 → 0 | 206.971 ms | 514.156 ms |
| 0 → 3 | 308.283 ms | 522.236 ms |
| 3 → 1 | 303.802 ms | 960.125 ms |
| Total | **995.923 ms** | **2,377.247 ms** |
| Maximum | **308.283 ms** | **960.125 ms** |

The invocation-gap aggregate was below one second by only **4.077 ms**. Each stronger receipt-to-next-DATA interval was also below one second, with a maximum of 960.125 ms, but their **2.377247-second aggregate was not below one second**. These are observations from one finite run, not promised future bounds. Sender child launch to the first part invocation was 428.737 ms; that initial startup remains in the goodput denominator. Initial startup, the five part invocation-to-receipt intervals, and the four invocation gaps partition the 37.339657-second interval exactly.

Paid host reservations and native counters reconcile independently:

| Endpoint | Paid journal attempts | Host reserved estimate | Native attempt counter | Native estimate increase | Receive-drop delta |
| --- | ---: | ---: | ---: | ---: | ---: |
| A, receiver | 61 | 796,477 µs | 359 → 420 | 668,560 µs | +22 |
| B, sender | 58 | 2,072,684 µs | 923 → 981 | 1,831,520 µs | +12 |

The retained RF_DONE driver estimates sum exactly to each native estimate increase. All ten part journals reported no fence or recovered UNKNOWN. The sender's 53 queued DATA attempts have all 53 complete ordered physical lifecycles in 318 retained events; its remaining five attempts were receipt confirmations. Every sender part ended with physical and worker pending counts at zero. All 240 phase aggregates were present across both endpoints, with no invalid, dropped or saturated timing samples. Native receive-drop increments are separate device diagnostics, not an application packet-loss rate.

Both SDK children exited with status 0, without forced cleanup. The held hardware context closed before each endpoint reported final success. Both final bound STATUS gates passed, requiring disarmed, non-transmitting and unfenced state. Host boots, MCU identities, and tracked service states and lifetimes remained unchanged: 20 services on A and 18 on B. This does not claim global system quiescence.

The controller recorded **39.937573 seconds** from scope issuance to result assembly after both endpoint RPCs had joined. This is a controller wall-clock interval including management setup, postflight and cleanup; no timestamps from different hosts are subtracted, and it is not a measured RF duration.

The exact candidate was `541a3796454291b1d8cb573b5e5e60dd4fc4d1ac`, wheel SHA-256 `cab6694a2004916af169b929b55bc7e3acd5c0971f8af1210428ffbabf346f1d`. Both supervisor and child verified the same 72 installed runtime files. The frozen batch adapter was `70436b7d3e5cddd9cfddae3a7358a21d5a5f05360c81cb9888e70e49d60f8c45`; later diagnostic changes were not used for this run. Queued SF5 used 500 kHz bandwidth, CR4/5, preamble 12, 921.375 MHz, requested chip power 0 dBm, a 60-second radio lease and one original 180-second file scope. Host profiling was enabled.

The full scope allocations remain reserved: **A, 320 attempts / 5,000,000 µs; B, 100 attempts / 5,000,000 µs**. Refunds are zero. Earlier failed trials, their paid debt and their full allocations remain separate evidence; this success does not supersede them or authorize another RF operation.

This is one 5 kB SF5 B-to-A run. Its workload and lifecycle differ from the earlier larger-file qualification, so it establishes no paired speed comparison, 100 kB speed result, sustained-throughput claim or range claim. The [sanitized summary](FINAL_SUMMARY.json) contains the timing arithmetic, per-part counts and evidence hashes. The original RESULT.json is unchanged, with SHA-256 `8c611c0f06ecfe3cfd3d9fa49d199293347d2c732a249f672c26f0126c5241cb`.

The earlier [failed 50 kB trial](FAILED_50K.md) and [partial 5 kB trial](SMALL_SUMMARY.json) remain part of this record. The [original 50 kB cold plan](COLD_PLAN.md) is a source calculation, not the successful workload. The [Lantern harness](https://github.com/l33tdawg/sage-lantern/tree/main/firmware/meshticulum-bench/bundle_handoff) includes later diagnostic and reservation-rounding improvements; its current source is not claimed as the executed frozen adapter.
