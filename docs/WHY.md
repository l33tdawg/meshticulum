# Why Meshticulum exists

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg), alongside the [main SAGE project](https://github.com/l33tdawg/sage/).

Meshticulum is being built to make small radio networks useful for both private conversations and verified resources, then connect those services across LoRa and IP. The competitive goal is to make those services faster and better than both MeshCore and Reticulum, including encryption security. We want the practical deployment discipline of an embedded radio mesh and the application capabilities of a secure networking stack, with more of the scarce radio capacity reaching the application as useful data.

MeshCore and Reticulum show two valuable parts of that direction. MeshCore provides a lightweight embedded packet-radio framework and a practical ecosystem of companion nodes, repeaters and room servers. Reticulum provides cryptographic destinations, secure links and reliable resource transfers over heterogeneous carriers. Both already support encrypted communication; Reticulum already connects different media. Our motivation is to explore a particular combination of service boundaries, resource verification and radio scheduling, rather than claim those capabilities are missing from the upstream projects. See the [MeshCore project](https://github.com/meshcore-dev/MeshCore), [Reticulum overview](https://reticulum.network/manual/whatis.html) and [detailed comparison](COMPARISON.md).

```mermaid
flowchart LR
    MC["MeshCore inspiration<br/>small radio nodes, compact packets<br/>and practical repeater operation"]
    RNS["Reticulum inspiration<br/>secure networking, reliable resources<br/>and heterogeneous carriers"]
    M["Meshticulum design<br/>bounded authenticated flows<br/>and verified application resources"]
    G["Goal<br/>useful services across LoRa and IP<br/>with measured efficiency"]
    MC -->|operational principles| M
    RNS -->|networking principles| M
    M --> G
```

[Editable Mermaid](diagrams/why-1.mmd) · [Rendered SVG](diagrams/why-1.svg)

The arrows describe design influences. Meshticulum has its own experimental wire protocol. Joining an unmodified MeshCore or Reticulum network would require a separately designed interoperability adapter.

## The gap we want to bridge

A radio network should be able to carry a private note, a signed public reference pack, or a missing piece of an interrupted download. When a local IP route is available, the same resource should be able to use it. When that route disappears, the receiver should keep the pieces it has already verified and obtain fresh permission for the slower path.

That requires several things to work together:

- **Application trust that survives a relay.** Private messages retain recipient encryption and sender signatures. Public resources retain their publisher signature and content hashes. A gateway forwarding bytes does not become their author.
- **Reliable transfers that fit a small radio.** Receiver credit limits outstanding data. Selective acknowledgements avoid retransmitting everything after one lost frame. Bounded queues, retries and bursts keep transfer behavior finite.
- **A complete cost for scarce media.** Requests, responses, acknowledgements and retries all consume capacity. The radio owner keeps attempt and airtime debt even when an application cancels or a process restarts.
- **Path choice based on useful work.** Only verified resource bytes train the local path estimator. Shared bottlenecks and elapsed request time matter; a route name or advertised rate does not prove which path finishes sooner.

These are implemented source boundaries in the [flow](../src/meshticulum/flow/flow.py), [authenticated carrier](../src/meshticulum/bench/wire.py), [resource verifier](../src/meshticulum/resources/lamplighter.py), [path scheduler](../src/meshticulum/resources/swarm.py) and [service runner](../src/meshticulum/bench/service_runner.py). Lantern recorded finite public mixed delivery and loss/LoRa-resume/measured-IP-return milestones; [their execution record](IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return) separates those results from broader deployment qualification. The [roadmap](ROADMAP.md) records the remaining work.

## Why performance matters

Higher useful throughput changes what a radio network can reasonably deliver. Reducing overhead and unnecessary retransmissions can reduce shared-medium cost; retaining verified pieces avoids transferring them again. Faster delivery alone does not establish lower RF airtime, which remains unmeasured in the retained results.

Meshticulum pursues that efficiency through compact binary envelopes, bounded block transfers, receiver credit, burst scheduling and acknowledgement coalescing. The public MR1 adapter moves raw blocks over the scarce carrier and reconstructs host-side batch envelopes locally, avoiding HTTP and base64 expansion on the radio path. The compact private codec preserves the existing encrypted and signed bytes while removing text encoding overhead. See [MR1](../src/meshticulum/resources/resource.py) and the [binary envelope codec](../src/meshticulum/privacy/mesh_binary_envelope.py).

The latest recorded finite public reply delivered 16,356 verified resource bytes at **1.071 kB/s**, measured from sender start through flow acknowledgement. The earlier encrypted bench recorded thirty transfers with directional medians around **1.2 kB/s**. These are separate workloads and evidence sets. The goal remains **1–1.5 kB/s sustained useful encrypted delivery**, with a later **4–5 kB/s stretch target**; the current measurements do not establish sustained capacity. [Performance evidence](PERFORMANCE.md) compares them with published upstream figures and explains the different measurement boundaries.

Our own transport and application qualification must count useful bytes against the full recorded interval, require receiver verification, and retain loss, retries, completion and latency. Larger bundle tests must verify the decoded original file's length and whole-file hash, with JSON, Base64 and other wire overhead counted separately. Source preparation for those tests is not completed radio delivery.

## Why security is part of the goal

Private conversations need confidentiality and a verified author, but stronger security also depends on replay handling, key lifecycle and what a later key compromise exposes. Meshticulum currently has provisioned PSK transport and static recipient envelopes; neither has proved forward secrecy. Reticulum already documents ephemeral Link keys and optional ratchets for link-less packets. That is a concrete gap to address before claiming better encryption security. The [security comparison](COMPARISON.md#security-properties-and-gaps) links the reviewed source and scopes those properties by mode.

The goal requires an explicit threat model and reviewed key management while preserving peer, consent and ownership boundaries. A different cipher name, a signed message or a fresh nonce alone cannot establish that the whole system is more secure.

## What success looks like

Finite native public campaigns have demonstrated a coordinated LoRa-to-IP transfer and, separately, IP failure followed by fresh LoRa admission and verified-piece resume, then return to a locally measured faster IP path under the original expiry. Success across the wider programme still requires native private admission, lifecycle and performance qualification while preserving peer bindings, deadlines, replay decisions and ownership boundaries. Competitive claims also need evidence against published upstream performance figures and reviewed security properties, with gaps kept visible.

A faster transfer only counts when it delivers the intended verified object under those constraints. The [architecture](ARCHITECTURE.md), [hardware guide](HARDWARE.md), [performance record](PERFORMANCE.md) and [roadmap](ROADMAP.md) track that work. [Lantern](https://github.com/l33tdawg/sage-lantern) consumes Meshticulum and owns its native device policy, deployment and protected SAGE adapters; the dependency runs **Lantern → Meshticulum**.

The project source and documentation can be published while qualification continues. A public repository or project announcement does not certify deployment or release **1.0.0-alpha**; those gates remain in [release readiness](RELEASE_READINESS.md).
