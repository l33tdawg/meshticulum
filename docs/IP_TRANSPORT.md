# Authenticated TCP transport

Historical Core commit IDs below identify development snapshots. Their results remain bound to those original inputs. The public project retains its extraction history with the owner's approval; see [repository history](REPOSITORY_HISTORY.md).

The `meshticulum.ip` package carries the unchanged MT05 reliable flow over an explicitly admitted TCP connection. It adds larger authenticated frames and durable stream-byte accounting without reusing the radio airtime ledger. Real loopback controls cover public MR1 request/reply, signed-pack chunk verification, malformed frames, replay, revocation, original deadlines and uncertain process/write recovery.

The carrier has source and loopback controls, plus the finite native public mixed-path and loss/resume/IP-return checkpoints below. Private native owner/consent and transport, sustained performance and release qualification remain open. Meshticulum provides the generic carrier, not a device launcher or native provisioner. The finite public [TCP job lives in Lantern](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/software/meshticulum-integration/src/lantern_meshticulum/ip/node.py), which depends on Meshticulum. Meshticulum imports no Lantern implementation and supplies no discovery, connection daemon, MeshCore/Reticulum gateway or private native provider.

## Recorded native public milestone

A finite Lantern campaign completed a public C→A LoRa→B IP request and the reverse reply. The recipient independently verified the original request binding, publisher-signed manifest and complete content before accepting **977 public bytes**. The historical execution pins are:

| Input | Historical identity |
| --- | --- |
| Meshticulum core | `6605ba714ce414445673b40bfcfe93fe467b86aa` |
| Lantern integration | [a41851a4301008bd840a8508187a59a150eeaa0e](https://github.com/l33tdawg/sage-lantern/commit/a41851a4301008bd840a8508187a59a150eeaa0e) |
| Host runtime SHA-256 | `d3194a92917056818dc58b6c7fce4f1320b37c4d6a16acaa9bc7512f593480a9` |

An earlier resume attempt at those pins retained its verified piece after **512 received stream bytes** of TCP loss, but refused LoRa before ARM/TX: its fixed 90-second lease exceeded the approximately 85 seconds remaining. The later campaign below exercised a separately corrected runtime and completed the sequence. The earlier attempt remains scoped to its original result.

Device policy and operator evidence stay in [Lantern’s pilot guide](https://github.com/l33tdawg/sage-lantern/blob/c596664b41e6ae1bd37cf64591fb7bd827533900/docs/lantern/MESHTICULUM_PUBLIC_PILOT.md). This is finite public delivery evidence. Sustained capacity, private production qualification and measured RF airtime, energy and EIRP remain open.

## Recorded native public resume and IP return

A later fresh Lantern campaign completed the public loss/resume/IP-return sequence under **one original 160-second resource window**, with these exact execution inputs:

| Input | Execution identity |
| --- | --- |
| Meshticulum core | `a89c1ca6fdf1865d61367cf3a8d59ac1ab50cb39` |
| Lantern runtime code | [379025e177619ee3ffd51ef8d5ae0b2bc27dd54e](https://github.com/l33tdawg/sage-lantern/commit/379025e177619ee3ffd51ef8d5ae0b2bc27dd54e) |
| Host runtime SHA-256 | `856570c771f1377630c8456774a33f55660fe787af9336f2164d55ae9d10561f` |

The campaign independently verified an initial IP piece, injected a **512-stream-byte loss** into the next reply and rejected that partial object while retaining the first verified piece. It then used **fresh C↔B LoRa request/reply sessions** and independent recipient verification, obtained a fresh faster IP sample, and independently verified the fourth piece after returning to IP. All four pieces were **977 bytes**, totaling **3,908 useful verified bytes**, with every verification before the original expiry and no deadline extension.

Ten sessions ended with **twenty terminal, retired native children** and **three terminal owned management masters**. The read-only journal/holder/service audit passed; handback completed before **11:35 UTC on 2026-10-10**. Native source policy, host details and the [sanitized campaign evidence](https://github.com/l33tdawg/sage-lantern/blob/2f96d9029f972e665286209d9883418d431ab1d0/docs/lantern/evidence/meshticulum-native-resume-20261010.json) remain in Lantern; see its [pilot guide](https://github.com/l33tdawg/sage-lantern/blob/2f96d9029f972e665286209d9883418d431ab1d0/docs/lantern/MESHTICULUM_PUBLIC_PILOT.md).

The resumed LoRa piece took **54.349727 seconds** from request to independent verification; the fresh IP sample took **9.529958 seconds**. These are two tiny service transaction samples including controller/management preparation and final radio ACK windows. They support that campaign’s measured path decision, not sustained RF/IP capacity, one-way latency or a matched upstream speed comparison. Actual RF airtime, energy and EIRP remain unmeasured.

These results establish a finite mixed-path milestone and a separate loss/resume/IP-return campaign at their own source pins. They do not close broader private, lifecycle or sustained-performance qualification. The source-build [`1.0.0-alpha` prerelease](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha) is public while that work continues; [release readiness](RELEASE_READINESS.md) records its tested scope and remaining programme requirements.

## Bindings and ownership

[`IpGrant`](../src/meshticulum/ip/wire.py) binds complete peers, independent session/incarnation values, the TCP endpoint pair, service format, exact object size/digest, route binding, epoch expiries and finite limits. Its separate `meshticulum.ip.v1` key/authentication context avoids interpreting TCP as the radio protocol. Pairwise secrets are provisioned independently; this PSK mode does not supply forward secrecy or identity enrollment.

[`IpAuthority`](../src/meshticulum/ip/authority.py) reads a private local grant, secret and active marker, checks their original identities and contents, and makes a durable single-use claim. It is a generic admission/lease checker; [Lantern's native provisioner](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/software/meshticulum-integration/src/lantern_meshticulum/bench/pilot_provision.py) creates the pilot inputs and independently supplies host facts and clocks. A dispatch lease holds the revocation lock, checks current ownership and belongs to its acquiring thread. Closing from another thread waits for that lease to end; closing from inside the lease is refused. Original application deadlines may only tighten and cannot be rebound on replay-store reopen.

[`TcpWorker`](../src/meshticulum/ip/worker.py) adopts an already connected IP stream socket by detaching the descriptor from its caller. It validates the actual local/remote endpoints and the journal owner before use. The host must separately authorize socket setup and protect this process and its descriptors; a Python record or file hash is not proof of native authority. The worker creates no connection and performs no radio operation. Lantern's public job performs bounded socket setup before passing the connected socket to this library, under the same original expiry and current revocation checks.

## Framing, counters and reliable flow

Each bounded packet has a four-byte stream length, a typed session/counter header and a ChaCha20-Poly1305 body. Lengths are checked before allocating a body. Separate directional keys are derived from the complete grant context. DATA and ACK lanes must agree with the authenticated inner MT05 frame.

The [IP session store](../src/meshticulum/ip/state.py) adapts the existing durable nonce/replay implementation for IP grants. Transmit counters commit before encryption; receive replay admission commits before plaintext is returned. The store retains the original boot, declared monotonic deadline and application epoch expiry. Missing state, a changed boot, retired state or a different declared deadline cannot silently restart it. Filesystem deletion/rollback protection is still the native deployment's responsibility.

[`IpFlow`](../src/meshticulum/ip/flow.py) uses the frozen `ReliableFlow`, including its receiver-issued startup credit, bounded window, receipt revisions, selective ACKs and retries. Its byte/reassembly bounds enforce the exact admitted object. Unmeasured socket serialization adds no invented queue delay; explicit source RTO bounds remain policy to qualify, not a measured network optimum.

## Explicit sender readiness

A relay can connect its TCP hop before the preceding radio hop has delivered the complete object. Starting receiver credit at connection time can exhaust the flow's startup allowance while the sender still has no object to send. The optional readiness mode delays that credit exchange until the sender has validated its complete admitted object.

Both endpoints must select `schema: meshticulum.ip-service-run.v2` and `startup_mode: sender_ready_v1` in the original admitted plan. The mode is bound into the grant digest, directional keys and authenticated frame header. Legacy v1 plans keep their original wire format and refuse READY frames.

The sender's first frame is READY: a 32-byte grant digest, authenticated in direction 0 at counter 1. Its **83 framed stream bytes** and one attempt consume the existing journal limits. Sending it requires the held application authority, current socket ownership, exact object size and digest, and the original deadlines. An uncertain write retains its reservation and fences the run.

Until READY is verified, the receiver checks live authority and expiry but emits no startup ACK. It rejects early DATA or ACK frames; duplicate or replayed READY cannot restart the flow. After READY, the unchanged reliable flow issues ordinary receiver credit before DATA can move. READY grants no permission, credit or delivery receipt. Waiting does not extend a deadline or enlarge a byte, frame or retry allowance.

This mode has source and loopback controls. Lantern owns native provisioning and must explicitly admit v2 before deploying it; the existing native v1 pilot is not upgraded by installing this library. These controls do not qualify a physical radio path or private backend.

## Byte accounting and uncertainty

[`IpJournal`](../src/meshticulum/ip/journal.py) owns a separate SQLite/WAL journal under an exclusive file lock. It checks the live lock descriptor as well as the journal and owner-file identities before every transaction. Losing or replacing that descriptor permanently refuses further use of the instance; reopening a file does not repair its ownership. Closing from another thread waits for an active transaction, and closing inside that transaction is refused. Cleanup avoids closing an unrelated descriptor that has reused the old descriptor number. It reserves the whole framed packet before a socket write. DATA, ACKs and retransmissions all consume the same finite lifetime transmit-byte budget. Known partial kernel writes are recorded; errors, interruption or an unresolved process-recovery reservation remain UNKNOWN and fence new dispatch. Closing or cancelling a flow does not refund the reservation.

A read intention also commits before `recv`. Known reads settle their actual count; a recovered unresolved read charges its possible bytes and fences the run. Reads stay within the remaining receive allowance. Packet lengths, receive buffers, object memory, attempts and lifetime bytes are bounded.

`KERNEL_ACCEPTED` describes local kernel acceptance of the complete framed packet. It does not mean `RF_DONE`, peer receipt, publisher verification, private admission or canonical SAGE commit. Journal byte counts describe the application socket boundary; IP/TCP headers, kernel retransmission bytes and RF airtime are unmeasured. Unknown writes can leave known kernel-byte counts as a lower bound; their full reservations remain charged.

## Service integration

The generic [ServiceTransfer](../src/meshticulum/bench/services.py) recognizes the concrete IP flow/authority family in addition to its radio family. It verifies exact object bytes, digest and admitted format under the held application lease. These imports create no connection or native permission. The public [ServiceRunner](../src/meshticulum/bench/service_runner.py) consumes the caller-owned flow; it does not provision hosts or implement a private inbox.

`PreparedBlockExchange` uses fresh independent request and response sessions, reversed peers and one original resource expiry. The loopback control drives MR1 over TCP and passes the returned chunk through the existing signed-manifest/content verifier. Resumption can retain verified pieces; it must use newly admitted wire sessions rather than resetting old counters or debt.

Private application dispatch belongs entirely to [Lantern's SAGE integration](https://github.com/l33tdawg/sage-lantern/tree/main/software/meshticulum-integration/src/lantern_meshticulum/sage). Its exact-grant native `PrivatePolicy` and durable capture/inbox sink consume Meshticulum's carrier and envelope primitives. Meshticulum's public runner refuses private jobs and has no SAGE SDK or Lantern imports. Private sealed records are not public resources; loopback transport controls do not qualify a real encrypted backend or enable a private daemon.

## Lantern's finite public TCP pilot

The Linux public entry is [`lantern_meshticulum.ip.node`](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/software/meshticulum-integration/src/lantern_meshticulum/ip/node.py), in Lantern's integration package. It invokes one public MR1 job, requires native Linux authority, checks the unit role/boot and service lifetimes, and reads only the native provisioner's private grant and sender file. It refuses private formats, malformed objects, a different boot or an expired deadline before socket setup. That launcher, its host checks and its fleet configuration are not shipped as Meshticulum code.

The initiator binds its exact admitted endpoint and makes one connection attempt; the responder binds its exact endpoint and accepts one peer. Setup lasts at most five seconds and remains subject to the original application deadline and revocation. An unexpected endpoint is refused. SIGTERM/SIGINT stop new operations, close the socket and retire wire state; previous stream-byte reservations remain charged.

Received bytes are fsynced to a create-only, descriptor-anchored `receiver.bin` in the private run directory. They remain quarantined public blocks, with `publisher_verified=false` and `private_admission=false`. A caller must independently verify MR1 request binding, the signed manifest and the complete chunk before caching or seeding.

The IP adapter flushes its pending final ACK before the finite receiver closes. A sender stops reading once it has accepted the authenticated final receipt, so a subsequent TCP EOF cannot erase that receipt. ACK kernel acceptance still does not prove peer receipt; the two endpoint results must be checked separately.

The [runtime builder and hash-verified launcher](https://github.com/l33tdawg/sage-lantern/tree/main/firmware/meshticulum-bench) also belong to Lantern. They package its finite jobs with a separately identified Meshticulum dependency. Building or installing a bundle does not authorize a connection or a radio. Native pilot orchestration lives in [Lantern's bench integration](https://github.com/l33tdawg/sage-lantern/tree/main/software/meshticulum-integration/src/lantern_meshticulum/bench); see [the hardware boundary](HARDWARE.md).

## Controls and remaining work

Run the carrier and lifecycle controls with:

```sh
python -m pytest tests/test_ip_transport.py tests/test_ip_sender_readiness.py tests/test_ip_ownership.py tests/test_mixed_carriers.py tests/test_mixed_resume.py
```

These generic carrier controls use actual loopback TCP, synthetic admitted peers and modeled radio workers. Lantern owns the separate finite-job and native integration controls. Child-process tests with stubbed device/root facts do not qualify actual host ownership or a native backend. The generic controls cover:

- Lost and reused owner descriptors, concurrent close, same-thread close refusal and retained unresolved reservations.
- C→A modeled radio→B actual TCP request/reply, preserving A's bridge identity, signed-pack verification, original expiry and each carrier's separate costs. TCP loss stops the exchange before a radio reply.
- Verified chunks retained across TCP loss, fresh radio request/reply admissions, a new IP route generation and selection of the faster locally verified route. Every block uses independent sessions; failed attempts keep their debt, and verified pieces are not fetched again. The test checks dispatch against the actual verified timing estimates. A busy host may make loopback TCP slower than the paced radio model, so TCP is not assumed to win. These timings are not physical throughput measurements.

Run Meshticulum's standalone suite for carrier/service regressions. Run Lantern's integration suite separately for private policy, SDK and native-job regressions; there is no optional SAGE suite in the Meshticulum package.

The recorded native public checkpoints cover a finite C→A LoRa→B IP request/reverse reply and a separate original-deadline loss/LoRa-resume/measured-IP-return campaign. Further execution requires current host/device ownership and a finite handoff. Private native lifecycle, owner/consent, transport and sustained-performance qualification remain open. [Performance](PERFORMANCE.md#comparison-with-published-figures) records the reviewed upstream figures and their different boundaries; [the roadmap](ROADMAP.md) and [release readiness](RELEASE_READINESS.md) track the remaining work.
