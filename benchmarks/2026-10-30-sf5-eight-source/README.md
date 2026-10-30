# Scalar four/eight source comparison

These 22 deterministic source cases compare explicitly admitted four- and
eight-frame SF5/BW500 trains. They establish model behavior, not measured radio
goodput or completion of the 3,000 useful-byte/s goal.

Each case carries the same signed part: 23,900 useful bytes, 24,565 transport
bytes and 128 DATA frames. Two independent host threads run the real flow,
crypto, durable journal and scalar serial worker. The model includes 64-byte /
2 ms USB writes, half-duplex collisions, actual queue underfill and unchanged
5/100 ms listening floors. Native setup and receive rearm are idealized.

| Explicit injected host work per seal, sender / receiver | Four frames | Eight frames |
| --- | ---: | ---: |
| None | 2,924.544 modeled useful B/s | 3,592.788 modeled useful B/s |
| 40 / 10 ms | 2,122.241 | 2,873.743 |
| 120 / 40 ms | 1,174.197 | 1,258.694 |

The modeled interval includes discovery/ARM through receipt confirmation and
cleanup. It excludes real process startup, CPU and fsync time. The received
response and canonical chunk are rechecked, but the complete `BundleTransfer`
quarantine/publication application is not exercised. These rates therefore do
not use the physical goal's timing or verification boundary.

All 22 cases completed with exact response/chunk checks, no modeled collisions,
no retained physical records and both radios disarmed. Additional cases lose
startup/final ACKs, ACK at cumulative sequence 8, individual DATA at the head,
middle, train tail and file tail, and six ACKs at sequence 8. Sequence 8 is the
same fault boundary in both depths; it is the second full train in depth four.
The file-tail and repeated-ACK losses still reduce modeled eight-frame rates
below 2,800 B/s. No result promises a rate under arbitrary loss or host load.

Both fresh source fixtures explicitly allow 160 sender attempts, within their
native 256-attempt profile; receiver allowance stays 64. Production operation
and boot caps are not changed by this experiment. Every charged attempt and
failure remains visible in [the results](RESULTS.json).

To reproduce, install the development/test dependencies in a source checkout:

```sh
python tools/benchmarks/sf5_scalar_train.py . /tmp/sf5-scalar-source.json
```

The [helper](../../tools/benchmarks/sf5_scalar_train.py) imports test fixtures and
is not a released-wheel command. The report records the base revision, whether
the checkout is modified, exact runtime/fixture hashes and whether those inputs
stayed unchanged during execution. No device is opened and no grant is issued
to a physical endpoint.
