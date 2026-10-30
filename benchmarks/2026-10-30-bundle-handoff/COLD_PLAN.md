# Five-part bundle handoff: source-only cold plan

This is a calculation for a possible 50,000-byte, five-part LoRa check. It is
**not a scope, grant, execution record or physical success evidence**. No radio
operation or budget renewal follows from this document. A separate finite
execution must pin its final source/artifacts, reconcile fresh counters and
original debt, coordinate device ownership and retain bounded cleanup.

## Source and comparison pins

The calculation uses the unchanged waveform/flow constants at SDK source
`46f34d7d6477ddd0ca440bd4656c17e2d486cf67`. The batch lifecycle changes in this
checkout are newer and are not assigned a measured artifact by this plan.

| Source file | SHA-256 |
| --- | --- |
| `src/meshticulum/bench/hat_worker.py` | `23d76358e5ceb33b9ca8da61dc011b0ff3689bf2aa5f0ebdb5b09a04369e921a` |
| `src/meshticulum/bench/serial_worker.py` | `f00b3111e73e6ffc98de48921d8386d13463d7112610bc90d93d620953790ad3` |
| `src/meshticulum/bench/flow_runtime.py` | `0609ea60a7fbbcc76ea9a103c76b1f9e91984f470691a8539812c93630e1f63e` |
| `src/meshticulum/bench/burst_protocol.py` | `b097cc08197d262f250ca18acb3a934cba2ffbd5cbff96333f8a4f5b4e5354d9` |
| `firmware/meshticulum-bench/burst_adapter.hpp` | `05e3be4e8c059862d8fef8e41faa3ee052665e2f8dc57fb91c65f9e4ceb19e67` |
| `firmware/meshticulum-bench/main.cpp` | `bae4a3e39cbd32cf26e5012259ebe4247baaca040f0bc1e3902108eeb0215319` |
| `src/meshticulum/resources/bundle_wire.py` | `20d60fee8590e23e188069bde99901a36f9b4c866b570582f34d3d14d214e75d` |

The historical comparison is [alpha.3 qualification](../2026-10-30-alpha3-qualification/README.md):
candidate `7b38c1f585786d764a952a2a67cbfcf56af0ad80`, installed wheel SHA-256
`ed3f8d23d4aa37cffa3764214f1d9f6a793e09bfabcd6c5bd0b05b858c263520`.
Its public `RESULTS.json` SHA-256 is
`34702167dfc8cb0ca97b90162df2a78d3d19a733ecee06904490c53cc2d736e4`.
That run used firmware `mtb1-6cfde1fdd4`, firmware-source metadata SHA-256
`68d74f96076d073eb403601217d93d9ee6d9eae0e1390749b9703192582a7ce8`.
These pins describe existing evidence, not a new installation or execution.
The recorded firmware identity's `main.cpp` matches the source hash above and
accepts SF5, with a minimum 12-symbol preamble at SF5/6. The source verifier
checks all six application inputs against that identity. No firmware or pacing
change is proposed here.

## Geometry and cost calculation

Use five signed parts of exactly 10,000 useful bytes, offsets 0, 10,000, 20,000,
30,000 and 40,000, in the existing byte-bundle descriptor. Its declared total is
50,000 bytes and its whole-file hash binds all five parts. The proposed order is
**4, 2, 0, 3, 1**, from **A to B**. The existing descriptor supports this
geometry; the normal `byte_bundle.build()` default of 23,900 bytes would make
three parts from this file. A small-part fixture must construct and
authenticate the correct descriptor rather than change the global default.

The sizing model uses the standard `part-%06d` chunk IDs, text `Public file
bytes.`, title `Public file part`, source `SAGE Foundation`, date `2026-10-30`,
licence `CC0-1.0`, base64 data and empty wire padding. With the exact existing
MB1/MR1 encoding, response lengths by index are **10,665, 10,669, 10,669,
10,669 and 10,669 bytes**. Each needs 56 DATA frames at a 193-byte transport quantum. Fresh
payload/hash values do not change these lengths. Prepared execution must verify
the actual signed-response lengths and refuse a changed shape.

Select **SF5/BW500 kHz, CR4/5 and preamble 12**, the fastest LoRa profile
supported by this source. Keep the previously admitted 921.375 MHz, sync word
0x42 and requested chip power 0 dBm, subject to a fresh exact admission. For
packet length `n`, the short-SF header/synchronization formula is:

```python
coded = (8 * n + 16 + 19) // 20
native_us = (105 + 20 * coded) * 16
host_reserved_us = (native_us * 11 + 9) // 10 + 1000
```

These are native airtime estimates and conservative host journal reservations,
not elapsed durations. Full DATA is 255 bytes, an ACK is 70 bytes and the sender
receipt confirmation is 79 bytes. The 56 DATA sizes are 55 × 255 plus
112 bytes for part 0, or 116 bytes for each later part.

| Sender plan | Part 0 attempts / host µs / native µs | Each part 1–4 | Five-part total |
| --- | ---: | ---: | ---: |
| No retries, including one confirmation per part | 57 / 2,184,224 / 1,933,840 | 57 / 2,184,928 / 1,934,480 | 285 / 10,923,936 / 9,671,760 |
| Add eight full DATA attempts per part | 65 / 2,497,056 / 2,210,960 | 65 / 2,497,760 / 2,211,600 | 325 / 12,488,096 / 11,057,360 |

The receiver allowance is 28 ACK attempts per part: **28 attempts / 365,568
host µs / 306,880 native µs**, or **140 / 1,827,840 / 1,534,400** over five
parts. This allowance is a bound, not a claim that exactly 28 ACKs will occur.

## Separate finite limits

The proposed per-part journal caps are **A sender: 84 attempts / 4,000,000
estimated airtime µs**, and **B receiver: 28 attempts / 1,000,000 µs**.
Reserve the full five-part allowances up front: **A: 420 attempts / 20,000,000
µs; B: 140 attempts / 5,000,000 µs**. Full allowances remain reserved
without refunds during the campaign; actual journal spend remains a separate
quantity. Loss that exhausts a cap must stop the operation, not receive an
automatic top-up or successor scope.

Retain independently admitted grant/cipher frame bounds covering each part's
plan. A journal attempt cap does not replace them. Frozen scheduler ledgers are
also separate: with the eight extra sender attempts, maximum calculated sender
own/shared headroom is 5,039,521 / 5,888,161 µs; the receiver's is
1,460,481 / 1,826,049 µs. The journal caps are therefore not adequate substitutes
for the scheduler's own/shared limits. A normally admitted 60-second per-part radio
lease has ample time allowance for these source estimates, subject to its
original file deadline and actual remaining ledgers.

The prior qualified native counters were A **300 attempts / 9,619,200 µs**
and B **871 / 85,947,584 µs**, with zero receive drops. These are historical
observations that must be checked again, not current authorization. Against
the unchanged absolute caps of **1,024 attempts / 300,000,000 µs**, reserving
the full role allowances would put A at at most **720** and B at at most
**1,011**, leaving B only **13 attempts**. Even 84 full 255-byte packets estimate
2,909,760 native µs and 3,284,736 host µs, below A's 4,000,000 µs per-part cap;
28 such packets estimate 969,920 native µs, keeping the full conservative
native addition for B below 4,849,600 µs. Both native cumulative airtime totals
remain below 300,000,000 µs. The actual receiver ACK plan fits its smaller
host journal cap. No reset,
reboot, old-debt refund or reuse of a consumed scope is assumed.

A fresh original **180-second file window** fits five possible 20-second ACK
graces plus a five-second coordinator reserve: 105 seconds, leaving 75 seconds
for DATA, setup and cleanup. It is within the SDK's 240-second bound for five
parts. A proposed 60-second radio lease remains bounded by that original
window; loss cannot renew a grace or deadline. Actual preflight must check
remaining time, counters, fences, owner/boot identity and every fresh grant.

## Measurement and closure requirements

Use one held OS device owner per endpoint with fresh per-part authority,
worker, journal and cipher. Advance only after both previous part sessions
drain, disarm and retire, and make the next receiver ready before its sender.
Preserve the original remote monotonic end across all parts and management
messages. A failed or uncertain part prevents progression; retain its full
reservation and diagnostics. Final success requires exhausting the batch
iterator and joining both endpoint supervisors after device closure.

The target is less than one second **in total across four handoffs**, with
each handoff below one second too. Record receipt-to-next-part-entry and
receipt-to-next-first-DATA intervals on the same sender monotonic clock, plus
the whole-file and scope-to-both-closed boundaries. Retain all host phase
aggregates separately; they are inclusive and cannot be summed into latency.
The target is unmeasured. With full trains and no retries or host/setup cost,
this profile's transport ceilings are 5.572 kB/s without guards and 3.045 kB/s
with the existing guards. This exact file shape estimates 9,671,760 RF µs plus
210 × 5,000 µs gaps and 70 × 100,000 µs reverse windows: 17,721,760 µs, or
**2.821 useful kB/s ideally**. These are decimal source-model rates.

The old 100,000-byte alpha.3 run was B→A, SF7/BW500, preamble eight, with
separate child processes for its larger parts: 98.903315 seconds and 1.0111
profiled useful kB/s. Direction, waveform, part size and lifecycle all change
in this plan. It cannot make a paired speedup claim against that result or
establish sustained capacity, loss/restart recovery or a new distance.
