# 1,000,000-byte public bundle, sender pinned away from the loaded CPU

One fresh whole-file transfer repeated the 1 MB geometry from
[the earlier record](../2026-10-18-public-sdk-large-files/results.json) on the
same source, firmware and hardware. The only deliberate change was the
benchmark sender's CPU placement: it was pinned away from the core the
production LLM engine was holding at roughly 95% CPU during the earlier sample.

The receiver independently verified all 42 signed parts, the publisher and the
complete 1,000,000-byte file (SHA-256
`16226b690f14d6b338d899f8e538907a3030b9d0b0de4ef2abb0d07e14fcba58`). All 84
endpoint processes exited zero without forced cleanup, and all 42
authenticated receipt confirmations were accepted.

| Whole-file boundary | Elapsed | Useful rate |
| --- | ---: | ---: |
| First sender startup → final authenticated receipt | 448.473933 s | **2,229.8 B/s (2.2298 kB/s; 17.838 kbit/s)** |
| First DATA-worker call → final authenticated receipt | 447.266757 s | 2,235.8 B/s (17.886 kbit/s) |
| Scope issue → both endpoints closed | 450.903081 s | **2,217.8 B/s (2.2178 kB/s; 17.742 kbit/s)** |

The run used 1,471 receiver attempts and 5,400 sender attempts, with two DATA
retransmissions and no fenced or unknown attempts. The MCU advanced from 4,584
to 6,055 attempts and from 14,019,936 to 18,021,056 reserved-airtime
microseconds; the raw receive-drop counter advanced from five to seven.
Firmware, boot identity and USB identity were unchanged.

## Why this number differs from the earlier 1,749.8 B/s sample

The earlier 1 MB sample summed 42 part intervals and carried about 110.8 s of
coordination gaps inside a 571.5 s whole-file window. This repeat sums to
363.706 s of part intervals and 87.197 s of gaps inside a 450.9 s window, so
both the per-part time (8.66 s versus 10.97 s) and the orchestration overhead
fell. That points at host contention and per-part process startup, not at a
change in the radio payload interval.

Two finite samples on one near-bench link are not a sustained-throughput
distribution. Measured RF airtime, energy, EIRP and range remain unavailable.
