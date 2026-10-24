# Host-backed MCU acceleration

The first acceleration step is a bounded DATA ciphertext queue on the MCU. Linux
keeps the transport session and its keys. The MCU can schedule several already
paid packets without waiting for a Python/USB round trip after every driver
completion.

This change is a **source-only prototype**. The queue header is not included by
`main.cpp` or the firmware build recipe. The Python module has no serial access,
wire opcode, grant issuer, durable journal or driver. Nothing here installs a
capability, changes a service, or establishes improved RF performance.

## Why this boundary

The [24 October distance sample](../benchmarks/2026-10-24-lora-distance/results.json)
delivered 8,164 useful bytes over about 20 m using two Heltec V4 USB radios,
SF7/BW500/CR4:5 at 921.375 MHz and 0 dBm. The final receipt was authenticated and
the publisher and whole-file hash were independently verified. There were 46
DATA packets, no retries and no receive-drop counter increases.

| Measurement | Result |
| --- | ---: |
| Sender startup to authenticated receipt | 9.183714 s; 888.965 B/s |
| First DATA worker call to authenticated receipt | 8.001248 s; 1,020.341 B/s |
| DATA worker calls, summed | 6.134363 s |
| DATA driver calls, summed | 4.984246 s |
| Median DATA worker / driver call | 132.347 / 108.781 ms |
| Median estimated DATA airtime | 99.904 ms |

The worker-minus-driver difference is 1.150117 s across DATA calls. It includes
host and boundary work; this sample does not attribute every millisecond to a
specific component. Driver elapsed time also includes driver work and is not an
independent RF airtime measurement. Removing that whole difference would be an
optimistic arithmetic counterfactual, not an achieved speedup.

A separate CPU-only diagnostic ran 128 durable reserve/NOT_SENT-settle pairs per
endpoint. Sender reserve and settle medians were 0.460 and 0.419 ms; receiver
medians were 0.460 and 0.433 ms. A paired median was not measured. No transport or
radio was opened. This does not establish journaling as the
main delay. The useful next experiment is to remove repeated dispatch waits
while preserving durable accounting and the receive path.

## Responsibility split

| Linux host | MCU queue and trusted adapter |
| --- | --- |
| Session keys, AEAD, nonce allocation and exact ciphertext | Store exact DATA ciphertext, at most four packets |
| Peer ACK authentication, credit and retry decisions | Check immutable admission and independent airtime estimate |
| Durable attempt/airtime reservation before any queue write | Charge shared boot-lifetime attempt/airtime counters once |
| Publisher, whole-file and application completion | Start one driver operation at a time within the short deadline |
| Reconcile every pending physical result and uncertain write | Preserve RX priority and emit exact local physical evidence |

Phase 1 accepts only DATA from a locally established role. A remote priority
label cannot turn ACK or control traffic into DATA. ACK snapshots and control
traffic retain their synchronous host path. The MCU receives no session keys,
does not authenticate peer ACKs, and does not retry automatically.

## Queue contract

The dormant native state machine is
[`experimental/tx_burst.hpp`](../firmware/meshticulum-bench/experimental/tx_burst.hpp).
The offline Python descriptor and evidence checks are
[`bench/tx_burst.py`](../src/meshticulum/bench/tx_burst.py).

Every item binds the current 16-byte MCU boot, an immutable grant/profile context
digest, monotonic request ID, DATA role, exact ciphertext length and SHA-256,
airtime estimate and absolute MCU deadline. The context digest must cover the
exact admitted grant and encoded radio profile; the admission must already bind
the authorized peers and lane. A matching digest is an identity check, not proof
that a grant is valid or current. The future adapter must verify authority and
hash the actual ciphertext rather than trusting host-supplied metadata.

The first item fixes a common burst deadline. It must be no later than the
original absolute MCU admission deadline, and at most 2,000,000 microseconds from
admission. Subsequent items cannot renew that window. At admission, every pending
item, including an active item conservatively, contributes its independently
computed airtime plus a 100,000 microsecond start guard. The total must fit before
the common deadline. Each issue repeats its own remaining-time check.

Depth is at most four **including retained terminal outcomes**, and a burst has
at most four admissions even if an outcome is acknowledged early. Its admission
count and common deadline reset only when all retained slots are acknowledged.
An exact retained replay returns its existing state without charging or extending
the deadline. Changed metadata or bytes conflict. A released request remains
stale under the same physical boot.

The native `BootState` outlives queue objects and admission changes. It owns the
shared cumulative counters, paid floor, request high-water mark, monotonic clock
watermark, sticky fence, containment requirement and queue ownership. Every other
radio path must use the same state. Recreating a queue, rebinding a grant or
clearing a slot cannot reset debt, reuse an old request, or erase uncertainty.
Retained packet outcomes belong to the queue object, so it must stay alive until
acknowledgment, normally for the physical boot. Destroying it with retained work
fences the boot state but does not preserve detailed outcomes. Destruction or a
physical reset therefore requires separate reconciliation.

A native issue requires no active operation, no pending RX evidence, and room to
report evidence. It marks `ISSUED` before returning the packet to a driver
adapter, and never calls a radio itself. The adapter must drain RX_DONE/USB receive
evidence before the next claim and preserve the admitted reverse listen window
after the finite burst. The 100 ms guard is a time allowance, not implementation
of that receive window. Queueing must not starve ACK reception or bypass RX
backpressure.

| Local evidence | Meaning |
| --- | --- |
| `QUEUED` | Exact ciphertext retained and charged; no driver handover established |
| `ISSUED` | Claimed for the driver; absence of later reports cannot prove it was unsent |
| `STARTED` | Driver start evidence; does not establish RF completion or receipt |
| `RF_DONE` | Matching successful local TX_DONE driver evidence; no peer receipt claim |
| `NOT_SENT` | Definite cancellation/expiry before issue; paid debt remains |
| `UNKNOWN` | An issued/started operation cannot be resolved; sticky fence and physical containment required |

Queued cancellation/expiry becomes `NOT_SENT`. Cancellation/expiry after issue
becomes `UNKNOWN`. No outcome refunds attempts or reserved airtime. Exact terminal
replay settles once. Late exact RF_DONE may account an earlier issued operation
if no terminal uncertainty has already been declared; it cannot authorize another
send. Once UNKNOWN is recorded, a later RF_DONE does not rewrite it or lift the
fence. A failed containment attempt must be retried, and acknowledging an UNKNOWN
slot must wait for containment. Clearing a containment requirement does not clear
the sticky fence.

The Python evidence history can accept a terminal report when intermediate
reports were lost. It validates shape and consistency, not provenance or driver
truth. It is bounded in-memory state, not a recovery journal. `RF_DONE` and an
empty pending set never establish authenticated transport or application success.

## Offline descriptor

`QueueDescriptor.encode()` is a canonical big-endian descriptor with magic
`BTD1`: a 77-byte header followed by one to four 55-byte packet descriptors. It
contains bindings and metadata and deliberately excludes ciphertext. A four-item
descriptor is 297 bytes. Decoder checks include exact size, count, DATA role,
nonzero identity digests, increasing request IDs, common deadline and aggregate
time allowance. Evidence stage values agree with the native enum, but this is
not a native structure serialization or a deployed serial ABI.

Python evidence includes an event timestamp while native evidence does not. A
future adapter must attach a stable boot-specific event time or retain the first
normalized observation: generating a new timestamp on every USB replay would
turn an exact native replay into conflicting host terminal evidence.

`check_admission()` and `check_dispatch()` compare supplied independent estimates
and remaining time. They do not derive airtime, map host clocks to the MCU clock,
consult global debt, enforce a live lease, or authorize a driver operation. The
future protocol needs a checked boot-specific MCU clock mapping with bounded
uncertainty; copying a host timestamp into a descriptor is insufficient.

## Integration gate

Before a queue write, Linux must durably reserve each exact request, ciphertext,
length, digest, estimate and immutable binding. It must retain unresolved physical
records independently of flow retirement. A partial or ambiguous write remains
charged and fenced on restart until reconciled; neither a missing acceptance
report nor a peer ACK proves that an outstanding item was not sent. MCU evidence
and counters must reconcile with the durable host journal without refunding
cancellation or double charging an exact replay. The prototype provides no queue
writer that can bypass this requirement.

The current scheduler permits one live attempt handle per flow, with a dispatch
lease held through one commit. Generic
`CommitOutcome.ACCEPTED` is a carrier disposition, not inherently RF proof; the
current BenchFlow adapter returns it only after RF_DONE. In today's synchronous
path, retry timing uses the original pre-call pump timestamp rather than a
measured RF_DONE time. A reviewed DATA queue adapter may be able to use that
existing carrier-acceptance contract without changing frozen flow modules. It
must make the altered handover explicit and preserve the following obligations:

- Keep physical records pending after carrier acceptance, including after a
  transport handle retires or an authenticated peer ACK arrives.
- Include the maximum bounded queue wait and preceding radio operations in RTO,
  or derive timing from validated dispatch evidence. Validate the frozen host
  handover and retry timestamp model explicitly with queue latency; an early
  timestamp could otherwise make retries premature.
- Prevent retry of the same logical DATA while its earlier physical handover is
  pending. `NOT_SENT` after accepted carrier handover is a physical result; it
  does not undo carrier accounting.
- Drain or settle all physical results before reporting successful closure.
  Expiry and cancellation also require reconciliation and containment of an
  uncertain active radio operation.
- Keep ACK canonical snapshots on their synchronous actual-handover path until
  their freshness and revision semantics receive separate review.

There is a deployment-policy decision beyond queue correctness. Releasing a host
service, broker or dispatch lease at queue acceptance delegates later RF starts.
A boot/context digest and short deadline cannot preserve instantaneous host
revocation while USB is disconnected. Integration must explicitly choose either
an irrevocably charged in-flight carrier handover with an approved short
cancellation/autonomy bound, or a host authorization check before each RF start.
This prototype grants neither a revised deployment policy nor permission to
weaken the current live-at-RF-start checks.

Next work is a reviewed capability-negotiated adapter, checked admission
receipt provenance, checked clock/deadline mapping, durable recovery and drain
integration, and RX/backpressure fault tests. Reproducible firmware, rollback and
a fresh finite RF comparison follow those checks. Native and Python unit tests
exercise source models; they do not qualify the installed firmware, a sustained
rate, range, energy use or a standalone MCU transport.
