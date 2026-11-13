# Train12 / train24 source model

This matched source experiment exercises the same signed 23,900 useful bytes
and 24,565-byte encoded response (128 DATA frames), using modeled SF5/BW500,
32-symbol preamble and sync word 18. Both policies use eight native slots,
the same attempt caps, 2s autonomous hold, 5ms receive gaps and 100ms reverse
floors. It does not measure physical throughput or process startup.

| Case | Train | Modeled duration | ACK emissions | Sender / receiver paid attempts |
| --- | ---: | ---: | ---: | ---: |
| Clean | 12 | 6.343599s | 12 | 129 / 12 |
| Clean | 24 | 5.868599s | 7 | 129 / 7 |
| Final ACK lost | 12 | 6.763200s | 13 | 129 / 13 |
| Final ACK lost | 24 | 6.288200s | 8 | 129 / 8 |
| Slow host | 12 | 8.388613s | 12 | 129 / 12 |
| Slow host | 24 | 7.913613s | 7 | 129 / 7 |

Every case completed, verified the exact encoded response and decoded chunk,
and retained the fixed operation caps. The observed scheduling delta is
475ms and five fewer ACK emissions in each matched case. Slow-host sensitivity
adds 40ms of modeled sender seal work and 10ms of receiver seal work.

The duration runs from modeled discovery/ARM through receipt confirmation and
cleanup. It excludes actual process startup and unmodeled CPU, storage,
IRQ/header detection and driver costs. This is neither the intended physical
startup-to-authenticated-receipt metric nor evidence of 4,000 useful B/s.
The source tests separately exercise short-train BUDGET refusal and paid drain;
these six successful modeled runs do not prove every allowed timing will fit.

Reproduce from the repository root:

```sh
PYTHONPATH=src python tools/benchmarks/compare_sf5_train24.py --repo . --output /tmp/train24-source-results.json
```

`RESULTS.json` retains the inputs, exact source hashes, model assumptions,
endpoint counts and errors. See [the admission design](../../docs/TRAIN24_SOURCE_EXPERIMENT.md)
for the v10 policy and remaining hardware qualification.
