# Meshticulum bounded reliable flow — Ember, isolated candidate

**Status: ISOLATED CANDIDATE, revision 9. No radio, no serial port, no broker, no
socket and no runtime import. Nothing is wired into `sage_lantern`, nothing is
installed or deployed, and no measurement below is a radio result.**

Revisions 2 to 9, 4–9 October 2026. Owner: Ember (`dsh/sage-commons`). Meshticulum task
`MT05` (SAGE `9aee0d2f-6753-41fc-82e9-fd0b1903531b`), program parent
`5ce51f3f-c41e-419b-8e7a-a3723ea7a84a`. Research: `work/meshticulum-review-20261004/`.
Evidence: `work/ember-meshticulum-mt05-20261004/`. Revision 1 is preserved
byte-for-byte in `work/ember-meshticulum-mt05-20261004/frozen-r1/`; the revision 5
bytes this one replaces are held in
`work/meshticulum-plan-audit-20261004/credit-contract-review/inputs/`, and the
revision 8 bytes this one repairs are held in
`work/rook-meshticulum-r8e-review-20261006/candidate/`.

## Revision 2

Revision 1 was independently reviewed and withheld: 17 RED / 3 GREEN invariant
probes (`work/rook-meshticulum-mt05-review-20261004/`). Revision 2 repairs every
finding, and each repair carries a discriminating test that is RED against the
frozen r1 bytes — `work/ember-meshticulum-mt05-20261004/R1_DISCRIMINATION.log`
replays those cases against r1 and reproduces the review's measurements,
including its exact figures.

| Review finding on r1 | Repair in r2 |
| --- | --- |
| A dropped overlay ACK stranded the sender: duplicate data was absorbed without re-asserting receipt, so the sender retried to its ceiling while the receiver sat complete. | A duplicate of an already-admitted frame re-asserts current receipt and credit, bounded by the ACK delay and the reverse-link allowance. |
| Terminal state could be resurrected: post-completion data was delivered, flipped the flow back to complete and drew an ACK. | Terminal state fences admission. The single deliberate exception is the duplicate re-assertion above. |
| An ACK for sequence numbers never sent removed pending frames and fabricated completion. | Cumulative and selective acknowledgements are validated against the sequence space actually sent; credit is bounded by the declared window. |
| One shared state per flow let a short transfer mark the long direction finished: both endpoints reported complete while one still held 7936 queued bytes. | Each direction has its own state, and each endpoint declares which directions it uses. Nothing is inferred from traffic. |
| Payload size, schema and unconsumed bytes were unbounded: a 200-byte payload was admitted under a 64-byte frame size, and delivered retention grew without limit. | Frame payload, total frame count, reorder window and unconsumed delivered bytes are all bounded; `consume()` is what returns credit. |
| Generation 257 normalised to 1 and admitted stale data; unknown flags, out-of-space sequences and mismatched ACK bitmaps were coerced; invalid limits were accepted; a duplicated carrier frame could exceed the declared queue. | Out-of-range values are refused rather than masked, parsers are strict, limits are validated at construction, and the carrier queue accounts for every copy a frame will produce. |

Flow demultiplexing moved to an explicit `flow_id`, separate from
`session_generation`, so two live flows no longer have to choose different
generations to be told apart.

Revision 3 closes the residual witnesses in the finalised findings
(`work/rook-meshticulum-mt05-review-20261004/FINDINGS.json`, F01-F09): the
terminal fence also covers the receive-ceiling failure path, an ACK whose credit
claim exceeds the negotiated window is refused alongside an impossible sequence,
a frame claiming LAST before the declared end is refused before any state is
mutated, the reassembly window is bounded against the sequence space at
construction, the carrier's duplicate control uses the reviewer's exact witness
with a paired clean control, and `FlowMux` refuses a duplicate
`(endpoint, flow_id, generation)` registration instead of silently sharing it.

**On generation lifetime:** eight bits is a finite space, so a generation value
is reused after 256 sessions. Refusing out-of-range values stops the silent
alias, but it does not make retirement permanent. Non-reuse or safe retirement
across the full identity space is an MT03 contract question and this candidate
does not claim otherwise.

## Revision 4

Revision 3 was reviewed independently and held on nine semantic and two
configuration findings (`work/rook-meshticulum-mt05-r3-review-20261004/`).
Revision 4 repairs all eleven. The evidence is not this document's word for it:
the reviewer's own flow probes are RED nine times out of twenty-two against the
frozen r3 bytes and GREEN twenty-two out of twenty-two against this revision
(`work/ember-meshticulum-mt05-20261004/r4-probes/`).

| Finding | Repair in r4 |
| --- | --- |
| R3-F01 a lost credit grant left an honest transfer stuck after recovery | A receiver that still expects data and has room re-announces its state at most `max_persist_acks` times, spaced `persist_interval_s`, and that budget resets on every admitted frame. |
| R3-F02 unsent selective positions were accepted before mutation | Every asserted selective position is validated against the actually exposed sequence interval; an impossible assertion is refused whole, before any cumulative deletion or credit change. |
| R3-F03 a stale cumulative ACK rolled credit backward | Cumulative progress has a monotonic floor, so an older acknowledgement cannot close the window; an equal one may still carry a fresh consume grant. |
| R3-F04 cancel still emitted ACKs after a completed receive | Cancellation sets a session-wide output fence. The completed-receive duplicate response survives only while the session does. |
| R3-F05 completed-receive duplicate ACKs bypassed the ACK delay | The rate bound covers every ACK, reassertions included: three duplicates in one instant are one ACK, while a reassertion after a real gap still goes promptly. |
| R3-F06 assumed initial credit could overrun an honest receiver | The sender assumes only what its own accepted limits can hold - `min(initial_credit_frames, reorder slots, receive byte capacity)` - rather than the negotiated maximum. |
| R3-F07 buffered bytes were not reserved against advertised credit | Held bytes count against credit, and drain waits for the consumer instead of failing an honest reordered flow. |
| R3-F08 out-of-transfer buffered data was delivered after completion | Admission is judged against the remaining absolute transfer span using the frame's own declared total, and delivery stops at the declared boundary. |
| R3-F09 a wider ACK bitmap acknowledged the wrong sequence | One fixed bitmap width; a wider frame is refused structurally rather than read at the wrong offsets. |
| CR-LIMIT-01 incompatible types and non-finite bounds were accepted | Integers must be integers (a bool is not one) and timing values must be finite numbers, so a limit cannot reach the wire packer and fail there. |
| CR-LIMIT-02 limits exceeded their 16-bit wire fields | `seq_modulus` and `max_total_frames` are bounded by the fields that carry them. |

Two things found while repairing, worth carrying forward:

* **The retry clock starts when a frame reaches the air, not when it is queued.**
  A full window on a slow carrier spends longer than the RTO in the radio queue,
  which reads exactly like loss: the un-repaired flow sent eleven spurious
  retransmissions on a *clean* link. The clock now starts at the estimated end of
  that frame's own transmission, from the carrier profile the flow was given.
  That estimate is a scheduling input, **not** reservation-bound RF completion
  proof, and must never be promoted to one.
* **New surface**: `persist_interval_s` and `max_persist_acks` in `FlowLimits`;
  `stale_acks` and `out_of_transfer` counters; `acked_frames()` for observing
  acknowledged progress without touching internals.

Still deliberately unsolved, and named rather than hidden: the completed-receive
duplicate reassertion remains the one place a completed direction accepts input,
because it is the R3-F01 repair; and an eight-bit generation is still reused
after 256 sessions.

## Revision 5

Revision 4 was reviewed independently and held on five findings
(`work/rook-meshticulum-mt05-r4-review-20261004/FINDINGS.json`, R4-F01..F05). The
reviewer's own eleven paired observations were reproduced on the frozen r4 bytes
*before* any repair — 5 GREEN / 6 RED, matching his record case for case — and all
eleven are GREEN against this revision (`work/ember-meshticulum-mt05-20261004/r5/`,
`REPRODUCTION.md` and `PROGRESS.md`).

| Finding | Repair in r5 |
| --- | --- |
| R4-F01 a lost cumulative ACK made every retransmission fail as out-of-transfer | An already-admitted frame is recognised as a duplicate *before* the absolute-span test, so it re-asserts receipt instead of being refused. Narrow on purpose: only frames behind `_expected`, and only once something has been delivered. |
| R4-F02 exhausted credit persistence left the sender open with no remaining event | A bounded zero-window credit probe repeats the last frame the receiver *acknowledged*, so it cannot aim new data at a closed window. An answered probe is liveness, not failure; only consecutive silence is bounded and ends the flow in `credit_stalled`. No new limit. |
| R4-F03 an old equal-base ACK overwrote fresh consume credit | The ACK carries a monotone grant revision, stamped on every emission. Credit is taken only from a revision newer than the last one seen, so an equal-base snapshot can no longer move the window in either direction; the probe stays as the recovery path for a grant that was genuinely lost. |
| R4-F04 whole-session failure and peer restart did not fence completed-RX ACKs | The cancel-only output fence became a session fence: `_fail_session` sets it exactly as `cancel()` does. `recv_state` still stays COMPLETE — the fence stops output and admission, it does not rewrite what happened. |
| R4-F05 the queue-depth estimate admitted a negative RTT sample | The sample is measured from the instant the frame was handed to the link, never from `sent_at`, which is an estimate that can overstate when a duplicated frame finished. `_sample_rtt` additionally refuses a non-positive sample rather than folding it in. |

**The wire grew one field for this, and MT03 owns the layout.** An ACK now carries
`grant_rev`, a 16-bit revision a receiver advances on every acknowledgement it sends
(cycling 1..65534, so a real revision is never zero). Zero means "this peer sent no
revision", which is the older frame shape: while no revision has ever been seen the
sender behaves exactly as it did before, so an older peer keeps working. Once a
revision has been seen, credit is accepted only from a strictly newer one, and a
refused snapshot is counted in `stale_grants` — counted, not silently dropped, and it
withholds the credit update only. The rest of the frame is processed either way, so a
stale snapshot cannot suppress selective acknowledgements, and it can never refuse a
send or move a retry decision. `work/ember-meshticulum-mt05-20261004/r5/f03_residual_probe.py`
measures both directions before and after: a stale credit 0 and a stale credit 8 each
left the window at 4 on this revision, where both previously moved it.

One regression this revision caused and fixed: the probe first re-asked once per
round trip for as long as the peer answered "no room yet", which moved the
receiver's acknowledgement cadence and broke a previously accepted case's timing
assertion. A no-grant answer now widens the probe gap, saturating at `max_rto_s`.

## Revision 6

Revision 5 was independently reviewed and **held, not approved**. The review found
that its ACK freshness was a withdrawal rather than a contract: only the credit
update was withheld, while the rest of an old acknowledgement was still applied.
That leaves the indirect path open — an old equal-base snapshot can name receipt
the endpoint has moved past, and retiring that receipt releases window slots, so a
statement the peer has already superseded still mints capacity. The 16-bit modular
revision had no monotone non-reuse either, and it advanced on every emission even
when the state it described had not changed.

The frozen decision
(`work/meshticulum-plan-audit-20261004/credit-contract-review/DECISION.json`,
sha256 `9fed87684d33657d8e0584c3780dfc2eddf566251c94474502c8372d9354bd4c`) replaces
that design. Revision 6 implements it point by point:

| Decision clause | What the bytes now do |
| --- | --- |
| MT3 on both directions | `MAGIC = b"MT3"` for DATA and ACK. MT2, an unknown magic or kind, a truncated or trailing frame, and both older ACK shapes (19-byte, and MT2's 21-byte) are refused at the parser, before any state is mutated. The incompatibility is deliberate: zero is not a "this peer sent no revision" fallback. |
| ACK layout | `!3sBBBHHBI` — magic, kind, flow id, generation, `ack_base`, `free_credit_frames`, `sack_bits`, `grant_revision` — then exactly 4 SACK bytes and 4 CRC bytes, 23 in total, with `sack_bits == 32`. A coherently declared 16/2-byte (21 total) or 64/8-byte (27 total) width is refused on the declared width itself, so it cannot be mistaken for a malformed old shape. |
| Revision | Unsigned 32-bit, 1..4294967295, ordered numerically. No modulus, no wrap, no masking, no zero sentinel. Exhaustion fences the direction in `grant_revision_exhausted` rather than reusing a statement that already described other bytes. |
| Snapshot | A revision binds the canonical tuple (cumulative receipt base, selective bitmap, free frame credit) **as one directional statement**. The tuple mints a higher revision when it changes; repeated, persisted and probe-answer copies of the same tuple keep theirs. A snapshot is committed only once it is on the wire, so a refused emission mints nothing. |
| Whole-ACK freshness | An older revision is ignored as an *entire* acknowledgement: no credit, no receipt retirement, no probe, silence or retry-timing change. An equal revision is idempotent for an identical tuple — it may still repair receipt, which is how a lost acknowledgement is recovered — and is refused without mutation for a different one. Advancing the cumulative base never bypasses this: the revision is checked before the receipt is applied, not after. |
| No inferred peer capacity | The sender no longer derives room from its own local limits. A fixture may establish one identical immutable startup snapshot at both endpoints, with C0 bounded by the *receiver's* free receive bytes over the fixed quantum, its reorder slots, its declared initial credit and the agreed window. Without one, the sender starts at zero, and the receiver's first statement is the explicit startup grant exchange. A last-acknowledged-DATA probe cannot bootstrap a flow that has acknowledged nothing. |
| Receiver | The snapshot covers every held reorder frame and every unconsumed delivered byte, with each held frame charged the fixed admitted maximum quantum and the result rounded down. |
| Conservative byte credit | `frames_from_byte_grant` returns `floor(byte_grant / P)` with a fixed `P`; a grant below one quantum buys nothing and is never clamped up to one. |
| Size accounting | `admitted_payload_bound` takes `min(max_payload_bytes, profile.payload_capacity)`; `CarrierProfile.payload_capacity` already subtracts the profile's own named overhead, so that overhead is not deducted twice, and `data_frame_fits` requires the quantum to leave room for the 12-byte MT05 encoding. |

One behaviour was **not** changed, deliberately. `pump(max_frames=0)` still bounds
new DATA rather than every lane, because a preserved accepted control
(`same_base_old_credit_delayed`) depends on exactly that meaning: a host that
postpones new data while current control still flows is the scenario it witnesses.
The real no-send fence belongs to the port adapter, which owns every lane, and
that is where it is implemented. Redefining `pump` here would have silently broken
a green control to fix a gap that is not in `pump`.

### Results on these bytes

| Suite | Result |
| --- | --- |
| Authored suite, `scripts/tests/test_ember_meshticulum_flow.py` | **59 passed** |
| Port controls, `scripts/tests/test_ember_meshticulum_flow_port.py` | **18 passed** |
| Rook's 22 adapted prior cases, `work/ember-meshticulum-mt05-20261004/r6/test_r3_flow_review_live_r6.py` | **22 passed** |
| Rook's eleven R4 observations, `work/ember-meshticulum-mt05-20261004/r6/probe_r4_new_live_r6.py` | **11 GREEN, 0 RED** |
| Seven discriminating witnesses on both byte sets, `work/ember-meshticulum-mt05-20261004/r6/DISCRIMINATION.py` | **7 GREEN on r6, 7 RED on the held r5 bytes** |

## The finite port — `flow_port.py`

`MeshticulumFlowOwner` is the actual finite `FlowAttemptPort`: it owns one flow's
logical admissions, its attempt bindings and its single live handle.
`FlowPortLink` is the `SimLink`-shaped face `ReliableFlow` already drives, with
`send` as the only way bytes leave, routed through the frozen scheduler's
`enqueue_from_flow` and `dispatch_one`. There is no second path.

| Ruling or clause | What the code does |
| --- | --- |
| Atomic role | `LanePolicy` maps the flow's own send kind to a `FrameRole` once, at construction. `kind="ack"` is CONTROL; everything else is DATA. No peek, no remote flag, and a frame is never relabelled. |
| One live handle | `prepare` refuses a second handle, is side-effect-free for logical accounting — no credit, sequence or attempt moves — and returns `None` for clean ineligibility. The frozen record re-derives `profile_cost` and refuses a binding that disagrees. |
| Snapshot accounting | `accept_grant` installs a byte snapshot, not an addition: `_has_slot` subtracts every unreflected logical admission plus a pending new handle before admitting another. A retransmission or probe reuses the logical admission it repeats, so it can never take a second slot. |
| Byte credit | `frame_grant_from_bytes` is `floor(bytes / P)` capped by the reorder ceiling. Zero and sub-quantum grants buy zero slots and are never clamped up to one. |
| Size accounting | `admitted_frame_bound` takes `min(contract.max_payload_bytes, profile.payload_capacity)` and deducts only overhead the profile does not know about, once. A frame past the bound is refused, never truncated. |
| Prepared-unsent | A zero or shrunken grant marks a live handle unsendable: `validate` refuses sending while `commit(REFUSED_SCHEDULER)` still reconciles it exactly once, releasing only the reserved slot and leaving attempted debt and the frame identity intact. |
| Exactly-once settlement | ACCEPTED and UNKNOWN convert the logical admission once and keep it unreflected; REFUSED_* take no credit, sequence or attempt. A duplicate completion is refused and cannot repeat state. |
| No-send fence | `settle_sending()` suppresses every lane — DATA, retransmission, acknowledgement, persistence and probe. This is the real fence; `pump(max_frames=0)` is deliberately left as "bound new DATA only" because a preserved accepted control depends on that meaning. |

The controls are `work/ember-meshticulum-mt05-20261004/r6/RAW_AUTHORED_PORT.txt`.
They run a real bidirectional transfer: a `ReliableFlow` on the port exchanging
frames with an ordinary peer over the simulated carrier, with three DATA
admissions and at least one CONTROL admission observed at the scheduler.

**Host seam, stated plainly.** `accept_grant` is where a host installs an accepted
byte-credit snapshot, and `settle_sending` is where it fences. Nothing wires
those to a live service yet, and the mocked carrier's ACCEPTED result is an
assertion by a test double, not physical evidence.

The two reviewer instruments are r5's, adapted only where the MT3 candidate
forces it — the synthetic package prefix, the fixture-established shared startup
snapshot, an explicit revision on a hand-built acknowledgement, and the MT3 magic
on the non-32-bit bitmap witness. Every scenario and every assertion is Rook's and
is unchanged; the adaptations are recorded in
`work/ember-meshticulum-mt05-20261004/r6/ADAPTATIONS.json`.

The discrimination probe is the one that shows the repairs are what moved the
measurement rather than a fixture that was quietly relaxed. Its sharpest witness
is the indirect path the review named: on the held bytes, an older revision
carrying one frame of receipt past the sender's floor retired that frame and
widened the window (`in flight` 4 → 3, `acked` 4 → 5, credit 4 → 8); on this
revision all three are unchanged.

**Not claimed.** No production wire, no authenticated bootstrap or ACK, no
control replay resistance, no full-session or compact-reference non-reuse, no
physical or RF completion, no device, no deployment. The eight-bit candidate
generation is not qualified for production non-reuse, and MT3 does not imply
Reticulum protocol compatibility. CRC checks corruption only. MT06 stays parked.

## Revision 7

Revision 6 was delivered and frozen, then independently reviewed and **held, not
approved**. The review returned seven repair groups over ten findings
(`work/meshticulum-r6-review-20261004/DECISION.json`, sha256 `bcbb15b6…4902c3`).
Revision 7 closes all seven. The held r6 bytes are preserved at
`work/meshticulum-resume-review-20261004/held-r6/`, and the evidence is not this
document's word for it: the reviewer's own frozen witnesses and this revision's
own controls were both replayed against both revisions
(`work/ember-meshticulum-mt05-r7-20261004/SOURCE_MANIFEST.json`).

| Group | What was wrong on r6 | What revision 7 does |
| --- | --- | --- |
| REPAIR01 `CF01`/`CF02` | A truthy queue admission was read as a handed-over transmission, so a merely queued frame consumed sequence, outbound bytes and pending-attempt state; and the retry timer was armed at host enqueue, so a dispatch two seconds later found an already-expired frame and invoked the carrier twice. | A link that queues without dispatching returns `DEFERRED`. Falsy, so nothing reads as sent; distinct by identity, so a deferral is not confused with a refusal. Until the real handover this endpoint consumes no byte, no sequence number and no retry deadline, and `handover()` starts the attempt at the dispatch instant. |
| REPAIR02 `PORT-R6-01` | The owner's no-send fence was never read before the carrier was invoked, but *was* required afterwards, so a pre-send stop still called the carrier once and a genuine invocation was then refused accounting for and fenced the flow with zero recorded attempts. | The lease re-reads the owner lifecycle at every validation point, including after each policy lease and immediately before the carrier, so a stop costs zero invocations. Once invoked, completion is recorded exactly once whatever eligibility does afterwards; the loss stays a fence on future sends. |
| REPAIR03 `PORT-R6-02`, `R6-ROOT-F01` | `prepare` popped the oldest queued candidate while the caller supplied the newest offer's role, so short DATA was transmitted under the CONTROL lane and the link logged the ACK's bytes as sent; and on a shared scheduler one flow logged and settled another flow's dispatch as its own. | `offer` queues the exact bytes, derives the lane from the flow's own send kind and **arms** that one candidate; `prepare` may only materialize the armed candidate and refuses anything it did not arm. `dispatch_queued` matches the scheduler's returned frame identity against the frame this link queued, and never logs, reports or settles a foreign dispatch. |
| REPAIR04 `PORT-R6-03`, `PORT-R6-05` | The owner's byte snapshot was bounded only by reorder slots, so a 1024-byte grant became 16 slots under a 128-byte window, and nothing rebased the owner's unreflected admissions when the flow accepted a canonical ACK. Separately, "room for a new reservation" and "does the existing reservation still fit" were the same strict comparison, so an exactly-fitting live handle was cancelled. | A grant wider than the negotiated window/unconsumed ceiling is refused, never clamped. An accepted canonical ACK is offered to the owner before either side commits, so receipt and credit move in one call, and a snapshot the owner refuses refuses the whole acknowledgement with nothing moved. Fit and room are separate predicates, so an identical repeat is idempotent and a shrink invalidates only what no longer fits. |
| REPAIR05 `PORT-R6-04` | Every refused offer appended another candidate and pre-registered another logical identity, so 1000 blocked offers retained 1000 frames and 16000 bytes against a 4096-byte budget; and a credit-blocked DATA at the head suppressed acknowledgement progress behind it. | An identical pending emission is the same emission, so repeating it neither grows the queue nor mints an identity, and the queue is capped by the negotiated byte budget and the frames it buys. Eligible control progress no longer depends on a DATA slot. |
| REPAIR06 `R6-CR-F01` | Losing the first startup ACK left no event and no bound: the receiver stated its snapshot once, and a sender with no acknowledged DATA had nothing to expire and no eligible probe. After 60 virtual seconds the flow was still open with nothing scheduled. | The receiver re-announces its first canonical statement a bounded number of times, bounded also by elapsed time so a link that refuses the emission outright cannot spend an infinite budget, and then fails the direction. The sender's wait for a first grant is bounded too. Both fail inside ten virtual seconds instead of idling. |
| REPAIR07 `R6-CR-F02` | The shared startup snapshot accepted base 1, a non-empty bitmap, revision 2 and a base outside the candidate wire range, and a credit the receiving endpoint could not actually hold. | The snapshot must be the canonical initial state — base zero, empty bitmap, startup revision — and a credit above the receiving endpoint's own capacity, or above the explicitly agreed trusted capacity, is refused at construction. |

**Two review probes disagree with this revision deliberately, and are reported
rather than smoothed over.** The reviewer's `queued_data_as_control` probe
requires that a credit-blocked DATA be the frame dispatched when a later ACK is
offered; revision 7 dispatches the ACK that was actually requested, under its own
role, and leaves the DATA queued for the flow's next transmit. Both avoid the two
r6 failure modes the paired control names, and the probe's own consistency clause
(`link.sent[-1] == actual.payload`) now holds; the remaining difference is which
frame is selected, and the reviewer's ruling is what decides it. The reviewer's
`CR-06` case refuses at construction rather than at runtime, so its harness
records ERROR where the refusal is the thing that was asked for.

**Not claimed.** No production wire, no authenticated bootstrap/DATA/ACK, no real
worker, broker or service authority, no trusted persistence, no incremental
stream or half-close, no physical completion, device, radio, RF, network or
deployment evidence. Revision 7 is source continuation inside the finite
synthetic subset; it is not MT05 acceptance, and MT06 stays parked.

## Revision 8

Revision 7 was reviewed independently and **held** on the three findings below.
Revision 8 repairs all three, and the repair is narrow: nothing else about the
wire, the credit contract or the port rulings moved.

| Finding | What was wrong | What the repair does |
| --- | --- | --- |
| R7-F01 | A retry queued at t2 and dispatched at t4 spent nothing. The owner recorded attempt two while the flow still had attempt one, `sent_at=0`, no RTO backoff and no retransmission, so the next `pump(0)` started a third attempt immediately. `handover()` only accepted the next new sequence and had no way to settle an existing pending retry. | `handover()` settles the exact attempt a handover describes. A deferred retransmission spends one attempt, one new retry clock, one RTO backoff and one retransmit **at the real dispatch instant**, and the synchronous expiry path and the deferred path share `_settle_retry` so they cannot disagree. |
| R7-F02 | A credit probe queued at t1.1 and dispatched at t2.1 was charged to the owner as a second RF attempt while the flow recorded zero probes, zero silence attempts and a `probe_at` already in the past. | A queued probe is marked as such by the flow that queued it, and its handover spends exactly one bounded silence attempt with the next probe deadline measured from the real dispatch. No sequence number, byte or DATA slot is admitted, and no probe payload is invented. |
| R7-F03 | A real dispatch that did not go through this link never came back to it. A host `dispatch()`, a shared scheduler serving two flows, and a confirmed-unsent refusal each left a stale hold with the sequence still at zero, all 48 outbound bytes and no flight — one shared case really sent and committed both flows and still reported neither. | The owner routes every settled frame to the links registered with it, identified by **exact frame bytes** rather than by frame id, because the owner and the scheduler mint frame ids in separate colliding spaces. A host dispatch this link never made, a foreign scheduler selection and a normal host-delayed dispatch all settle the real originating transaction, and a known-unsent refusal releases the hold so the flow can re-offer. |

Two ordering facts make that repair correct rather than merely green, and both are
asserted in the suite. A frame the flow sent synchronously has already spent its
byte and sequence, so the link must not report that same emission again while it is
still inside the flow's own `send`: the flow records every sequence it consumed and
refuses a repeat report, and the retry and probe branches are settled **before**
that check, because a retry legitimately reuses the sequence number of the frame it
repeats. A probe is likewise indistinguishable from the retry of the same bytes by
`(sequence, payload)` alone — a probe repeats the frame it is acknowledging — so the
flow, not the link, marks the emission that is outstanding.

### Revision 8, second round — the deferred record itself

Revision 8 was reviewed and **held again** on two residual findings, both of them
in the bookkeeping this revision introduced
(`work/meshticulum-interim-review-20261005/DECISION.json`, sha256
`440d59f75e5fcd9d6a0cbcf616d5096cdd22310a758fd8fc11b5d190f12d87ad`). Both had one
cause: a record created on the `DEFERRED` path but removed only by a *different*
path than the one that actually terminates the emission.

| Finding | What was wrong | What the repair does |
| --- | --- | --- |
| R8-F01 | Repeating an offer for the **same still-queued** emission was not idempotent. 1000 identical `pump(t2, max_frames=0)` calls appended 1000 records against a single held 63-byte frame, and one real accepted retry removed exactly one, stranding 999. | One queued reservation carries exactly one outcome record. The record for a sequence is appended only when those exact bytes are not already outstanding, so however many times the flow re-offers a held frame the count stays at one, and the single real handover consumes it. |
| R8-F02 | A known-unsent `REFUSED_CARRIER` released the link's hold but never cleared the flow's record, because the port's terminal path and the flow's record live on opposite sides of the seam. One offer left one stale marker; 1000 left 1000. | The port reports an abandoned handover on exactly the terminal outcomes that are *not* a handover, and the flow retracts the matching record. Nothing else moves — no attempt, no retry clock, no retransmission, no DATA byte — and the flow still owns its frame and re-offers it unchanged. |

One detail is worth stating because it is the part that was wrong on the first
attempt: the record is keyed by the **application bytes the frame carries**, while
the link holds the **encoded frame**, so retraction cannot match on identity or on
the raw value. The comparison re-encodes the record's own payload exactly as the
retry lane encodes it, and the suite asserts that the two objects genuinely differ
so the fix cannot silently rot back into an identity check.

### Results on these bytes

| Evidence | Result |
| --- | --- |
| Rook's expanded outcome controls, `work/meshticulum-r7-review-20261004/EXPANDED_CONTROLS.py`, on the held r7 snapshot | **7 GREEN / 7 RED / 0 ERROR**, reproduced before editing a byte |
| The same controls on these bytes | **14 GREEN / 0 RED / 0 ERROR** |
| Rook's repeated-deferred controls, `work/meshticulum-interim-review-20261005/REPEATED_DEFERRED_CONTROLS.py`, as delivered against the frozen first-round r8 | **1 GREEN / 3 RED / 0 ERROR** |
| The same controls after this round | **4 GREEN / 0 RED / 0 ERROR** |
| All eleven Meshticulum suites | **1130 passed, 0 failed** |
| Authored flow and port suites | **65 passed** and **48 passed** |
| The seven cases added in the first round, against the held r7 bytes | **7 failed** — they discriminate |

### Revision 8, third round — the probe's own record

The second round repaired the retry lane's record and left the probe lane's record
alone, which is the same defect one lane over. Reviewed and **held** on that single
residual (`work/meshticulum-r8b-review-20261005/DECISION.json`, sha256
`4c7730facb345e299ef68a0724d55582e4f9653c5daf7cbf18c6ca5fcb9b79e1`).

A credit probe queued under a live handle sets `_probe_outstanding`, and the
terminal outcomes that are *not* a handover retracted only the retry lane's
record. So a refused probe kept an obligation open for a frame that would never
leave, and the next real handover for that sequence was matched by the probe
branch instead: the flow's fresh DATA frame took the abandoned probe's identity,
the radio never made the probe attempt the flow charged, and the DATA stayed
unaccounted.

| Finding | What was wrong | What the repair does |
| --- | --- | --- |
| R8B-F01 | A known-unsent credit probe (`REFUSED_CARRIER`, or a scheduler refusal before the carrier was invoked) cleared the link's hold but left `_probe_outstanding` set with no new attempt. The later canonical ACK and fresh DATA then reported `next_sequence=1`, `outbound_bytes=48`, `in_flight=0` with `probes=1` and `probe_attempts=1` — the probe was charged an attempt it never made and the DATA was classified as that probe. | The port offers the abandoned handover to the probe lane as well, and the flow retracts the exact pending probe record. The record is matched on the probe's own bytes *and* sequence, not on the sequence alone, so an unrelated or still-live probe obligation is never cleared. No attempt is spent, no probe clock moves and no DATA byte is admitted. |

The probe interval already armed stays where it is, so a refused probe is asked
again on the flow's own schedule rather than immediately. The positive controls
are unchanged and still green: an **accepted** and an **unknown** probe each
spend their one bounded silence attempt and let the later DATA advance to
`next_sequence=2`, `outbound_bytes=0`, `in_flight=1`, with no probe attempt
recorded after the ACK.

### Results on the third round

| Evidence | Result |
| --- | --- |
| Rook's probe-refusal controls, `work/meshticulum-r8b-review-20261005/PROBE_REFUSAL_CONTROLS.py`, on the held r8b snapshot | **2 GREEN / 4 RED / 0 ERROR**, reproduced before editing a byte |
| The same controls on these bytes | **6 GREEN / 0 RED / 0 ERROR** |
| The two authored regression cases added this round, against the held r8b bytes | **2 failed** — they discriminate |
| The same two cases on these bytes | **2 passed** |
| Authored flow and port suites | **65 passed** and **50 passed** |

The frozen dependencies are unchanged from the second round: all nine files under
`meshticulum_contracts/` and `meshticulum_scheduler/` are byte-identical to Rook's
frozen snapshot. Hashes and the repaired-versus-held mapping are in
`work/ember-meshticulum-r8c-20261005/MANIFEST.json`.

The frozen dependencies are unchanged: all nine files under
`meshticulum_contracts/` and `meshticulum_scheduler/` are byte-identical to Rook's
frozen r7 snapshot, 9/9 identical and 0 differing. Nothing in the frozen scheduler
API, the ordinary leases, the carrier routing or the lifetime uncertainty debt was
altered. Hashes, the repaired-versus-held mapping for every group and the exact
counts are in `work/meshticulum-r8-ember-20261004/MANIFEST.json`.

### Revision 8, fourth round — the slot that went to another flow

The third round repaired both lanes' records for emissions that reached a terminal
outcome without leaving. Rook's review of those exact bytes found a third case,
which is present on r8b too
(`work/rook-meshticulum-r8c-review-20261005/DECISION.json`, sha256
`94c2e5a21f10146976ff7376f457412b2a9a17c17e1fe6284b22d456d8dc0643`).

A synchronous offer asks the shared scheduler for the slot and reports whether the
carrier took **this** link's frame. When the scheduler gave the slot to a different
flow, the offer came back `False` — the same value as a refusal — while this link's
frame stayed queued under its own live handle. The flow therefore recorded no
deferred probe or retry for an emission that was still waiting, and when the central
scheduler dispatched it later the handover settled nothing: the owner and the link
both counted the real attempt, while the flow's probe counters stayed zero or its
retransmission stayed at the original attempt and the original clock.

| Finding | What was wrong | What the repair does |
| --- | --- | --- |
| R8C-F01 | A probe or retransmission offered synchronously while another flow held an earlier queued frame was reported as refused, because the link's `False` covered both "the scheduler dispatched someone else and your frame is still held" and "your frame reached a terminal unsent outcome". The later real dispatch then found no record for the emission and settled no attempt, probe clock or backoff. | `send()` separates the two outcomes it already knew apart. A frame still held under this link's own live handle is reported `DEFERRED`, the state the host switch already reports; only a hold the scheduler actually released without a handover is a refusal. The flow keeps the byte, sequence and retry clock it has not spent, records its one outcome, and settles it on the real handover. |

The negative control is deliberate: a terminally unsent retry is still a refusal, is
still not logged as sent, and still retracts its record, so the repair cannot be
"every false is a deferral".

### Results on the fourth round

| Evidence | Result |
| --- | --- |
| Rook's shared-outcome controls, `work/rook-meshticulum-r8c-review-20261005/SHARED_OUTCOME_CONTROLS.py`, on the held r8c snapshot | **8 GREEN / 4 RED / 0 ERROR**, reproduced before editing a byte |
| The same controls on these bytes | **12 GREEN / 0 RED / 0 ERROR** |
| The six authored regression cases added this round, against the held r8c bytes | **5 failed, 1 passed** — they discriminate, and the refusal control is preserved |
| The same six cases on these bytes | **6 passed** |
| Rook's probe-refusal, repeated-deferred, expanded and adjacent control sets | **6 / 4 / 14 / 18 GREEN, 0 RED, 0 ERROR** |
| The inherited credit, port, transaction and shared-scheduler instruments | **22 / 13 / 19 / 2 GREEN, 0 RED, 0 ERROR** |
| Authored flow and port suites | **65 passed** and **56 passed** (121 together) |

Three of the eight announced files changed this round: `flow_port.py`, the port
suite and this README. The other five, and all nine files under
`meshticulum_contracts/` and `meshticulum_scheduler/`, are byte-identical to the
revision Rook reviewed. Hashes and the full frozen manifest are in
`work/ember-meshticulum-r8d-20261005/MANIFEST.json`.

**Not proven, and not claimed.** Everything above is finite synthetic source and
host review with virtual clocks and mocked carrier, service and broker leases. It
establishes no production or authenticated wire, no real worker or broker
authority, no incremental stream or half-close, no physical, RF, LoRa, device, live
traffic or deployment evidence, and no release acceptance. MT05 acceptance remains
Rook's independent review, and MT06 remains parked. The 1130 passing tests are this
revision's own reported result, not independent acceptance.

## Fifth round: the queued acknowledgement's canonical outcome

Rook's independent review of the fourth round held it on one remaining P1, R8D-F01:
a queued acknowledgement lost its canonical settlement. Because the shared
scheduler serves one slot at a time, a legitimate DATA frame from another flow can
take the dispatch the receiver's acknowledgement was queued for. The
acknowledgement then really leaves on a later central dispatch — the owner and the
link both record the handover — but the receiver that minted its revision never
settled it. The revision stayed bound to the state it had been minted for, so the
next changed snapshot reused it, and the peer refused the newer statement as a
conflicting grant.

The gap was structural rather than local. A control frame is queued without a
sequence number, and the handover callback returned for any hold that had none, so
there was no way for the emitting flow to hear about a control handover at all.
Worse, the acknowledgement path had no record to settle even if it had heard:
`_emit_ack` committed the snapshot, revision, clock and bounded counters only on a
truthy synchronous send, and a queued emission is not that.

| Finding | What was wrong | What the repair does |
| --- | --- | --- |
| R8D-F01 | A current-credit acknowledgement was queued, a foreign dispatch took the slot, and the acknowledgement then left on a later central dispatch with its revision uncommitted. The receiver kept `last_ack_at=-1` and a dirty snapshot while the peer accepted the emitted revision; the next changed snapshot reused that same revision, so the peer counted a conflicting grant and kept the stale allowance. | The flow keeps one exact record of the queued emission — the sealed bytes, the canonical snapshot they state, the revision minted for them — and commits that snapshot, that revision, the real dispatch clock and the applicable bounded counters exactly once when the frame actually leaves. A host-settled unsent outcome retracts the record without minting a revision or replenishing a grant, so the statement is re-offered unchanged. The link routes a control handover to the flow by exact bytes, and the commit has one implementation shared by the synchronous and delayed paths. |

Committing at enqueue is deliberately not the repair: it would mint canonical
state, spend the announcement budget and start the acknowledgement clock for a
frame the carrier may never take. Both confirmed-unsent controls refuse it.

### Results on the fifth round

| Evidence | Result |
| --- | --- |
| Rook's own witness, `work/rook-meshticulum-r8d-review-20261005/semantic-review/ACK_REVISION_CONTROLS.py`, on his frozen candidate **and** on held r8c | **4 GREEN / 2 RED / 0 ERROR**, reproduced identically before editing a byte |
| The same witness on these bytes | **6 GREEN / 0 RED / 0 ERROR** — `shared_accepted` and `shared_unknown` repaired, all four positives still green |
| The five authored cases added this round, against the exact reviewed r8d source | **3 failed, 2 passed** — they discriminate, and both positive controls are preserved |
| The same cases on these bytes | **5 passed** |
| Rook's shared-outcome, probe-refusal, repeated-deferred, expanded and adjacent controls | **12 / 6 / 4 / 14 / 18 GREEN, 0 RED, 0 ERROR** |
| The inherited credit, port, transaction and shared-scheduler instruments | **22 / 13 / 19 / 2 GREEN, 0 RED, 0 ERROR** |
| Authored flow and port suites | **65 passed** and **61 passed** (126 together) |
| The Meshticulum-keyed lane, four environment-blocked modules ignored | **1180 passed, 2 skipped** |

Four of the eight announced files changed this round: `flow.py`, `flow_port.py`,
the port suite and this README. The port suite changed only by the five cases
added above, and the README changed only by this section plus the fourth-round
count sentence, which now reads three of eight — what that round's manifest and
diff actually showed. All nine files under `meshticulum_contracts/` and
`meshticulum_scheduler/` are byte-identical to the revision Rook reviewed, so no
scheduler API, lease, carrier routing or deadline moved. Hashes and the full
frozen manifest are in `work/ember-meshticulum-r8e-20261005/MANIFEST.json`.

**Not proven in the fifth round either.** The same finite synthetic boundary
applies: virtual clocks, a mocked carrier and mocked service and broker leases, no
radio, socket, serial port, device, live traffic or deployment, and no release
acceptance. The peer's acceptance of the newer revision is a mock assertion about
bytes on a simulated wire, not evidence that a physical peer received anything.
MT05 acceptance stays Rook's, and MT06 stays parked behind it.

## Sixth round: the older acknowledgement's bounded settlement

Rook's independent review of the fifth round confirmed the queued-acknowledgement
repair and held it on two remaining P1s. Both are about what a committed
acknowledgement is allowed to settle, and both were reproduced from his frozen
instruments before a byte was edited.

R8E-F01 is the narrower half of the fifth round's own finding. That repair made a
queued emission commit the snapshot and revision it actually stated, but the commit
also cleared `_ack_dirty` and `_reassert_pending` unconditionally. Receive work
raised *after* the emission was sealed — a later frame, a completed transfer, a
consume — was therefore retired by a statement minted before it existed. The peer
sends its single final DATA frame while an ACK for base0/credit8/revision2 waits
behind another legitimate flow; the receiver reaches COMPLETE at base1 with its
credit still 8, so neither the credit comparison nor the persistence branch
recreates the lost work, and the next eligible pump sends nothing. The peer stays
open with one frame in flight. Equal credit does not make two acknowledgements
equal: the cumulative base and the selective bitmap move independently of it.

R8E-F02 was found by the adversarial reviewer. `build_ack_from_wire` rebuilt a
queued emission without the owning limits, so `build_ack_frame` fell back to
`FlowLimits(seq_modulus=...)` and its default reassembly ceiling. A valid flow
configured with a *larger* ceiling than the default advertises credit the rebuild
then refuses as out of range, and because the callback raises rather than returning
a value, the owner's outcome is left unresolved.

| Finding | What was wrong | What the repair does |
| --- | --- | --- |
| R8E-F01 | A queued ACK for an older snapshot committed that snapshot correctly but also cleared `_ack_dirty` and `_reassert_pending`, discarding receive work raised after the emission was sealed. With credit unchanged across the two statements, nothing recreated the work: the peer stayed OPEN with 1 in flight and 0 acknowledged. | Every route that makes an acknowledgement necessary advances one monotonic receive-work counter, and a queued emission records the value it was sealed at. The commit still settles the recorded snapshot and revision unconditionally — that emission left and minted that revision — but retires the dirty and reassert flags only when nothing has been raised since. A superseded emission therefore leaves the newer statement owed, and the next eligible pump emits it. The synchronous path passes no generation, because no such window exists there. |
| R8E-F02 | `build_ack_from_wire` called `build_ack_frame` without `limits`, so the constructor's defaults replaced the flow's own configuration. A flow with a reassembly ceiling above the default had its own valid emission refused mid-callback, fencing an otherwise definite settlement. | The owning limits travel with the comparison. `build_ack_from_wire` takes them as a keyword argument and both callbacks pass `self.limits`, so the rebuild validates the credit against the configuration that actually minted it. Only the exact recorded bytes are ever compared. |

Two details are deliberate. The record of a queued emission stays the positional
tuple the review boundary already indexes, with the generation appended fifth
rather than inserted, so a reader that indexes the first four still finds what it
expects. And the generation is advanced by every work-raising route — consume,
stall, unstall, startup re-announcement, buffer, reassert, delivery and the credit
change detected inside `_emit_ack` — because any of them can make a newer statement
necessary.

### Results on the sixth round

| Evidence | Result |
| --- | --- |
| Rook's ACK adjacent controls, `work/rook-meshticulum-r8e-review-20261006/ACK_ADJACENT_CONTROLS.py`, against the frozen reviewed r8e bytes | **20 GREEN / 2 RED / 0 ERROR** — `final_before_accepted` and `final_before_unknown` fail exactly as his report says |
| The same instrument on these bytes | **22 GREEN / 0 RED / 0 ERROR** — both controls now report `newer_dirty_survives_older_handover`, `newer_final_revision`, `final_ack_emitted` and `peer_completes` |
| Rook's small-modulus controls, `independent-confirmation/SMALL_MODULUS_CONTROLS.py`, on the frozen r8e bytes | **18 GREEN / 6 RED / 0 ERROR** |
| The same instrument on these bytes | **24 GREEN / 0 RED / 0 ERROR** |
| The inherited R8D-era instruments on both arms | ACK witness **6 GREEN / 0 RED** and shared lifecycle **42 GREEN / 0 RED**, identical on held and repaired bytes — no fifth-round positive moved |
| Authored flow and port suites | **130 passed** (126 inherited, unchanged, plus 4 new permanent controls) |
| The frozen r8e harness resolving against each arm | frozen arm loads `flow.py` `b8868852…`, candidate arm loads `12fef493…`; the arm identity is recorded rather than assumed |

Three files changed this round: `flow.py`, the port suite and this README. The
flow suite is byte-identical to the reviewed revision — the four new controls all
belong to the port suite, because both findings are about the port-bound
acknowledgement path. `flow_port.py`, `carrier.py`, `mux.py` and all nine files
under `meshticulum_contracts/` and `meshticulum_scheduler/` are likewise
byte-identical to the revision Rook reviewed, so no scheduler API, lease, carrier
routing or deadline moved. The four new controls are
`test_an_older_queued_ack_cannot_retire_newer_receive_work` (both outcomes) and the
two `small_modulus` cases. Hashes, the frozen predecessor and the paired runs are
in `work/ember-mt05-r8f-20261009/`.

**Not proven in the sixth round either.** The same finite synthetic boundary
applies: virtual clocks, a mocked carrier and mocked service and broker leases, no
radio, socket, serial port, device, live traffic or deployment. The peer's
acceptance of the newer revision is a mock assertion about bytes on a simulated
wire. The reported `flow_attempt_commit_uncertain` fence is a property of the real
frozen scheduler driven by those mocks, not a statement about a production worker
or broker. MT05 acceptance stays Rook's, and MT06 stays parked behind it.

## Why it exists

`TASKS.md` MT05 asks for two things before any of it touches a radio: an
isolated candidate for ordered bidirectional flows with generations, bounded
windows, selective acknowledgement, retry deadlines, receiver credit and
backpressure; and a deterministic harness that can produce delay, loss,
duplicate, reorder, corruption, wraparound, asymmetry, route switch and peer
restart on demand.

Both are here, as pure simulation. The point is to have the failure cases
executable — and cheap to re-run — before a carrier is attached, because the
carrier's own acceptance (`MT12`) has to distinguish a flow bug from a radio
bug and this is the half that removes the radio.

## Layout

| File | What it is |
| --- | --- |
| `carrier.py` | The simulated carrier: seeded faults, a virtual clock, a serial radio whose capacity actually costs time, bounded queues, a reverse-link ACK allowance, and two costs modelled apart. |
| `flow.py` | One endpoint of one flow: flow id and generation admission, modular sequence space with wraparound, selective repeat, RTT-derived retry deadlines, receiver credit, bounded queues, bounded reassembly and bounded unconsumed delivery, honest teardown. The wire format is a candidate for `MT03` to accept or replace. |
| `flow_port.py` | The finite MT04 port binding: one flow's logical admissions, live handle and exactly-once settlement, plus the link that routes every lane through the frozen scheduler. |
| `mux.py` | Deficit round robin plus flow-id demultiplexing, so several flows share one carrier and one stalled flow cannot stall another. A placeholder for `MT04`'s real scheduler. |

Run the suite from the repository root:

```
PYTHONPATH=.:software/runtime/src python3 -m pytest scripts/tests/test_ember_meshticulum_flow.py -q
```

## Rules this code keeps

* **Admission is ordered and explicit**: flow id, then generation, then
  direction eligibility, then terminal state. A generation this endpoint did
  not accept is refused whatever else the frame says, and nothing is widened
  silently — an out-of-range value is an error, not a wrap.
* **Terminal is terminal.** A completed, failed or cancelled direction does not
  absorb, deliver or re-complete. The one exception is deliberate: a duplicate
  of an already-admitted frame is answered with current receipt and credit,
  because that is how a lost acknowledgement is repaired.
* **Bounded failure is a result.** An unrecoverable carrier ends in `failed`
  with a reason, inside a bounded number of retries. There is no path where a
  missing byte reads as a delivered one.
* **A cancelled flow stops speaking.** Frames already handed to the radio can
  still arrive; nothing after the cancel can be transmitted.
* **Both ends stay bounded whatever the peer does.** A receiver that never
  consumes advertises zero credit and then refuses; a sender that ignores
  credit is refused by the receiver's window rather than growing its buffer.
* **An equal-base acknowledgement is ordered, not trusted.** The cumulative floor
  refuses an ACK that names an earlier position; the grant revision orders the ones
  that name the same position, because a consume grant and a stale snapshot are
  otherwise indistinguishable and the second can move credit *upward*.
* **The two acknowledgement costs stay separate.** `firmware_ack_bytes` is
  charged per data frame handed to the radio; the reverse-link allowance covers
  overlay ACK frames only. Aggregating overlay ACKs cannot be reported as
  removing the firmware's own per-frame cost.

## What this is not

* Not a radio, thermal, duty-cycle or range result. Every number here comes
  from a simulated clock; none of it is goodput, latency or capacity evidence.
* Not the frozen contract, and not a scheduler. The frame layout is a proposal
  for `MT03`; the multiplexer is a placeholder for `MT04`, and the
  reserve/commit seam between them is not implemented here.
* Not MT01-aware. The frozen logical-versus-attempt identity is an input this
  layer has not consumed.
* Not multipath or bonding. A flow is pinned to one carrier, per the report's
  "pin a flow to one lane and perform explicit failover".
* Not MT06. Nothing here validates a Lamplighter manifest, a pack-v2 chunk or
  durable resume state.
* Not an authorization boundary. The simulation's CRC is corruption checking
  only, never authentication.

