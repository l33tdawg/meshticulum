# Finite MCU batch prototype (MBQ2)

This is source-only work toward reducing per-packet host/USB admission and
release round trips. It is **not installed firmware**, a supported serial
capability, or a measured throughput improvement. Current workers still use
MBQ1. The production serial enum, framing limits, firmware loop, flow engine,
owner, durable journal and radio adapter are unchanged.

The prototype consists of:

- `firmware/meshticulum-bench/experimental/tx_batch.hpp`: a fixed-memory,
  single-loop job owner around the existing native `TxBurstQueue`.
- `src/meshticulum/bench/burst_batch_protocol.py`: bounded offline record bodies,
  exact binding checks, and a conservative model of an already prepaid host
  commit. These are deliberately not production serial `Frame` objects.
- `tests/mcu_batch_native/tx_batch.cpp`, `tests/test_mcu_tx_batch.py` and
  `tests/test_burst_batch_protocol.py`: native sanitizer and Python controls
  with synthetic driver/time evidence. The native tests calculate real SHA-256
  and compare the canonical manifest with Python.

## What the MCU already does

The existing MBQ1 path retains at most four presealed DATA ciphertexts, charges
native attempt/airtime debt before acceptance, and drives the admitted sequence
under its original deadline. It produces per-request physical evidence and
requires explicit evidence acknowledgment. It does not possess peer keys,
authenticate peer ACKs, choose retransmissions, increase logical credit, or
create authority.

The host still prepares and seals each flow attempt, durably reserves its
physical charge, writes each DATA command, waits for its admission reply, and
settles/releases each physical result. The current flow owner permits one
prepared handle at a time. A vector transport cannot safely bypass that owner
contract; production integration needs a separately reviewed handover API and
an atomic durable reservation for the entire vector.

## One immutable job

Each job binds the original boot, admitted context/profile digest, OPEN token,
job request, common deadline, ordered packet requests and ciphertext hashes.
Only DATA is eligible. Each packet remains at most 255 bytes; each job contains
one to four packets. The common deadline cannot exceed either the admitted
lease or two seconds from BEGIN. STAGE and COMMIT cannot extend it.

The canonical manifest is SHA-256 over network-order fields:

```text
"MBQ2" | boot[16] | context[32] | token:u64 | job:u64 |
deadline:u64 | count:u8 |
count * (request:u64 | native_estimate_us:u64 | size:u16 | ciphertext_sha256[32])
```

This input is at most 277 bytes. The trusted native caller must provide a real
SHA-256 function and derive each airtime estimate independently from the actual
admitted waveform. The prototype verifies each ciphertext digest while staging
and recomputes the complete ordered manifest at commit.

| Offline record | Envelope request | Body | Maximum body |
| --- | --- | --- | ---: |
| BEGIN | job | token, deadline, count, manifest | 49 bytes |
| STAGE | physical packet request | token, job, index, estimate, size, digest, ciphertext | 314 bytes |
| COMMIT | fresh command request | token, job, manifest | 48 bytes |
| RELEASE | fresh command request | token, job, manifest, count, ordered terminal entries | 245 bytes |

All fit the existing 320-byte body bound. The external record envelope carries
the boot and request; no actual opcode numbers are allocated. Request ordering
is token < job < ordered packet requests < COMMIT < RELEASE. A release or other
mutation must not overtake staged packet request IDs in a future integration.

BEGIN consumes the job high-water mark but neither charges native airtime nor
permits issue. STAGE buffers validated packets without RF or native charge.
Identical staging replays are inert; conflicting bytes, binding or descriptors
are refused. A second job cannot replace the retained job.

The future wire dispatcher must not advance the shared mutation high-water mark
for each STAGE: COMMIT still has to admit those member request IDs in order.
Copying the existing per-command mutation-floor rule would make the vector
stale. The prototype advances the floor at BEGIN, through the complete vector
and command at COMMIT, and at RELEASE after complete validation.

COMMIT first validates the complete vector, request order and manifest. It runs
the existing queue's admission checks against a private copy of the boot state,
including all cumulative costs, overflow, original deadline and cap checks.
Only after every entry passes does it enqueue and charge the entire vector in
the live queue. No issue method is exposed between these synchronous steps.
An unexpected live-commit failure retains the full validated native charge and
fences the owner. This relies on single-loop ownership: neither the hash callback
nor another task may mutate boot state during preflight/commit.

No dynamic allocation is used. The native test bounds the job object at 8 KiB;
commit additionally holds a fixed temporary queue/boot copy on the stack. Actual
target stack/RAM headroom must be checked before firmware integration.

## Ambiguity, release and original authority

Before sending COMMIT, a future host must durably reserve **all** physical
attempts in one FULL-synchronous transaction under the original authority. A
partial write, lost reply or mismatched reply leaves the entire host vector
charged and UNKNOWN, with further sending fenced. A known rejection also
retains those host charges. The `PrepaidCommit` model demonstrates that rule;
it is not a journal, authorization check or persistence implementation.

An exact duplicate COMMIT returns the original admission without charging or
issuing again, including after release. It does not report current queue
liveness or restore authority. The host UNKNOWN state remains sticky even if a
late exact reply arrives. A new job owner rejects old job IDs using the
boot-wide high-water mark. Reconstructing an owner cannot replay old work.

The native owner retains per-packet ISSUED/STARTED/terminal evidence. Unknown
issued work requires checked physical containment. RELEASE includes every
terminal request in manifest order, with exact outcome, event time and digest.
The native owner validates the entire vector before freeing its first entry.
Partial, reordered, duplicated or mismatched releases free nothing. Exact
release replay returns the cached result without freeing or charging again.
Commit/release replay checks may report old immutable results without sampling
a fresh clock; they never issue radio work or renew a deadline.

Cancellation, expiry, release and owner destruction never refund debt. Expiry
or clock regression while a packet is active retains UNKNOWN and permanently
fences the boot. Checked containment clears only the physical-containment
requirement; it does not clear the fence. Tests cover a new owner after such
poll-triggered containment to ensure it cannot send again.

## Timing stays bounded

The queue depth stays four. The first packet can issue immediately after a
successful full commit, subject to the actual driver/RX/output/deadline checks.
The existing 100 ms start guard is admission/deadline headroom, not an initial
transmit delay. Between successful packets the wrapper retains a 5 ms listening
floor. At the actual empty tail it retains a 100 ms reverse-listen floor, including
a one-packet job. `BatchPacing` must live alongside `BootState` for the whole boot,
so a new job owner cannot erase this floor. A cancelled or uncertain job stays
fenced regardless of elapsed listening time.

The native wrapper calls no driver. Production integration would still need the
existing RX_DONE drain, header/receive-in-progress checks, evidence-output
capacity checks, checked standby/IRQ handling and physical containment. Native
RF_DONE never means authenticated peer or application receipt. Host revocation
cannot be instantaneous after delegation: cancellation must reach the MCU, and
the original deadline is the final bound on autonomous work.

## What this can and cannot establish

A complete prefilled vector could prevent a temporary host enqueue gap from
being interpreted as the actual radio tail. It could also combine admission
and release acknowledgments. STAGE still transports every ciphertext over USB,
and this prototype does not yet implement combined event vectors or an actual
serial dispatcher. No saved time is claimed from source tests.

At SF5/BW500/CR4/5 with the current full-frame geometry, four 193-byte transport
payloads, four 34,640 us frame estimates, three 5 ms intervals and a 100 ms tail
give an optimistic bound of about **3.045 transport kB/s**. That excludes useful
object metadata, CONTROL, initial startup, real driver overhead, losses and
recovery. It leaves essentially no margin for a sustained **3 useful kB/s**
stretch target. The next measured acceptance target is above 2.8 useful kB/s.
Reaching the stretch target needs further measured work and likely compact
metadata or a separately reviewed train policy; batching alone is not proof.

Before activation, the remaining work includes the reviewed multi-handle owner
contract, atomic host journal reservation, wire dispatch and parser fuzzing,
exact capability negotiation, native target memory/clock checks, crash and USB
loss reconciliation, evidence-vector output/backpressure, dependency provenance,
and a separately authorized finite hardware qualification. Private applications
must consume a pinned public core; no second firmware implementation is created
here. This prototype grants no installation or RF permission.
