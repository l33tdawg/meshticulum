# Scalar fill pair source comparison — 30 October 2026

This compares the explicit v5 terminal-retry policy with v6 scalar fill pair.
Both retain the same logical window 32, physical train 8, native guards, signed
23,900-byte useful input, 24,565-byte response, and original role caps. V6 allows
one additional fresh scalar DATA offer at low water under the conditions in
[the policy note](../../docs/SCALAR_FILL_PAIR.md). It adds no native batch command.

[RESULTS.json](RESULTS.json) retains all 32 rows and the exact source hashes.
The source files remained unchanged during the run. The checkout was modified
relative to its recorded base commit; the per-file hashes identify the tested
candidate. The [helper](../../tools/benchmarks/compare_sf5_scalar_fill_pair.py)
uses real Flow, owner, AEAD and durable journals with modeled radios. Its clock
includes modeled discovery, ARM, receipt confirmation and cleanup, but excludes
real process startup, CPU and fsync time. It does not execute the complete bundle
quarantine/publication application or qualify physical useful-file throughput.

Every row completed with exact response/chunk verification, empty retained
physical state, disarmed radios, no cleanup error, no fence/UNKNOWN and no modeled
collision. The maximum physical occupancy stayed at eight. Sender caps stayed
160 attempts/8,000,000 estimated airtime microseconds and receiver caps stayed
64 attempts/3,000,000 microseconds; every attempted frame remained charged.

| Source case | V5 modeled seconds | V6 modeled seconds |
| --- | ---: | ---: |
| Clean | 6.652215 | 6.652215 |
| Six DATA losses from run04 logical sequence/attempt counts | 8.051015 | 8.051015 |
| Six losses plus conditional ACK loss | 8.971600 | 8.971600 |
| Original DATA and its first repair lost | 8.366095 | 8.366095 |
| Two holes | 7.004555 | 7.004555 |
| Terminal holes 123, 124, 126 | 7.590915 | 7.590915 |
| Last DATA 127 lost | 9.808695 | 9.808695 |
| First final ACK lost | 7.069240 | 7.069240 |
| Per-seal host delay 40/10 ms | 8.316680 | 8.316680 |
| Per-seal host delay 120/40 ms | 18.987940 | 18.981540 |
| Per-step host delay 18/3 ms | 6.665780 | 6.665780 |
| Per-step host delay 40/10 ms | 8.302340 | 8.198980 |
| Per-step host delay 120/40 ms | 17.758800 | 11.893520 |
| Six losses plus per-step delay 18/3 ms | 8.134900 | 8.147740 |
| Per-step delay 18/3 ms plus per-seal delay 10/1 ms | 6.771780 | 6.753620 |
| Six losses plus both preceding delays | 8.939780 | 8.874340 |

Delay pairs are sender/receiver values injected into the model. They are
sensitivity experiments, not calibrated measurements of either physical host.
The six-loss schedule preserves logical sequence/attempt counts; it does not
replay RF ciphertext or timing. ACK-loss predicates use cumulative bases rather
than exact snapshot revisions. Remaining predicates are visible in the JSON.

The unchanged clean cases and the 12.840 ms regression remain visible. These
results show that avoiding another host step can help when that step is costly;
they do not establish an RF improvement, remove the measured startup cost or
meet the 3,000 useful-file B/s goal. A separately authorized physical trial must
measure the original process-start-to-authenticated-receipt boundary.

Reproduce with the project's test dependencies:

```sh
PYTHONPATH=src python tools/benchmarks/compare_sf5_scalar_fill_pair.py \
  --repo . --output /tmp/scalar-fill-pair-results.json
```
