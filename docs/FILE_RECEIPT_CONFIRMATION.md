# Optional file receipt confirmation

`examples/radio_file.py transfer --confirm-receipt` enables the existing
accounted authenticated confirmation on both peers, including USB senders and
HAT receivers. The default remains the original finite receiver ACK grace.
Use the option on both endpoints of a fresh, explicitly admitted operation.

The file context binds the complete validated manifest, chunk index and
original file issue/expiry timestamps. It excludes host-local clocks and boot
identities so the peers derive the same context; the authenticated endpoint
binding separately retains the grant, flow, generation and direction. It uses
a file-specific domain distinct from bundle confirmation.

A receiver becomes ready for confirmation only after `VerifiedChunks.commit`
returns and the original authority remains live. A sender offers confirmation
only after receiving the authenticated final ACK. The extra CONTROL attempt
uses the existing journal, owner and airtime allowances. A lost or mismatched
confirmation preserves the original nonrenewing ACK grace. No new lease or
retry budget is created, and insufficient original time still refuses work.

For admitted SF5 burst transfers, enabling this existing endpoint mode also
makes the existing selective-repair path eligible. Its SACK evidence, original
FINAL, physical drain, credit, ownership and deadline gates still apply. The
option does not force repairs or claim a throughput improvement. Source-model
tests are not physical delivery or performance qualification.
