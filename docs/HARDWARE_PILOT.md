# Coordinated public LoRa/IP pilot

The finite public radio and TCP entries, native grant preparation, signed-public-store response and recipient publisher verification are source-prepared. The controller and the mixed hardware path remain under qualification. No fresh RF handoff is established by these sources or by a read-only preflight, and no native device execution is claimed at this checkpoint.

Before activation, obtain a fresh finite device handoff and inspect current hardware, firmware, boots, exclusive ownership, power/region/antenna records and service lifetimes. Confirm the native authentication needed on every participating host. Historical releases and benchmark pins are location hints. A report of no visible radio holders is one observation; the actual worker must still acquire its exclusive owner.

Use synthetic signed public resources and independent adjacent-link keys. Private SAGE providers remain disabled. Root privileges, an artifact hash, successful preparation and a current dispatch lease establish different facts.

## Prepared source stages

| Component | Current responsibility |
| --- | --- |
| [`pilot_context.py`](../src/meshticulum/bench/pilot_context.py) | Create or recheck the immutable original host-local resource context |
| [`pilot_provision.py`](../src/meshticulum/bench/pilot_provision.py) | Prepare one finite public child, with payload provenance and the original native deadline; opens no transport |
| [`pilot_grant_bootstrap.py`](../firmware/meshticulum-bench/pilot_grant_bootstrap.py) | Run preparation as isolated Linux root after package, manifest, source-file and bundle permission checks; obtain current host facts and clocks locally |
| [`pilot_resources.py`](../src/meshticulum/bench/pilot_resources.py) | Lease local authenticated quarantine, serve a selected public chunk from a verified store, or independently verify a complete reply before caching |
| [`pilot_resource_node.py`](../src/meshticulum/bench/pilot_resource_node.py) | Verify against the original sent request and the recipient's own campaign, manifest and trust material; opens no socket or radio |
| [`bench_node.py`](../firmware/meshticulum-bench/bench_node.py) | Hash-verified native actions for finite radio/TCP runs, `pilot-verify` and explicit child revocation |

The provisioner creates protected, create-only child files, including `grant.json`, `secret.bin`, `active`, `provision.json` and a sender body only when applicable. It does not claim the child, open a listener, arm a radio, start a transfer or create a worker journal. Receivers receive no management-supplied payload. The public runtime manifest keeps private services disabled.

The pending controller must connect these stages, preserve their actual process handles and evidence, and enforce the finite device window. A set of successfully prepared children is not an executed or qualified route.

## Original resource permission

The native provisioner creates one `original_context` per host, campaign and logical resource before the first child admission. It records the actual unit and boot, independently observed epoch/monotonic clocks, resource manifest identity, original epoch expiry and original local monotonic deadline. `context.json` is immutable and create-only; another host, boot, resource or expiry cannot rebind it.

The initial context permits at most a 180-second remaining resource window. Each child must remain within its original epoch and monotonic bounds; the current provisioner also caps the grant's issued-to-expiry span at 240 seconds. Preparation cannot install an operator-chosen replacement monotonic deadline. A later wall-clock change, route generation or child grant cannot renew the original resource window.

All IP children require `application_deadline_monotonic_us`. Public radio service grants can carry the same host-local field at the plan's top level. It may only tighten the epoch-derived deadline and is excluded from the shared peer wire context, because hosts have different monotonic clocks. Peer/session/generation/object/service/epoch bindings remain shared. Legacy finite grants do not by themselves prove whole-resource deadline preservation across later admissions.

Relay preparation takes the earlier of the host's original context deadline and the source quarantine's original deadline, while requiring the same application epoch expiry. The context grants no socket, RF, identity or private-store authority. Retain old journals and contexts through failures, cancellation and fresh admissions; new children cannot refund earlier debt or reuse retired nonce/replay state.

## Payload provenance and revocation

There are three sender preparation modes, each recorded in `provision.json`:

- **Original public request fixture:** a synthetic initial sender may receive an explicit management-provisioned MR1 request that matches its admitted size, digest and shape. An MR1 response supplied this way is refused, even if its size and hash match a grant.
- **Local relay:** the body must come from this host's prior authenticated `receiver.bin`, referenced by its run identifier. A simultaneous operator replacement body is refused. The source must be a completed, durable, public receive with matching unit, current boot, format, digest and unexpired original deadlines.
- **Native public response:** `served_public_chunk` references the actual local MR1 request capture. The responder decodes those received bytes and uses a `BlockServer` backed by its configured `VerifiedCache`, pinned manifest and publisher trust. It prepares the resulting response only if its exact bytes match the separately admitted response descriptor. A simultaneous operator replacement body is refused.

The native response mode serves only a chunk from the selected public pack. The pilot controller must choose that mode for the resource server and the local-relay mode for bridge forwarding. Response preparation requires a locally received native source; it cannot substitute an operator-provided reply for the request→store→response path.

`captured()` holds the source run's shared `active` lock while reading and using quarantine. It checks the marker and active-file identity before use, then rechecks the capture, digest and original deadlines before releasing the lease. Retirement or substitution refuses that use. During dispatch, each fresh child holds its own current authority/revocation and worker leases. The controller must retire affected children explicitly; a source lease held during preparation does not imply automatic cascading revocation of every later child.

Protected native receipts provide local provenance. Quarantine remains transport-authenticated bytes, with `publisher_verified` false, until the recipient's independent checks pass. Root access or a matching body hash does not replace publisher verification.

## One radio on each device

Run radio stages sequentially through the existing exclusive worker. Request and reply must not open concurrent physical workers on the same radio. The current finite radio receiver waits 20 seconds after first flow completion so it can repeat a lost final receipt, while its original authority remains current. Allow that margin inside the resource window. Its settled exit and current owner release must precede the next radio stage on that device.

Include receiver grace, operator/controller gaps and setup in whole-resource latency. They are not PHY airtime. Disarm, wire retirement and retained worker debt remain separate evidence.

For C→A LoRa→B IP:

1. C sends a bounded MR1 request to A through fresh radio admission. A durably quarantines the exact authenticated bytes; request/ACK costs remain charged.
2. A provisions its fresh A→B TCP sender from that local request quarantine. Do not copy a substitute request into A through management. B receives and quarantines the actual TCP bytes.
3. B prepares the response from the received request and its configured verified public store, then sends those exact bytes through separate B→A TCP admission under the same original resource expiry.
4. A provisions fresh A→C radio admission from the response actually received over TCP. Management must not install a substitute reply at A or C.
5. C runs independent MR1 request-binding and publisher/chunk verification before committing the piece. The native verifier reads C's original completed sent request, rather than accepting a newly supplied request body.

Every direction needs independent child sessions, adjacent-link keys and current admission. Record the complete peer bindings, original deadlines, sender authenticated receipt, receiver durable bytes, each worker's costs and cleanup separately. TCP `KERNEL_ACCEPTED` is stream-write evidence; it cannot settle RF debt, prove peer receipt or establish publisher trust.

## Independent recipient verification

The initial verifier handles one **whole selected chunk**, starting at offset zero. It checks the MR1 response's magic, request digest binding, success status, offset, complete length and requested size. It then verifies the recipient's independently pinned manifest digest and publisher signature, finds the named chunk in that manifest, validates its content and pack rules, and checks original expiry and the earliest campaign, request or response monotonic deadline before cache insertion.

Prepare signed public metadata and trust material independently at the recipient; receiving a response does not enroll its publisher. This pilot begins with that metadata already provisioned. It does not claim discovery or a complete pack transfer.

The radio child object ceiling is 16,384 bytes, including the 28-byte MR1 response header. Select a complete chunk no larger than 16,356 bytes for the initial radio response. This verifier does not merge arbitrary partial replies into a chunk. Chunk reuse is allowed only after revalidation; an existing piece must match the verified bytes exactly.

The native `pilot-verify` action holds the original sent request's `active` lease and checks its receipt schema, unit, boot, completed send, MR1 format, common epoch expiry and exact descriptor bytes. Its original monotonic deadline must still be live and no later than the immutable campaign deadline; that tighter bound is passed into the chunk verifier. The action rechecks the active-file identity, marker, deadlines, receipt, plan and request bytes before recording `verified-piece.json`. An uncertain or changed request cannot produce that success receipt. The receipt establishes neither private admission nor a SAGE import.

## Failure, resume and route return

For a separate resource transfer, admit a direct C↔B IP route and verify a first piece. Cause a bounded loss of that test connection, retain its original reservations and verify that an incomplete piece has not entered the cache. Do not change production network configuration to inject the failure.

Admit a fresh C↔B LoRa route under the original host contexts, resume using already verified pieces and verify the next completed piece. Then admit a new IP route generation. Obtain a new verified timing sample; old-generation measurements and successful socket writes do not establish its current advantage. Return to IP only if its current verified predicted cost is lower. If it is slower, retain that result and leave the faster-IP requirement open.

These route changes remain controller and physical qualification work. Prepared resource helpers do not migrate live wire sessions, select or authorize routes, or clear retained debt.

## Evidence and cleanup

The controller must enforce the finite handoff end time, preserve raw failures and interrupted writes, poll actual process handles, and stop/disarm/retire its own jobs before returning a device. Preserve production model/voice/UI services, SAGE/SDK identities and protected stores. A state file alone proves neither process liveness nor safe writer handoff.

Keep provisioning receipts, native run receipts, quarantined bytes, verification receipts and cleanup outcomes distinct. An expired or failed run may leave durable evidence; do not delete it and reprovision the same session as if no attempt occurred. A new admission retains the original resource deadline and the prior DATA/ACK/retry reservations.

Collect useful verified bytes and full request→verification elapsed time, plus per-hop timing, stream-byte counts, radio driver completion and uncertainty. Leave unmeasured RF airtime, energy, EIRP and kernel retransmission measurements null. Synthetic and loopback timings are source controls rather than physical capacity measurements.

Matched MeshCore/Reticulum measurements follow this qualification with the same hardware, settings, budgets and application privacy. The retained radio results and 1–1.5 kB/s engineering target remain documented in [performance](PERFORMANCE.md); this pilot plan is not a new benchmark result.
