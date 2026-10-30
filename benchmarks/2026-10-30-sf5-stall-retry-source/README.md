# SF5 stalled-window repair source model

This paired source model uses the same signed 23,900-byte response geometry and
the real flow, owner, AEAD, durable journal, serial worker, eight-slot native
queue, authority and scheduler paths. It changes only the explicit
`selective_stall_retry` arm.

Seven consecutive holes:

- baseline: **8.267515 s**
- stalled-window repair: **6.998415 s**
- reduction: **1.269100 s / 15.35%**
- two pre-existing ACK/DATA model collisions remain in both arms

Nine consecutive holes:

- baseline: **8.310935 s**
- stalled-window repair: **7.231835 s**
- reduction: **1.079100 s / 12.98%**
- zero modeled collisions

Both pairs complete with identical attempt counts; the arm does not hide or
refund retransmission debt. Native receive priority, process startup, host CPU
and physical radio behavior remain modeled or excluded. These are not physical
goodput measurements.

Reproduce:

```sh
python tools/benchmarks/compare_sf5_stall_retry.py \
  --repo . \
  --output /tmp/sf5-stall-retry.json
```

The complete machine-readable result, source pins and limitations are in
[`RESULTS.json`](RESULTS.json).
