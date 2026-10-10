# Coordinated public LoRa/IP pilot

The finite public radio and TCP entries are source-prepared. The mixed hardware
path is not qualified. Before loading or running them, finish the native grant
provisioner/controller, obtain a fresh finite device handoff and inspect current
hardware, firmware, boots, ownership, power/region/antenna records and service
lifetimes. Historical releases and benchmark pins are location hints.

Use synthetic signed public resources and independent adjacent-link keys. No
private SAGE provider is enabled. Root/native privileges, a bundle hash and a
successful import each prove different things; none substitutes for current
device or dispatch authority.

## Original resource permission

The native provisioner should create one
[`original_context`](../src/meshticulum/bench/pilot_context.py) per host and logical
resource before its first child admission. It records the actual boot, native
epoch/monotonic clocks, resource identity, original expiry and original local
monotonic deadline. Its file is immutable and create-only. Another host, boot,
resource or expiry cannot rebind it; expired monotonic time cannot be renewed
by a wall-clock change or a new route.

All IP children already require `application_deadline_monotonic_us`. Public
radio service grants can now carry that optional host-local field at the plan's
top level. It may only tighten the epoch-derived deadline and is excluded from
the shared peer wire context, because different hosts have different monotonic
clocks. Peer/session/generation/object/service/epoch bindings remain shared.
Legacy finite grants retain their current behavior; they do not by themselves
prove whole-resource deadline preservation across later admissions.

This context is an accounting/admission input, not a socket or RF grant. It does
not refund previous attempts, replace original nonce/replay state, or qualify a
private lifecycle owner. The native provisioner must bind each child to it and
retain old journals through cancellation, failures and new admissions.

## One radio on each device

Run radio stages sequentially through the existing exclusive worker; do not
open separate physical workers for request and reply. The current finite radio
receiver stays available to repeat its final receipt before disarming. Its
settled exit and current owner release must precede the next radio stage on
that device. Include that policy and operator/controller gaps in whole-resource
latency; do not present them as PHY airtime.

For the C→A LoRa→B IP case:

1. C sends a bounded MR1 request to A through fresh radio admission. A durably
   quarantines the exact authenticated bytes; the request/ACK costs stay charged.
2. A forwards those received bytes through fresh A→B TCP admission. Do not copy
   the request into A via management as a substitute for the radio transfer.
3. B reads only the configured verified public store and sends the MR1 response
   through a separate B→A TCP admission under the same original resource expiry.
4. A sends the bytes actually received over TCP through fresh A→C radio
   admission. C checks the MR1 request binding and independently verifies the
   signed manifest and full chunk before committing it.

Record complete peer bindings, independent child sessions, original deadlines,
sender authenticated receipt, receiver durable bytes, publisher verification,
each worker's costs and disarm/retirement separately. TCP `KERNEL_ACCEPTED`
never settles RF debt or proves publisher verification.

## Failure, resume and route return

For a separate resource transfer, admit a direct C↔B IP route and verify a first
piece. Cause a bounded loss of that test connection, retain its original
reservations and verify the incomplete piece has not entered the cache. Do not
alter production network configuration to inject the failure.

Admit a fresh C↔B LoRa route under the original host contexts, resume using the
already verified pieces and verify the next completed piece. Then admit a new
IP route generation. Obtain a new verified timing sample; no old-generation
measurement or successful socket write authorizes a faster-path claim. Return
to IP only if its current verified predicted cost is lower. If it is slower,
report that result and keep the required faster-IP qualification open.

## Evidence and cleanup

The controller must enforce the finite handoff end time, preserve raw failures
and interrupted writes, poll actual process handles, and stop/disarm/retire all
its own jobs before returning the device. Preserve production model/voice/UI,
SAGE/SDK identities and stores. A state file alone is not process liveness or a
safe writer handoff.

Collect useful verified bytes and full request→verification elapsed time, plus
separate per-hop timing, DATA/ACK/retry reservations, known stream-byte counts,
radio driver completion and uncertainty. Leave missing RF airtime, energy,
EIRP and kernel retransmission measurements null. Do not publish synthetic or
loopback timing as physical capacity.

Matched MeshCore/Reticulum measurements follow this qualification with the same
hardware, settings, budgets and application privacy. The retained radio results
and the 1–1.5 kB/s engineering target remain documented in
[performance](PERFORMANCE.md); this plan does not constitute a new benchmark.
