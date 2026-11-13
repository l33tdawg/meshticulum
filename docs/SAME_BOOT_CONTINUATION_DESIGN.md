# Explicit same-boot continuation

This successor implements a separate opt-in ContinuationAdmission. The default
RadioAdmission remains limited to1024. Independently pinned application approval
bytes must bind the exact finite operation. Source qualification creates no grant,
application approval or hardware authorization.

The versioned canonical record contains the original1024 ceiling; exact boot,
firmware, USB serial and paid baseline; sender/receiver incremental limits; exact
absolute ceiling equal to baseline attempts plus sender allowance; unchanged300M
cumulative airtime; and history, approval, shared context and previous profile
hashes. Baseline attempts must be <=1024: this deliberately models a single initial
extension, not automatic chaining. An example current baseline1012 plus358 is1370.
The application must approve that exact operation; the native100000 maximum is
not a default permission. A hash field is a binding, not an approval signature.

The shared context covers the grant's behavior-bearing fields, including both
file_capability and forthcoming turn_end_capability. Unknown fields are rejected
until deliberately reviewed. Endpoint-local unit/host/local_peer metadata remains
under the existing RunAuthority checks. Only the continuation record itself is
excluded from its own context hash; if embedded it must exactly match the checked
record bytes. Canonical immutable bytes prevent caller mutations of a returned
mapping from changing the record.

## Implemented execution contract

1. Add a distinct explicit admission type, never a subclass trick or monkeypatch.
   Legacy RadioAdmission's constructor and checks remain <=1024. New admission
   must retain the same country/antenna/waveform/lease/airtime checks and accept
   only a checked continuation record backed by independent application approval.
   Do not merely replace the1024 constant with100000.
2. RunAuthority must deliberately recognize the new type, require the identical
   record in the plan, bind it into the shared crypto route, and retain canonical
   admitted bytes for mutation detection. Missing/mismatched records fail before
   worker creation. C validates the shared A continuation and its own operation
   allowance; it must never claim an A MCU identity of its own.
3. Keep identity ownership separate: FirmwareIdentity proves native boot/version;
   ExclusiveSerial and HeldHardware prove the configured USB identity and channel
   ownership. Do not accept a caller-supplied serial string as independent hardware
   proof. Exact protected owner callbacks/descriptors must remain bound and live.
4. Within the existing arm cleanup region, after checked disarm and before ARM,
   perform a fresh request-bound STATUS and BURST_STATUS through the actual held
   channel. Retain existing _live checks before/after each callback and immediately
   before ARM. STATUS must match this record's exact baseline and previous profile;
   burst must prove token/arm/deadline/context/pending/retained all empty and clear
   flags, with matching cumulative counters. No received callback can update the
   trusted baseline or extend the original deadline. Any counter increment,
   response mismatch, owner revocation, dirty burst or timeout refuses ARM.
5. STATUS validation alone is not sufficient: the record decoder returns
   no reusable admission token. The worker
   must bind both observations to the current call/owner/identity and original
   authority, with no cached proof across retries. If a radio reset or boot change
   occurs, this record is invalid; old debt remains retained.
6. The final sender grant, journal and hardware profile must carry the exact finite
   ceiling and unchanged operation limits. Original request/deadline/replay checks,
   native shared BootState and cumulative airtime accounting remain unchanged.
   Existing scopes and journals must never be rewritten to the larger value.

The public CLI uses default-off --boot-continuation, independently pinned by
--boot-continuation-approval and --boot-continuation-approval-sha256. Constructor/owner/worker exception paths must keep bounded cleanup.
Do not enable the higher cap in a campaign before source integration, full standalone
suite/frozen-flow verification, independent review, concrete user/application
approval and fresh finite hardware authorization.

## Source checks

Focused tests exercise exact record/context binding, default admission refusal,
canonical representation, immutable copies, bool/limit/identity mutations, future
capability and unknown-field handling, and STATUS boot/request/profile/counter/
closed-flag failures. They use synthetic identities and frames and no device I/O.
They do not prove live owner continuity, burst closure or physical feasibility.

Execution models additionally exercise actual SerialWorker and BurstSerialWorker
ARM/OPEN/DATA bookkeeping, fresh native state/owner refusal, callback replacement,
advancing-clock lease narrowing and public run_object cleanup. The final ARM lease
is narrowed after expensive fresh owner checks using the original deadline; pure
captured-reference checks run after the final clock and immediately before the
protected write. Burst subclass ARM bookkeeping remains intact. No baseline is
refreshed, old journal rewritten, or paid attempt/airtime debt refunded.
