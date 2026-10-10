# Experimental forward-private sessions

[`forward_session.py`](../src/meshticulum/privacy/forward_session.py) is an experimental end-to-end session primitive for the existing private envelope. It adds fresh interactive key agreement and authenticated key confirmation around an unchanged LM1/LM2 sealed record. It has source fixtures and a reference-vector comparison, not native deployment or radio qualification. The ordinary [radio](../src/meshticulum/bench/wire.py), [IP](../src/meshticulum/ip/wire.py) and [static recipient envelope](../src/meshticulum/privacy/mesh_envelope.py) modes retain their existing properties; this module does not upgrade them automatically.

The frozen MT05 flow is unchanged. Meshticulum provides this generic primitive; [Lantern](https://github.com/l33tdawg/sage-lantern) owns native identity storage, private policy, capture, consent, provisioning and deployment. The dependency remains Lantern → Meshticulum. Both repositories remain private, and this work creates no device authority or release.

## Intended protection and assumptions

The intended protection is against later decryption of captured **outer session records** after an endpoint's long-term identity key is compromised. It depends on fresh secure ephemeral keys, a completed authenticated exchange, protected session keys and effective destruction of ephemeral/session secrets. It does not protect a compromised live endpoint or erase private content that an endpoint has already stored.

The existing inner SealedBox record still uses a static recipient key. If that inner record is captured or retained separately, later recipient-key compromise can expose it. Wrapping it in a forward-private session does not change its storage properties. Native capture and staging must distinguish outer network records, inner sealed records and admitted application content. [SealedBox construction](https://doc.libsodium.org/public-key_cryptography/sealed_boxes)

The caller independently pins both complete Ed25519 peers. An opaque `binding_digest` must represent the approved application context; the primitive cannot establish that digest's meaning or decide whether the peer is authorized to send a particular message. A trusted clock, live authorization and durable counter implementation are also caller responsibilities. No certificate enrollment, discovery or trust-on-first-use is supplied.

## Exchange and record boundaries

The key agreement uses `Noise_KK_25519_ChaChaPoly_SHA256`. Both static public keys are known before the exchange. The ordered initiator/responder peers, session ID, application binding, original epoch lifetime and record limit are hashed into the prologue. Local role also binds the counter claim and signed confirmation. Fresh X25519 ephemeral keys contribute to the final directional keys. The token order and split follow the [Noise specification](https://noiseprotocol.org/noise.html#interactive-handshake-patterns-fundamental).

| Phase | Initiator | Responder | Application availability |
| --- | --- | --- | --- |
| H1 | `start()` creates the first empty-payload handshake message; state becomes `await_h2`. | `respond()` authenticates H1, creates H2 and derives keys; state becomes `await_confirm_i`. | No application records. |
| H2 | `finish()` authenticates H2 and derives keys; state becomes `send_confirm_i`. | Waits for initiator confirmation. | No application records. |
| Initiator confirmation | `confirm()` encrypts an Ed25519-signed context/transcript/role digest; state becomes `await_confirm_r`. | `accept_confirmation()` verifies that digest and signature, commits replay acceptance and produces its encrypted signed reply. | Responder becomes locally `ready` after producing the reply. |
| Responder confirmation | `verify_confirmation()` authenticates the reply and commits replay acceptance; state becomes `ready`. | Already locally `ready`. | Each endpoint may use its ready session. The caller must deliver confirmation before dependent application records. |

Empty handshake payloads carry no legacy private content. The additional confirmations bind the full Ed25519 identity and the final handshake transcript, beyond the converted X25519 keys. A locally ready responder has verified the initiator and produced its own confirmation; it has no proof that the initiator received that reply. Reliable delivery, ordering, retransmission and application receipts remain separate integration duties.

`MPF1` is Meshticulum's separate framing and record protocol. Its 29-byte header contains magic, message kind, session ID and counter. Encrypted confirmation and DATA records authenticate `DOMAIN + header` as associated data. Counter 1 is reserved for confirmation in each direction; DATA starts at 2. The record nonce is four zero bytes followed by the little-endian encoding of `counter - 1`. The bounded context permits at most 4,096 counter values per direction, including confirmation and consumed reservations.

Each handshake packet is 77 bytes; each confirmation is 141 bytes. DATA adds 45 bytes to the unchanged binary envelope and remains bounded by its existing size ceiling. These are encoding sizes, not measured link costs. The Noise handshake bodies, MPF1 framing, signed confirmations and LM1/LM2 records form a project-specific composition; an ordinary Noise or NoiseSocket peer is not wire-compatible with this service.

## Durable state, deadlines and failure

One session belongs to one process/thread owner and one incarnation. The caller supplies a counter store with three synchronous operations:

- `claim(binding)` must durably and uniquely claim the session ID and local role in a dedicated namespace. Its key is independent of the remaining context, so changing peers, expiry or limits cannot reclaim that ID/role. Reuse must fail, including after restart. The full context is authenticated separately by the prologue and signed confirmation.
- `reserve_transmit()` must commit a new monotonically increasing counter before returning it. Encryption happens afterward. A lost result or later encoding failure leaves the reservation consumed.
- `record_receive(counter)` must atomically commit replay acceptance after record authentication and before an opened record is returned. A lost result fences the session without exposing the opened record; a committed acceptance remains committed.

These counters are independent of adjacent-link flow counters and radio/IP debt. They do not replace application replay admission or refund transport attempts. Native transaction durability, protected file ownership, deletion/rollback resistance and recovery of uncertain writes remain unqualified here.

Operations check live authorization, the original epoch window and the caller's original monotonic deadline. Clocks may not move backward within the incarnation. Restart requires a fresh session ID and exchange under the original application authority and lifetime; it does not resume old keys or reset old replay decisions. Boot identity and the relationship between the supplied clocks, application intention and deadline must be enforced by the native owner.

Errors close the session and return a fixed refusal for ordinary exceptions. Interrupted control flow also closes it. Closure drops Python references to handshake, signing and record keys; it is not proof of memory zeroization. There is no rekey, ratchet, persisted session key or post-compromise recovery protocol.

`open()` returns the authenticated **sealed binary envelope**, not admitted private content. The caller must still use the existing opener with independently pinned sender/recipient, verify the inner signature, enforce original expiry and protected replay, and complete native capture/consent admission before storage or action. A session confirmation or MT05 ACK is not proof of delivery, canonical application commit or permission to display the message.

## Review and evidence limits

The initial source review checked KK token order, prologue/pre-message hashing, transcript binding, directional split, record nonce/header authentication and commit ordering against the implementation. A checksum-pinned `noiseprotocol` 0.3.1 package was used only as a deterministic test oracle for both handshake bodies, the handshake hash and split keys. It is not a runtime dependency or a security endorsement. The oracle does not validate MPF1, the signature composition, native authority, secret destruction or RF behavior.

The initial review found a callback fencing defect: `_live()` checked closure and ownership before invoking authorization/clock callbacks, but did not recheck those conditions after they returned. A source fixture closed the session inside its final clock callback and still obtained an authenticated outgoing record. Six closing-callback controls exposed four failures on that initial source.

The corrected source checks ownership and closure before and after both callbacks. An operation guard covers all eight public crypto operations; nested entry closes the session even when the callback catches the nested refusal. Constructor callbacks are guarded too, while `close()` remains available for cancellation. Focused source regressions now refuse closing callbacks, reentrant send/receive/handshake operations and interrupted sends, retaining committed nonce reservations and replay acceptance. Independent re-review reproduced refusal of the original final-clock case and a caught nested send, with the consumed counter retained. This resolves the demonstrated source defect; it does not qualify native lifecycle or secure memory erasure.

Static-key reuse is another explicit review gate. The prototype converts the existing Ed25519 identity to the same X25519 static key used by legacy SealedBox. Noise cautions against using its static keys outside Noise without careful cross-protocol analysis, and libsodium recommends separate signing/encryption keys when possible. Matching a KK vector does not settle that composition's security. A dedicated-key design or a justified review of the existing reuse must precede production acceptance. [Noise security considerations](https://noiseprotocol.org/noise.html#security-considerations), [libsodium conversion guidance](https://doc.libsodium.org/advanced/ed25519-curve25519)

Remaining gates include independent cryptographic review; broader negative and interrupted-state coverage; native key generation, storage and erasure; durable claims and replay recovery; original policy/capture/consent integration; transport ordering and loss handling; and measured private transfer cost. Existing physical, firmware/source correspondence, third-party licensing, clean-history publication and [release-readiness](RELEASE_READINESS.md) gates remain open.

The competitive security goal is still a goal. Reticulum already documents forward-secret Links and optional destination ratchets; this experimental source does not establish stronger overall security or deployed forward secrecy. Performance evaluation continues to use our qualified measurements and published upstream figures, as described in the [comparison](COMPARISON.md#security-properties-and-gaps) and [roadmap](ROADMAP.md).
