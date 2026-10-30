# Stalled-window selective repair

The Alpha 4 train12 path adds an opt-in recovery rule for a logical DATA
window that is fully occupied but making no forward progress. It addresses a
measured pattern where seven consecutive sender attempts were absent at the
authenticated receiver, only one later DATA frame arrived, and the next repair
waited for the ordinary 2.22-second retry timer.

## Trigger

`FlowLimits.selective_stall_retry` defaults to false and requires selective
fast retry. BenchFlow admits it only for the explicit v8/v9 repair-train policy
when `train_frames` is 12. Every offer additionally requires:

- fresh DATA still queued;
- the current peer/window allowance fully occupied;
- at least one distinct, later SACK position accepted through the canonical
  receipt and owner path;
- the authenticated original authority still live;
- all earlier DATA physical records drained through the normal release/STATUS
  checks;
- no deferred retry, retry-ceiling, timeout, fence, failure, peer restart or
  physical inventory mismatch.

The flow predicate evaluates logical state. It is not proof of physical drain or
authority and does not grant either. BenchFlow performs those checks before
calling it.

## Bounded behavior

The first eligible hole uses the existing bounded repair train. Each later
normal host step may offer one further distinct proven hole. At most the
admitted train length can join the turn, the native eight-slot capacity remains
authoritative, and any ingress, refusal, UNKNOWN, deferred handover, timeout,
capacity loss or authority change ends the turn.

Every repair still passes the existing owner, cipher, durable journal, native
queue, original deadline and scheduler path. It consumes one attempt and its
normal airtime reservation. No byte, sequence, credit, RTO or debt is
manufactured or refunded. A lost repair falls back to the unchanged ordinary
timeout path.

Ordinary non-train12 flows retain the three-distinct-later-SACK requirement.
Terminal recovery retains its existing original-FINAL predicate. The stalled
path is not a lower RTO and does not alter the five-millisecond receive floor,
100-millisecond reverse floor, two-second native hold or operation limits.

## Source evidence

The paired source model injects two loss controls into the same signed
23,900-byte response geometry:

| Case | Without stall repair | With stall repair | Reduction | Collisions |
| --- | ---: | ---: | ---: | ---: |
| Seven consecutive holes | 8.267515 s | 6.998415 s | 1.269100 s / 15.35% | 2 in both arms |
| Nine consecutive holes | 8.310935 s | 7.231835 s | 1.079100 s / 12.98% | 0 |

The seven-hole case retains the same two pre-existing ACK/DATA model collisions
in both arms. The nine-hole case is the cleaner scheduling comparison. Both
complete with identical attempt counts, closed native queues and disarmed
modeled radios.

These are source-model timings. They exclude real process startup, host CPU,
fsync and physical receive priority. They do not establish Alpha 4 throughput,
RF qualification or a prediction of the next physical run. The raw record is
[`RESULTS.json`](../benchmarks/2026-10-30-sf5-stall-retry-source/RESULTS.json).

## Measured failure input

Case11 physical run11 recorded 13 sender DATA attempts absent from the
receiver in groups of 2, 7, 1 and 3. Seven consecutive attempts were followed by
a 2.057912-second interval with no DATA queue-to-RF_DONE interval outstanding.
All 17 receiver ACKs reached the sender. That run completed, but its diagnostic
1,961.068604 B/s remains below the published 2,872.004839 B/s checkpoint until
the stale post-readback and official exporter checks complete.
