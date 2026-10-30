# R13 repeat selective repair — accepted source review

The owner explicitly accepted this source revision after root and independent
review. It is pinned in the frozen verifier. A subsequent
[physical sample](../benchmarks/2026-10-30-sf5-repeat-retry-profile/README.md)
verified a file at 1,577.815726 useful B/s including startup, below the
throughput targets. Repeated capacity qualification remains open. All v1–v6
defaults retain the accepted R12 behavior.

The accepted flow SHA-256 is
`963c2a3b4fdd8292d8a473da67dd7eaeee735f27ab4bd77f18702583cd2e1a9e`;
the port SHA-256 is
`ea29289b960ca895f54ccb46669b476a50bae79ab9a6b4060342da8c5cfacf07`.
The [revision record](../provenance/FLOW_REPEAT_RETRY_REVIEW.json) binds all four
runtime hashes and review receipts. The owner acceptance receipt SHA-256 is
`90aa8daf9435ff39a83da3abfb209625ddf019f0b8e2ab160829de1c83f4b598`.

The motivating retained v6 run lost five selective repairs. For each affected
hole, later canonical SACKs retired at least three distinct higher positions
after the lost repair's carrier acceptance. The preceding one-offer-per-flight
rule could not use that new proof. This observation does not identify the RF loss
cause or predict a physical throughput gain.

## Explicit admission

`policy_for_repeat_retry()` returns the exact
`meshticulum.data-burst-policy.v7` policy. It adds
`selective_repeat_retry: true` to the existing v6 policy: logical window 32,
physical train and queue 8, repair fill, terminal selective retry and bounded
scalar pairs. Peer grants bind the entire canonical policy. Partial shapes,
mixed policy ciphertext and a non-SF5 eight-frame admission are refused.

Generic `FlowLimits.selective_repeat_retry` defaults to false and requires
`selective_fast_retry`. It does not lower the bulk threshold of three distinct
higher SACK positions. The existing separately admitted terminal threshold of
one still needs actual original FINAL handover, empty outbound DATA and a
canonical peer receipt covering that FINAL.

## A proof epoch belongs to one accepted attempt

An actual, confirmed retry handover clears that flight's prior selective
evidence and opens one new early-offer opportunity. An ordinary timeout
handover can also start this new epoch. A refused offer, a held `DEFERRED`
request, a known-unsent cancellation or an uncertain invocation cannot do so.

Only fully canonical, owner-accepted ACKs may add proof. A proof position must
be a higher live flight newly retired by SACK, observed at a finite,
nonregressing host timestamp no earlier than the current epoch. Repeated,
stale, conflicting, impossible, foreign and owner-refused receipts add none.
Receipts observed while the retry is held are discarded; repeating their
positions after handover cannot recover them. At most three positions are
stored per hole. Sequence-wrap comparisons use the existing bounded flow
ordering.

The offer consumes the opportunity before calling the carrier, including a
refused or deferred offer. A later confirmed handover may open the next epoch,
but that epoch starts empty. A retired or terminal flight is never recreated
by a late owed handover. Attempt and maximum-retry ceilings are unchanged.

## Exact port disposition and separate clocks

The opt-in `CarrierHandover` record distinguishes `ACCEPTED` from `UNKNOWN` at
the exact held-object settlement. Both remain truthy charged invocations.
The port samples a fresh local monotonic timestamp after invocation only for
v7 DATA. This timestamp is the proof-epoch lower bound, not RF completion,
peer receipt or permission for another transmission.

Proof time must not precede this invocation's existing handover anchor, the
previous epoch, or an already observed canonical receipt. A clock exception,
invalid value or regression preserves the charged attempt and fences the
flow. Cancellation is re-raised after this accounting. An uncertain carrier
outcome likewise remains charged and fenced without opening a new epoch.
Deferred dispatch carries the same exact disposition and timestamp; a
foreign or repeated settlement cannot lend another request's confirmation.

The original retry timer anchors are unchanged. Selective handover preserves
the existing per-flight RTO, ordinary timeout handover doubles it within the
same bounds, and queued work retains the original conservative allowance.
The SF5 minimum remains 2.22 seconds. A due ordinary timeout has priority over
an early offer. No new physical timestamp or response-horizon policy is part
of this revision.

## Preserved physical and accounting boundaries

The bench adapter still requires live original authority and exact physical
release plus drained STATUS before a selective offer. Repair fill admits
only fresh DATA behind the repair. Scalar pairs keep their per-step limit,
fresh-clock checks and per-frame owner, cipher, nonce, journal, scheduler and
native acceptance. Every new retry spends another attempt under the original
finite role caps. No old debt is refunded.

No native firmware, wire opcode, queue capacity, 5 ms or 100 ms listening
guard, two-second native deadline, receiver ACK policy or resource encoding
changes. No policy is silently enabled for an existing grant. Existing port
Boolean returns and deferred callback shape are preserved for v1–v6.

## Source validation and limits

The new controls cover new proof after a lost repair, repeated and late ACKs,
deferred proof discard, accepted/refused/unknown dispositions, post-call clock
failure and cancellation, sequence wrap, ordinary timeout priority, full-size
FINAL identity, terminal proof, max retries, original authority and real
owner/scheduler debt. Composition controls use real cipher/session stores and
durable attempt journals with modeled native radios.

The author run passed 87 new focused controls and the full suite: 3,779 tests,
37 skips and 49 passing subtests. Root independently passed the 87 new controls.
The independent reviewer passed 394 controls, including 39 additional private
tests for initial DATA and probe faults, exact duplicate settlement, terminal
owed retries, default clock overhead and accepted-source trace parity.
All 30 arms of the exact-source comparison
completed with verified response/chunk bytes, bounded occupancy, known
`RF_DONE` journal settlements, closed radios and no modeled collisions.

| Synthetic case | v6 elapsed | v7 elapsed | Sender attempts, both |
| --- | ---: | ---: | ---: |
| Clean | 6.652215 s | 6.652215 s | 129 |
| Retained v6 DATA-loss counts | 15.821215 s | 14.069975 s | 155 |
| Original and first repair lost | 8.366095 s | 7.182615 s | 131 |
| Original and two repairs lost | 13.050015 s | 7.400315 s | 132 |
| Missing original FINAL | 9.808695 s | 9.808695 s | 130 |

The retained-loss-count comparison changes the recovery timing, not the number
of sender attempts: selective retries increase from 19 to 23, with the same
26 total retries. A missing FINAL still has no later receipt and receives no
new permission from this option. These figures use the modeled boundary below;
they are not measured application rates.

`tools/benchmarks/compare_sf5_repeat_retry.py` compares v6 and v7 with identical
signed 23,900-byte useful input, 128 logical DATA frames, radio model and role
caps. It includes the retained v6 logical loss counts, lost repairs, terminal
holes, a missing FINAL, conditional ACK loss and injected host-work cases.
The model records every configured but untriggered fault, collision, error,
retained journal charge and peak occupancy. It does not replay actual RF
timing or identify a receive-loss cause.

Modeled elapsed time includes discovery, ARM, receipt confirmation and cleanup
but excludes real process startup, CPU and fsync time. It cannot establish the
repeated physical goal of at least 3,000 useful file bytes per second including
process startup. Source acceptance alone establishes neither a physical result
nor the throughput goal.

## First physical observation

The separately authorized physical sample used exact SDK `7102952`, the v7
policy and the same header-preservation firmware and MCU boots as its v6
predecessor. It verified 23,900 useful bytes in 15.147523 seconds including
startup, with 18 selective DATA retries and zero ordinary retries. Eighteen
accepted retry epochs were recorded; that count alone does not demonstrate
that any flight used another selective opportunity after a lost repair.

The observed rate was 6.659214% below the preceding 1,690.381876 B/s sample.
Loss and recovery patterns differed, so the pair does not isolate the policy
effect. Both endpoints closed and drained with all prior paid debt retained.
The [physical record](../benchmarks/2026-10-30-sf5-repeat-retry-profile/README.md)
preserves the exact result and timing boundary. The repeated 3,000 B/s goal
and above-2,800 B/s checkpoint remain unmet.
