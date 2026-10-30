# Selective repair after the peer receives FINAL

The explicit v5 source policy can repair a remaining tail hole after one
distinct later SACK receipt. It applies only after the sender has handed over
the original FINAL frame, has no outbound DATA left, and has accepted a
canonical peer receipt for that FINAL frame. The existing bulk threshold of
three later receipts and the default flow API remain unchanged. This source
revision has no physical throughput qualification.

## Exact opt-in and proof

`meshticulum.bench.burst_protocol.policy_for_terminal_retry()` returns the
exact v4 record with schema `meshticulum.data-burst-policy.v5` and
`selective_terminal_retry: true`. The policy binds both peers through the
original grant and packet authentication context. It retains physical depth
eight, logical window 32, repair-fill behavior and the existing SF5 requirement.
The v1–v4 records retain their previous bytes and behavior.

The generic flow option is `FlowLimits(selective_fast_retry=True,
selective_terminal_retry=True)`. The caller must also explicitly request
`selective_retry_ready(terminal=True)` or
`retry_selective_gap(now, terminal=True)`. Omitting that keyword keeps the
three-receipt threshold, including on a flow configured for the new option.
Readiness is a hint; dispatch rechecks the existing state and timing guards.

The flow records FINAL handover only after actual carrier acceptance. A
prepared or deferred original FINAL does not establish it. Peer FINAL proof
comes only from retirement by a completely accepted canonical ACK, after the
owner accepts its receipt and credit. Stale, conflicting, impossible, foreign
or owner-refused receipts do not establish proof. Evidence for each hole still
consists of distinct, actually handed-over later sequence positions; repeats
and newer revisions of the same receipt cannot invent another position.

The bench adapter additionally requires the existing empty physical maps,
released terminal records, checked drained STATUS and live original authority
before offering a selective repair. A logical ACK cannot substitute for that
physical accounting. The existing receipt-confirmation opt-in is still needed.

## Bounds that stay in force

Each original logical flight has one early opportunity, consumed before an
offer even if refused or deferred. An ordinary timeout offer consumes that
opportunity too. All accepted retries use the normal owner, fresh cipher
counter, durable attempt reservation and worker admission. Retry ceilings,
lease expiry, cancellation, peer incarnation, UNKNOWN fencing and charged
attempt/airtime debt remain unchanged. R11 selective retry preserves the
flight's RTO; ordinary timeout handover still doubles it within its bounds.

After a terminal repair, the sender uses the existing reverse hold. There is
no fresh outbound DATA to fill the rest of that train. Receiver quiet timing,
ACK persistence, native listening floors, serial pacing and retry clocks are
unchanged. A missing FINAL disables the lower threshold for every hole; the
existing three-receipt path or ordinary timeout can still recover eligible
work. A hole with no later receipt remains on timeout recovery.

## Source controls

The generic controls reproduce a 128-frame tail with holes 123, 124 and 126,
where accepted FINAL 127 and frame 125 supply evidence counts of two, two and
one. They also cover missing or unhanded FINAL, rejected ACKs, sequence wrap,
repeated evidence, dispatch refusal/defer, cancellation, retry limits and
unchanged no-argument behavior. Composition controls retain real owner,
cipher and journal accounting, including physical release withheld after a
peer ACK, UNKNOWN and a changed MCU boot during drained STATUS.

The deterministic half-duplex controls include a lost terminal repair,
missing FINAL and lost ACKs. These are source tests, not a replay of a measured
radio channel. They cannot explain physical packet losses, prove a speedup
or establish the 3,000 useful file bytes/s goal. See
[repair-fill](SELECTIVE_REPAIR_FILL.md) for the unchanged bulk policy.

## Physical policy sample

The [first v5/native-RX diagnostic sample](../benchmarks/2026-10-30-sf5-terminal-retry-profile/README.md)
verified 23,900 useful bytes in 11.793771 seconds including sender process
startup (2,026.493477 B/s). All six repairs still had fresh outbound DATA,
used the existing bulk threshold and formed full repair-fill groups. The
terminal-only threshold was enabled but not exercised. Different losses and
diagnostic firmware prevent a causal comparison with the earlier sample;
physical qualification of the lower terminal threshold remains open.
