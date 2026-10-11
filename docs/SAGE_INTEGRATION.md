# Lantern and SAGE integration boundary

Meshticulum is a [SAGE Foundation](https://sage-foundation.net/) project created and maintained by [Dhillon Andrew Kannabhiran (@l33tdawg)](https://github.com/l33tdawg). Private SAGE integration belongs to the separate [SAGE Lantern repository](https://github.com/l33tdawg/sage-lantern), within the [SAGE project family](PROJECT_FAMILY.md). The main SAGE code is in [l33tdawg/sage](https://github.com/l33tdawg/sage/).

Lantern consumes Meshticulum as a one-way dependency. Its `software/meshticulum-integration/` owns protected inbox admission, durable sealed capture, WorkflowStore/storage interfaces, private lifecycle policy and native device entry points. Its `firmware/meshticulum-bench/` owns the runtime bundles, grant bootstraps and device/pilot deployment tooling. SDK installation and private integration tests are maintained in Lantern, not in this repository.

Meshticulum provides generic identity/deadline contracts, authenticated transport, verified public resources and offline envelope codecs. It has no SAGE SDK extra and no dependency on Lantern services, protected storage or private application tests. An application must supply its own current authorization and storage policy; importing a codec or constructing a contract grants neither.

The source split preserves existing envelope protocol identity, signature domains and sender/recipient pinning. It does not enroll identities, rotate keys, import received packs into SAGE, produce read receipts or establish a canonical SAGE commit.

Lantern's private controls must use its documented compatible SDK and pin the exact Meshticulum candidate. SDK MockTransport and modeled ownership remain source evidence. Real encrypted backend, native owner/consent/revocation authority, original boot/deadline recovery and physical worker qualification still gate private dispatch. Follow Lantern's current integration instructions and [the remaining qualification](ROADMAP.md); no private production enablement is implied by this boundary.

Publishing Meshticulum's reusable source and project documentation is separate from enabling a Lantern service or releasing an alpha. The public project does not carry Lantern credentials, deployment configuration or protected native policy implementations.
