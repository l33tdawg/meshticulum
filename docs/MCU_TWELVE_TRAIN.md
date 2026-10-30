# Twelve-frame trains with eight queue slots

The explicit v9 source experiment separates a train's admission count from its
simultaneously retained native descriptors. A train can admit twelve DATA
packets through eight slots as earlier terminal records are durably settled and
released. Target builds are reviewed separately from these source controls.
This change has no installation or RF qualification result.

`policy_for_train12()` returns the exact v8 policy with schema
`meshticulum.data-burst-policy.v9` and `train_frames: 12`. It retains
`max_depth: 8`, logical window 32 and every v8 prerequisite. Both peers must
bind that policy in their original grants. Policies v1–v8 retain their existing
bytes and behavior. A v9 host refuses an older capability before ARM or DATA.
The accepted frozen Flow and FlowPort sources are unchanged.

## Native and peer bounds

The distinct capability selector is `08 0c`. Its 25-byte `MBT1` reply uses
`!4sBBBHIIII`: magic, eight slots, twelve admissions, flags 7, maximum packet
255 bytes, hold 2,000,000 µs, start guard 100,000 µs, receive dwell 5,000 µs and
reverse listen 100,000 µs. OPEN prefixes the existing 48-byte body with those
two selector bytes and echoes all 50 bytes. Replaying OPEN must match the exact
shape; reopening the same context cannot change depth, train or deadline.
The original empty/`08` capability and 48/49-byte OPEN formats are unchanged.
DATA, event, release, cancellation and STATUS records are unchanged.

Every scalar DATA still receives its own fresh ciphertext, original authority
check, durable prepaid journal reservation and exact native acceptance. The
native queue and release caches remain eight entries. A terminal record still
occupies its slot until exact release. Releasing one slot does not reset the
twelve-admission count or common deadline: only complete release of the group
allows a new group. A thirteenth admission is refused even if a slot is free.
All attempt and airtime debt survives release, cancellation and uncertainty.

The first DATA fixes the original common deadline; refills never renew it.
Admission and issue retain their conservative remaining-time checks, original
two-second autonomous bound, checked receive restart, 5 ms inter-packet floor
and 100 ms reverse floor. An actual empty queue or expired suffix still creates
a full reverse turn. A new owner or OPEN cannot erase that receive-anchored
floor. UNKNOWN and failed receive rearm remain fenced.

The host coalesces up to twelve DATA arrivals, while canonical credit and
reassembly remain bounded at 32. SF5 quiet time remains 250 ms and maximum
coalescing hold remains 2 seconds; neither becomes 3 seconds. Sparse or slow
arrivals can reach either limit before a nominal train finishes. The receiver
and sender do not share a loss-free train counter, so this policy does not
promise that ACK transmission can never overlap DATA. Terminal repair trains
use the same twelve-admission ceiling, distinct-domain proof checks and one
additional offer per normal step. Physical occupancy remains eight.

## Saturated-window repair

The Alpha 4 source adds [stalled-window selective repair](STALLED_WINDOW_RETRY.md).
When fresh DATA remains, the peer/window allowance is fully occupied, all
earlier DATA has physically drained, and one authenticated later SACK proves a
blocking hole, BenchFlow may spend the existing bounded repair train before the
ordinary 2.22-second timeout. Eligibility is enabled only for the explicit
train12 policy and remains subject to the same owner, cipher, journal, native
queue, authority and deadline path for every attempt.

The paired source model injects seven or nine consecutive holes into the same
signed response geometry. Stall repair reduces modeled elapsed time by
1.269100 seconds (15.35%) and 1.079100 seconds (12.98%) respectively without
changing attempt counts within either pair. The seven-hole arm retains two
pre-existing model collisions in both variants; the nine-hole arm has none.
These modeled timings exclude real process startup, host cost and physical
receive priority and do not establish a measured rate.

The original admission fit, in microseconds, is:

```text
train × (maximum DATA airtime + 100000 + 5000) + 100000 + 50000 <= 1980000
```

| Preamble | Maximum 255-byte airtime | Twelve-frame fit | Sixteen-frame fit |
| --- | ---: | ---: | ---: |
| 12 symbols | 34,640 µs | 1,825,680 µs | 2,384,240 µs |
| 32 symbols | 35,920 µs | 1,841,040 µs | 2,404,720 µs |

Twelve fits this existing SF5/BW500 envelope; sixteen does not. Fit is a refusal
bound, not a completion guarantee. No prime-two delay, release batching, new
retry timer, guard reduction, operation cap increase or firmware default
change is part of this experiment.

## Source evidence and its limits

The paired helper runs v8/eight and v9/twelve with the same signed 23,900 useful
bytes and 128-DATA response geometry, real flow/owner/crypto/journal paths,
eight-slot retention, common-deadline checks and original role allowances.
The sender retains 160 attempts/8,000,000 host-estimated µs and receiver
64 attempts/3,000,000 µs. Reproduce it without hardware:

```sh
python tools/benchmarks/compare_sf5_train12.py --repo . --output /tmp/train12.json
```

| Modeled condition | Eight train | Twelve train |
| --- | ---: | ---: |
| Clean | 6.652215 s | 6.177215 s |
| Six terminal logical holes | 7.300535 s | 6.825535 s |
| Missing original FINAL | 9.808695 s | 9.333695 s |
| Clean, injected sender/receiver seal work 40/10 ms | 8.316680 s | 8.221680 s |
| Terminal holes, injected seal work 120/40 ms | 20.050240 s | 19.625240 s |

All 24 arms completed with paid debt, drained queues and disarmed modeled
radios. The nine-terminal-hole and terminal-40/10-ms cases retain two modeled
collisions in each arm. The clean 475 ms difference matches five fewer reverse
turns, each replacing a 100 ms floor with a 5 ms floor. Injected host costs can
reduce that advantage; the model does not remove empty-tail guards.

These clocks exclude real process startup, native setup, actual host CPU/fsync
costs and full application publication. Receive priority and native setup are
idealized. The terminal-hole schedule reproduces only selected logical holes,
not the complete physical run08 loss/timing trace. No model rate establishes
the goal of repeated physical runs at least 3,000 useful file bytes/s including
startup. Run08's measured 15.147523 seconds would need to fall below 7.966667
seconds for the same 23,900-byte file; this source result does not close that gap.

`test_burst_train12.py` covers exact peer policy/capability/OPEN binding, original
fit, slot reuse without renewal, refusal and UNKNOWN debt, unchanged logical
limits and DATA/ACK-loss models. `test_burst_train12_native.py` runs the actual
adapter and queue with ASan/UBSan, profiling off and on: twelve admissions,
eight-slot pressure, malformed admission, replay, singleton/expired tails,
checked rearm failure, cancellation and inherited reverse floors. Previous
policy controls and the complete standalone suite remain required.
