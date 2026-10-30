# Logical eight/32 with physical trains of eight

These 24 deterministic source cases compare two explicit host credit windows
under the same eight-frame radio train. All cases use the same synthetic signed
part: 23,900 useful bytes, 24,565 transport bytes and 128 DATA frames. The role
caps remain 160 attempts / 8,000,000 estimated µs for the sender and 64 attempts /
3,000,000 estimated µs for the receiver. No device is opened.

| Injected case | Logical eight, modeled seconds | Logical 32, modeled seconds |
| --- | ---: | ---: |
| Clean | 6.652215 | 6.652215 |
| First DATA and its early repair lost | 9.602595 | 8.394735 |
| Middle DATA and its early repair lost | 9.483555 | 8.394735 |
| Two holes, including a lost early repair | 9.605595 | 8.707435 |
| Last DATA lost | 9.808695 | 9.808695 |
| 40 / 10 ms injected host work per sender / receiver seal | 8.316680 | 8.316680 |
| 120 / 40 ms injected host work per sender / receiver seal | 18.987940 | 18.987940 |

The wider window reached 31 buffered frames behind a hole; the eight-frame
policy reached seven. Physical occupancy stayed at most eight, and all 24 cases
completed with exact response/chunk checks, no modeled collisions, no retained
physical requests and both modeled radios disarmed. Every attempt and cleanup
result is retained in [RESULTS.json](RESULTS.json), including the cases losing
DATA at offsets 31/32, the first SACK, a train ACK and the final ACK.

The modeled interval runs from discovery/ARM through receipt confirmation and
cleanup. It excludes actual process startup, CPU and fsync time. Native RX/setup
is idealized; USB writes use the existing 64-byte / 2 ms pacing model. The real
flow, owner, cipher and durable journal execute, but the full `BundleTransfer`
quarantine/publication application is outside this model.

These results support bounded lookahead while a hole awaits repair. They do
not establish physical goodput or the 3,000 useful file bytes/s target, and they
are not a paired hardware comparison. Wider credit cannot help a missing final
frame when no later DATA remains to send.

Reproduce from a source checkout with development dependencies:

```sh
python tools/benchmarks/compare_sf5_window32.py --repo . --output /tmp/sf5-window32-source.json
```

The [helper](../../tools/benchmarks/compare_sf5_window32.py) records exact source
and fixture hashes and verifies that they remain stable during the experiment.
It compares v2 and v3 on the same accepted R11 flow; it does not disable recovery
or change timeout, radio-turn or operation-budget policy.

A separate [physical sample](../2026-10-30-sf5-window32-profile/README.md)
subsequently verified the same useful payload at 1,276.917174 B/s including
startup. That observation does not change this source model's evidence scope.
