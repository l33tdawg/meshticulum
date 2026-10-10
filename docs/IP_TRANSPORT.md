# Authenticated TCP source prototype

The `meshticulum.ip` package carries the unchanged MT05 reliable flow over an explicitly provisioned TCP connection. It adds larger authenticated frames and durable stream-byte accounting without reusing the radio airtime ledger. Real loopback controls cover public MR1 request/reply, signed-pack chunk verification, malformed frames, replay, revocation, original deadlines and uncertain process/write recovery.

This is source integration. Current device forwarding, full C→A LoRa→B IP, physical failover and production private admission remain open. There is no listener, DNS discovery, connection daemon, MeshCore/Reticulum gateway or IP hardware launcher in this increment.

## Bindings and ownership

[`IpGrant`](../src/meshticulum/ip/wire.py) binds complete peers, independent session/incarnation values, the TCP endpoint pair, service format, exact object size/digest, route binding, epoch expiries and finite limits. Its separate `meshticulum.ip.v1` key/authentication context avoids interpreting TCP as the radio protocol. Pairwise secrets are provisioned independently; this PSK mode does not supply forward secrecy or identity enrollment.

[`IpAuthority`](../src/meshticulum/ip/authority.py) reads a private local grant, secret and active marker, checks their original identities and contents, and makes a durable single-use claim. The host supplies its boot identity and native epoch/monotonic clocks independently of peer input. A dispatch lease holds the revocation lock, checks current ownership and belongs to its acquiring thread. Closing from another thread waits for that lease to end; closing from inside the lease is refused. Original application deadlines are supplied by the native provisioner, may only tighten, and cannot be rebound on replay-store reopen.

[`TcpWorker`](../src/meshticulum/ip/worker.py) adopts an already connected IP stream socket by detaching the descriptor from its caller. It validates the actual local/remote endpoints and the journal owner before use. The host must separately authorize socket setup and protect this process and its descriptors; a Python record or file hash is not proof of native authority. The worker creates no connection and performs no radio operation.

## Framing, counters and reliable flow

Each bounded packet has a four-byte stream length, a typed session/counter header and a ChaCha20-Poly1305 body. Lengths are checked before allocating a body. Separate directional keys are derived from the complete grant context. DATA and ACK lanes must agree with the authenticated inner MT05 frame.

The [IP session store](../src/meshticulum/ip/state.py) adapts the existing durable nonce/replay implementation for IP grants. Transmit counters commit before encryption; receive replay admission commits before plaintext is returned. The store retains the original boot, declared monotonic deadline and application epoch expiry. Missing state, a changed boot, retired state or a different declared deadline cannot silently restart it. Filesystem deletion/rollback protection is still the native deployment's responsibility.

[`IpFlow`](../src/meshticulum/ip/flow.py) uses the frozen `ReliableFlow`, including its receiver-issued startup credit, bounded window, receipt revisions, selective ACKs and retries. Its byte/reassembly bounds enforce the exact admitted object. Unmeasured socket serialization adds no invented queue delay; explicit source RTO bounds remain policy to qualify, not a measured network optimum.

## Byte accounting and uncertainty

[`IpJournal`](../src/meshticulum/ip/journal.py) owns a separate SQLite/WAL journal under an exclusive file lock. It checks the live lock descriptor as well as the journal and owner-file identities before every transaction. Losing or replacing that descriptor permanently refuses further use of the instance; reopening a file does not repair its ownership. Closing from another thread waits for an active transaction, and closing inside that transaction is refused. Cleanup avoids closing an unrelated descriptor that has reused the old descriptor number. It reserves the whole framed packet before a socket write. DATA, ACKs and retransmissions all consume the same finite lifetime transmit-byte budget. Known partial kernel writes are recorded; errors, interruption or an unresolved process-recovery reservation remain UNKNOWN and fence new dispatch. Closing or cancelling a flow does not refund the reservation.

A read intention also commits before `recv`. Known reads settle their actual count; a recovered unresolved read charges its possible bytes and fences the run. Reads stay within the remaining receive allowance. Packet lengths, receive buffers, object memory, attempts and lifetime bytes are bounded.

`KERNEL_ACCEPTED` describes local kernel acceptance of the complete framed packet. It does not mean `RF_DONE`, peer receipt, publisher verification, private admission or canonical SAGE commit. Journal byte counts describe the application socket boundary; IP/TCP headers, kernel retransmission bytes and RF airtime are unmeasured. Unknown writes can leave known kernel-byte counts as a lower bound; their full reservations remain charged.

## Service integration

The existing [ServiceTransfer](../src/meshticulum/bench/services.py) recognizes the concrete IP flow/authority family in addition to its radio family. It still verifies exact object bytes, digest and public/private format under the held application lease. IP imports are lazy so the frozen public radio bundle does not gain an IP dependency or enable an IP dispatch path.

`PreparedBlockExchange` uses fresh independent request and response sessions, reversed peers and one original resource expiry. The loopback control drives MR1 over TCP and passes the returned chunk through the existing signed-manifest/content verifier. Resumption can retain verified pieces; it must use newly admitted wire sessions rather than resetting old counters or debt.

Private IP dispatch still requires the existing exact-grant native `PrivatePolicy` and durable capture/inbox sink. The default runner refuses private traffic without those providers. Private sealed records are not public resources, and the prototype does not qualify a real encrypted backend or enable a private daemon.

## Controls and remaining work

Run the carrier and lifecycle controls with:

```sh
python -m pytest tests/test_ip_transport.py tests/test_ip_ownership.py tests/test_mixed_carriers.py tests/test_mixed_resume.py
```

These controls use actual loopback TCP, synthetic provisioned peers and modeled radio workers. They cover:

- Lost and reused owner descriptors, concurrent close, same-thread close refusal and retained unresolved reservations.
- C→A modeled radio→B actual TCP request/reply, preserving A's bridge identity, signed-pack verification, original expiry and each carrier's separate costs. TCP loss stops the exchange before a radio reply.
- Verified chunks retained across TCP loss, fresh radio request/reply admissions, a new IP route generation and return to the faster locally verified TCP source. Every block uses independent sessions; failed attempts keep their debt, and verified pieces are not fetched again. Deliberate model-radio pump pacing makes this scheduler control repeatable. Its timings are not physical throughput measurements.

Run the standalone and optional SAGE suites for service-registration regressions.

The next qualification must cover current host/device ownership, actual C→A radio forwarding into A→B IP, connection loss, fresh LoRa admission with verified-piece resume, and return to the measured faster IP path. Native lifecycle and backend tests are additionally required for private messages. Version-bound, matched MeshCore/Reticulum benchmarks remain separate. See [the roadmap](ROADMAP.md).
