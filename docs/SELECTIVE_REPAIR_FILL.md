# Carry fresh DATA after one selective repair

The explicit v4 source policy can use the remaining positions in an eight-frame
radio train after one selective repair. It keeps the logical window at 32 and
does not change the accepted R11 flow, retry timers, native firmware or radio
guards. Physical qualification of this policy is still pending.

## Exact opt-in

Use `meshticulum.bench.burst_protocol.policy_for_repair_fill()` to construct the
original shared policy:

```json
{"schema":"meshticulum.data-burst-policy.v4","handover":"irrevocably-charged-at-queue-acceptance","max_depth":8,"max_hold_us":2000000,"inter_packet_rx_us":5000,"reverse_listen_us":100000,"train_frames":8,"flow_window_frames":32,"selective_repair_fill":true}
```

The record is exact and participates in the original route/grant digest. V3
and v4 peers cannot authenticate packets under different policies. The v1/four,
v2/eight and v3/logical-32 selectors retain their existing behavior. A later
grant or property mutation cannot enable this mode on an existing run.

The owner window remains 6,656 bytes and reassembly remains 32 positions.
Physical depth and train count remain eight. The unchanged MBQ8 capability and
serial formats apply. Original role attempt/airtime limits, deadlines, boot
debt, frame count and retry ceilings remain independent bounds.

## What changes after a repair

The existing selective-retry API still requires three distinct later SACK
receipts, one early offer per logical flight and checked physical drain before
the offer. The normal owner, cipher, journal and worker admit and charge the
repair. Its QUEUED handover already counts as the first frame of a train.

V3 yields a dedicated reverse turn immediately. V4 can keep that counted repair
and let subsequent bounded host steps admit up to seven **new** DATA frames,
provided the current canonical credit has room and unsent data remains. Each
frame independently passes the existing credit, authority, cipher and durable
attempt checks. The step that offered the repair still returns; no unbounded
dispatch loop is introduced.

The eighth accepted frame uses the existing reverse hold. Receiver ACK
coalescing stays eight, partial-train quiet stays 250 ms, and native checked
5 ms dwell / 100 ms train-or-empty-tail listening floors remain unchanged. Host
underfill can still produce a shorter physical train.

With no outbound DATA, no spare credit, a full train or an existing hold, the
original single-repair reverse turn applies. A full-size final frame retains its
FINAL bit. If new DATA finishes the file before eight positions fill, the
receiver's existing complete-ACK path applies.

An active fill cannot accept another selective repair. If an ordinary timeout
becomes due, the adapter reads the unchanged R11 pending deadlines and ends the
fill before queued dispatch or new handle preparation. The owner is held for
the original reverse turn; the ordinary retry follows the normal flow path
afterwards. Existing prepared work remains exactly held. This guard does not
reset a timer, refund reservations, reserve a nonce or spend a physical attempt.

## Evidence and limits

[The source comparison](../benchmarks/2026-10-30-sf5-repair-fill-source/README.md)
uses identical signed 23,900-byte input, logical window 32 and physical depth
eight. Its ten-DATA-loss case uses logical sequence/attempt loss counts from a
retained window32 hardware run. It is not a replay of that run's radio, host
scheduling or clocks. The separate ACK-loss predicates and any predicates that
do not occur remain visible.

All 30 source cases complete with exact response/chunk checks and no modeled
collisions. Tests also cover timeout during a fill with and without a prepared
new-DATA handle: journal, nonce, owner attempts, scheduler debt and pending
reservations stay unchanged at the hold boundary.

This policy cannot accelerate a missing final DATA when no later data remains.
It does not rebase retry clocks on RF_DONE, shorten the 2.22-second queued
minimum, explain physical packet losses or establish the 3,000 useful file
bytes/s target. Completion-anchored recovery and native receive diagnostics
remain separate work.
