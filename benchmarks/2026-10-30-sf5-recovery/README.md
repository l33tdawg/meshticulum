# SF5 recovery: 30 October 2026

A finite 3,000-byte B-to-A file completed in **7.609176 seconds**, or
**394.260824 useful bytes/s (0.394261 decimal kB/s)**, from sender SDK child
launch through its authenticated receipt. The receiver verified the publisher,
part and whole-file hash. Both endpoint processes exited normally and closed
their owned hardware; all 35 RF_DONE attempts reconcile with retained debt and
native counters. This is one small file, not sustained throughput or a paired
speed comparison with the earlier five-part 5 kB trial.

The exact tested source is `81786885b2dab2e55507fcbeda8f2f3a67c402b7`, with
unpublished local wheel SHA-256
`8ea6ac4c21316772275f8368144dfb19433c38f8c4bdd337569c6fdc227c9e20`.
Its package metadata remains `1.0.0a3`; the published alpha.3 wheel is unchanged.
The [sanitized hardware record](ACK_POLICY_HARDWARE.json) pins the original
result, frozen supervisor adapter, SDK observer, runtime inventory and scope.

## What the new evidence establishes

The receiver sent 13 ACKs and the sender authenticated all 13. No final ACK was
lost, and no optional final reannouncement was needed. Two original DATA
packets were absent at the receiver. ACK base 5 with SACK `0xe0000000` identified
missing sequence 5 with 6–8 received; base 13 with the same bitmap identified
missing 13 with 14–16 received. Both corresponding retries arrived.

Three distinct higher receipts were already known **1.750682 s** and
**1.928936 s** before the retries, respectively. Their **3.679618 s** total
points to selective recovery as the next source change. The retained sender
physical queue was already settled before those proof-bearing ACKs. This does
not establish why either original packet was missed.

The receiver also spent 289.851 ms and 286.978 ms between receiving each
hole-filling DATA and completing its next cumulative ACK. Those are existing
partial-train quiet waits, separate from sender retry timing. No firmware,
USB pacing or native listening guard was changed.

| Retained trial measurement | Value |
| --- | ---: |
| Useful file bytes / parts | 3,000 / 1 |
| Sender DATA attempts / retries | 21 / 2 |
| Sender receipt confirmations | 1 |
| Receiver ACK attempts | 13 |
| New receive-drop counter increments, A / B | 6 / 0 |
| Sender child launch → authenticated receipt | 7.609176 s |
| No queued DATA outstanding within that interval | 5.336593 s |

The no-outstanding category can still contain ACK processing, host checks,
sleeps and retry timers; it is not CPU-idle time or entirely removable delay.
All timestamps used in a duration share one endpoint's clock. Native receive
drops do not supply an application packet-loss rate or an RF-collision cause.

## ACK scheduling change and source controls

The tested candidate adds two local policies only for admitted queued SF5 with
receipt confirmation enabled: idle unchanged ACKs retain their first 0.5-second
persistence opportunity, then repeat after 1 and at most 2 seconds; a verified
completed receiver can request one repeat of its final canonical ACK. Fresh
receive work, startup and pending ACKs retain their existing path. Every send
still uses the same live authority, scheduler, fresh ciphertext and paid journal.
The optional repeat skips when durable or scheduler headroom is unavailable.
It never renews the original deadline or refunds debt.

The [seven paired source cases](SOURCE_COMPARISON.json) use the same current
source and receipt-confirmation opt-in at both endpoints. The before subclass
disables only the new ACK recovery hook. These are deterministic modeled
host/USB/RF intervals with explicit injected loss, not RF goodput measurements.

| Source control | Before | After |
| --- | --- | --- |
| Clean nine-DATA object | Completes, 0 retries | Identical transmissions and modeled elapsed time |
| One lost final ACK | 1 DATA retry, 3.094126 modeled s | 0 DATA retries, 1.134100 modeled s |
| Four specified DATA losses, 20-ACK cap | Sender stalls at 8/9; 20 ACKs spent | Completes at 9/9 with 12 ACKs |
| Same losses, 64-ACK cap | Completes with 23 ACKs | Completes with 12 ACKs |
| Three explicit slower-host cost controls | Completes without collisions/retries | Same charged attempt totals |

The source model now treats `START_GUARD_US` as deadline/admission headroom,
matching native source, rather than an invented 100 ms pre-TX delay. It uses
actual queue occupancy for early tail reverse listening. Modeled RX priority
remains idealized; header-detection latency, IRQ/SPI work, USB output pressure
and pending-abort RF behavior are not qualified by these controls. Historical
physical measurements are unchanged.

To reproduce these ACK-only source comparisons, use candidate `8178688` with
its development dependencies, then run:

```sh
python tools/benchmarks/compare_sf5_ack_recovery.py --repo . --output comparison.json
```

This candidate passed **3,070 tests, 37 skips and 49 subtests**. Independent
review added actual refusal/NOT_SENT/UNKNOWN controls and a completed receiver
with exhausted own/shared scheduler budgets: more than 1,024 subsequent pumps
retain only its original four handles/ACKs and still accept authenticated sender
confirmation. Frozen flow source and retry floors were unchanged in this trial.

The result does not establish 2–3 kB/s, a larger-file rate, range or production
qualification. All previous failed trials, complete allocations and debt remain
retained. The next selective-retry candidate requires its own reviewed source
revision and finite physical scope.


## Reviewed selective-retry source revision

The [incremental ten-case comparison](SELECTIVE_SOURCE_COMPARISON.json) keeps the
ACK recovery policy enabled in both arms and disables only the new selective
retry flag in the before arm. These are modeled intervals with injected losses.

| Injected source loss | Timeout only | Selective enabled | Sender retries, both arms |
| --- | ---: | ---: | ---: | ---: |
| First DATA | 3.232999 s | 1.215520 s | 1 |
| Middle DATA | 3.350366 s | 1.348300 s | 1 |
| Two separated DATA holes | 5.606144 s | 1.714444 s | 2 |
| Four specified losses | 10.442984 s | 8.413780 s | 4 |

Clean delivery, lost-final-ACK and three slower-host controls retain identical
transmissions and modeled times. The four-loss case uses only one early repair;
its other losses still require the unchanged timeout fallback. The original
ACK-only comparison above remains tied to candidate `8178688`. The current
source helper explicitly disables selective recovery in both ACK-only arms.
A separately scoped physical run is required to measure the new revision.

The final selective-retry source passed **3,126 tests, 37 skips and 49 subtests**,
plus accepted-flow pins, repository/history boundaries, documentation, website
and benchmark checks. The private supervisor projection passed 74 tests and
8 subtests independently.
