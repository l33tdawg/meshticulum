# Selective repair followed by fresh DATA: source comparison

These 30 deterministic cases compare the v3 dedicated repair turn with explicit
v4 repair fill. Both use logical window 32, physical trains of at most eight,
the same signed 23,900 useful bytes / 24,565 transport bytes / 128 DATA frames,
and unchanged role caps: sender 160 attempts / 8,000,000 estimated µs; receiver
64 attempts / 3,000,000 estimated µs. No device is opened.

| Injected case | V3, modeled seconds | V4, modeled seconds |
| --- | ---: | ---: |
| Clean | 6.652215 | 6.652215 |
| Retained ten-DATA-loss schedule | 11.914255 | 9.649655 |
| Ten DATA losses plus conditional ACK losses | 12.921375 | 10.656775 |
| First DATA and early repair lost | 8.394735 | 8.366095 |
| Lost repair and two adjacent DATA | 9.020135 | 8.365575 |
| Two holes | 7.454475 | 7.004555 |
| Last DATA lost | 9.808695 | 9.808695 |
| 120 / 40 ms injected host work per sender / receiver seal | 18.987940 | 18.987940 |

The retained DATA schedule drops the first attempt at sequences 2, 13, 16, 45,
58, 75, 81 and 119, plus the first two attempts at sequence 27. Both policies
receive that identical logical-attempt rule. It does not reproduce the hardware
run's timing, radio losses or ciphertext. The additional ACK case drops the
first ACK at cumulative bases 0, 2, 45 and 55, and the first two at base 103.
These are conditional predicates: a changed schedule may never emit a selected
base. [RESULTS.json](RESULTS.json) retains untriggered predicates explicitly.
In this comparison only bases 0 and 2 trigger; the predicates for 45, 55 and
103 do not occur in either policy. This case therefore injects two ACK losses,
not all six absent ACKs from the hardware trace.

All 30 cases complete with exact response/chunk checks, no modeled collisions,
physical occupancy at most eight, no retained physical requests and both modeled
radios disarmed. Other controls cover sequence 31/32 loss, startup/SACK/final ACK
loss, partial final trains, multiple holes and slow host work. Real-owner tests
also stop a fill before an ordinary retry can prepare new work, preserving
existing scheduler debt/reservations, journal attempts and nonce state.

The interval is modeled discovery/ARM through receipt confirmation and cleanup.
Actual process startup, CPU and fsync time are excluded. Native RX/setup is
idealized; serial writes retain the 64-byte / 2 ms model. Real flow, owner,
cipher and durable journal code execute, but the full `BundleTransfer`
quarantine/publication application is outside this model. These are not physical
goodput measurements or completion of the 3,000 useful file bytes/s goal.

Reproduce with development dependencies:

```sh
python tools/benchmarks/compare_sf5_repair_fill.py --repo . --output /tmp/sf5-repair-fill-source.json
```

The [helper](../../tools/benchmarks/compare_sf5_repair_fill.py) records exact
runtime and fixture hashes and checks that they stay stable during execution.
Neither policy changes the accepted R11 flow, timeout floor, physical queue,
radio guards or original operation budgets.
