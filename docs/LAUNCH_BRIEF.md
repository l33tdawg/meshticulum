# Meshticulum launch brief

For project-announcement material after the repository and website are confirmed public. `1.0.0-alpha` will have its own later announcement.

## The story

A radio network should carry more than a short message. It should move a signed reference pack, recover the missing pieces of a download and use a faster local connection when one becomes available. The data still needs a verified author, and private messages need to stay encrypted to their recipient as they cross relays.

That's the work behind Meshticulum. Created by Dhillon Andrew Kannabhiran under the SAGE Foundation, it brings practical small-radio operation and authenticated networking together in an experimental protocol for LoRa and IP. MeshCore's embedded radio approach and Reticulum's broader secure networking model helped shape the priorities. Meshticulum has its own protocol; existing MeshCore and Reticulum peers cannot join it unchanged.

Its reusable core covers reliable flow, cryptography, drivers and verified public resources. Lantern builds on that core and keeps its product code, STL/printing files, private SAGE adapters and deployment policy in a separate repository. Other applications can use Meshticulum without taking on Lantern or the SAGE SDK.

## Facts to use

- The latest accepted finite radio response delivered 16,356 verified resource bytes at **1.071 kB/s** from sender start to final flow ACK, with zero DATA retries or duplicates. The full request-to-publisher-verification interval was **80.731 seconds**, or **202.6 B/s**.
- **2.61×** is the numerical ratio to the **410 B/s** MeshCore test published by Dhillon in [issue #3404](https://github.com/meshcore-dev/MeshCore/issues/3404). Both state SF7/BW500, but use different workloads, devices and timing boundaries. Phrase it as a scoped comparison between published observations.
- Thirty earlier radio samples recorded about **1.2 kB/s median** useful encrypted goodput. That experiment used an earlier runtime; it is separate from the latest native service samples.
- Finite public campaigns have demonstrated LoRa/IP delivery, verified-piece retention through IP loss, fresh LoRa resume and return to a locally measured faster IP path.
- Compact private envelopes preserve the existing encrypted and signed content while removing text-carrier overhead. Public resources use publisher-signed manifests and content hashes.

Link numerical claims to [Performance](PERFORMANCE.md) and security claims to [Comparison](COMPARISON.md). Sustained whole-file throughput, successful 100 kB/1 MB radio bundles, native private forward secrecy, autonomous routing and better overall speed/security than both upstream projects remain development goals. Reticulum's cited LoRa bitrate is in **kb/s**, not **kB/s**, and is not a comparable encrypted application result.

## A short announcement draft

We're building Meshticulum: secure messages and verified resources across small radios and faster networks.

The idea grew out of our work on SAGE Lantern. We wanted small radio transfers to stay bounded and useful, while giving applications a way to verify complete data and move it between LoRa and IP. Meshticulum now has its own reusable core, with architecture diagrams, source and measured results open for review.

This is a project preview. The alpha release comes later. If you work on radio networks, offline tools or secure data transfer, we'd like you to take a look.

A SAGE Foundation project, created and maintained by Dhillon Andrew Kannabhiran.

## Links and artwork

| Material | Destination |
| --- | --- |
| Project website | [Meshticulum](https://l33tdawg.github.io/meshticulum/) |
| Source | [Meshticulum repository](https://github.com/l33tdawg/meshticulum) |
| Main project | [SAGE](https://github.com/l33tdawg/sage/) |
| Stewardship | [SAGE Foundation](https://sage-foundation.net/) |
| Product integration | [SAGE Lantern](https://github.com/l33tdawg/sage-lantern) |
| Scalable logo | [SVG](../website/assets/meshticulum-logo.svg) |
| Transparent logo | [1024 px PNG](../website/assets/meshticulum-logo.png) |
| Article share card | [1200 × 630 PNG](../website/assets/social-card.png) |
| Brand colors and artwork source | [Brand guide](../website/assets/BRAND.md) |

The announcement can link to the technical overview, comparison tables and SDK/device guides on the website. There is no ready-to-use alpha firmware download in this preview. Device instructions describe the experimental source build and its current limits.
