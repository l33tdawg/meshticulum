# Bounded scalar fill pair

The explicit `meshticulum.data-burst-policy.v6` option adds `scalar_fill_pair: true`
to the exact v5 terminal-retry policy. It retains logical window 32, native train 8,
repair fill, terminal retry, the original 2 s autonomous hold and 5 ms/100 ms receive
guards. Select it with `policy_for_scalar_fill_pair()` before negotiating the
original contract. V1–v5 policies retain their existing behavior and serialized
policy fields; peers admitting different policies cannot authenticate each
other's frames.

This is a source candidate. No physical throughput gain or completion of the
3,000 useful-file B/s target is established by these changes.

The retained terminal-retry run exposed six queue-empty transitions inside its
host eight-frame groups. Three followed a selective repair; the next native
admission arrived roughly 2 ms after the preceding RF_DONE. The existing native
100 ms empty-tail listening floor correctly applied. That evidence suggests
keeping one extra frame ahead, without weakening the floor or waiting to upload
a complete train. It does not prove that every observed delay is removable.

## Extra opportunity and unchanged handover

`BenchFlow.step()` first runs its normal bounded receive, accounting and
one-frame pump. V6 permits at most one additional fresh DATA pump when exactly
one DATA handover has been accepted during that step. A successful deferred
handover followed by a normal fresh handover already consumes the two-offer
bound. A refused, deferred or unknown latest dispatch cannot enable an extra
opportunity.

Before the extra pump, the adapter checks:

- The native worker and host physical records identify exactly the same occupied
  requests, with at most one retained request. A terminal is still occupied until
  its normal release completes.
- The owner is sendable, the worker has capacity, the current eight-frame train
  has room, and no buffered receive or terminal work needs the next ordinary
  step.
- Canonical peer credit permits fresh DATA, bytes remain, and the original
  application authority is live. The current durable counter is observed again.
- A newly sampled monotonic clock permits the next send and no current flight
  has reached its ordinary retry deadline. Such expiry closes an active repair
  fill under its existing reverse hold; a normal pair simply stops the extra
  offer.

Each extra frame still traverses the held owner, current leases, cipher nonce,
FULL journal reservation and scalar MCU admission. The carrier also refuses any
nonfresh frame on this extra opportunity before allocating a nonce or physical
attempt. If a framework handle was already prepared for a refused offer, its
normal retained accounting is not refunded or hidden.

After an accepted selective repair, the same checks can offer at most one fresh
DATA before returning from that step. The repair continues to count toward the
same eight-frame train. A full train, a partial FINAL repair fill, zero credit,
no outbound data, expiry or a refusal keeps the existing turn behavior. No second
repair or CONTROL joins the extra opportunity.

There is no new native opcode, capability, queue or batch handover. The native
eight-slot scalar protocol remains MBQ8; default four-slot MBQ1 bytes are also
unchanged. Frozen Flow/Port, retry clocks, RF attempts, UNKNOWN containment and
original deadlines are unchanged. The adapter never waits for a full train to
be uploaded before allowing the native queue to run.

## Source controls and comparison

[The composition controls](../tests/test_burst_scalar_fill_pair.py) exercise real
owner, scheduler, cipher and journal code with modeled radio records. They cover
policy mismatch, individual debt/nonces, low-water capacity, pending terminals,
buffered ingress, deferred handover, refusal/UNKNOWN, ordinary expiry during the
first handover, full FINAL, cancellation exceptions and large monotonic clocks.

The [paired helper](../tools/benchmarks/compare_sf5_scalar_fill_pair.py) compares
v5 and v6 with the same signed 23,900-byte useful input, 24,565-byte response and
original role caps. Its fault schedules include the six logical DATA losses
from run04, tail losses, lost ACKs and explicit per-seal/per-step host delays.
Injected delays are sensitivities, not a calibration of the physical host.
Real CPU, fsync and process startup are excluded from its modeled clock; it
cannot qualify physical useful-file throughput. Conditional ACK predicates and
any untriggered loss rules remain visible in the output.

The [retained source comparison](../benchmarks/2026-10-30-sf5-scalar-fill-pair-source/README.md)
completed all 32 rows. Clean and six-loss cases were unchanged. Injected host
delays produced conditional gains and one small regression; they do not establish
how much of the measured physical delay this change will remove.
