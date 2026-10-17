# A signed 100 kB file over the existing MB1 service

[`radio_bundle.py`](radio_bundle.py) uses the public SDK's existing
`byte_bundle`, MB1 encoding, `ServiceTransfer`, reliable flow, authenticated
frames, durable journals and `byte_bundle.Receiver`. Its baseline path needs no
firmware, RF cap, retry limit or frozen-flow change. The hardware and ownership
requirements are the same as the [generic example](README.md): the verified
Orange Pi 5 Plus SX1262 HAT sends DATA; a Heltec V4 receives and sends ACKs.
On 17 October, this public SDK path completed **two fresh five-part 100,000-byte
files at 2,030.2 and 1,949.2 useful B/s including sender startup**, with independent
publisher and whole-file verification. Both endpoints used receipt confirmation
on a direct GFSK250 link about one metre long. [The measurement report](../docs/RADIO_SDK_MEASUREMENTS.md)
records the exact source and timing; broader repetition and deployment remain open.

## Why five transfers

The existing byte-bundle builder uses parts of at most **23,900 useful bytes**.
A 100,000-byte file therefore uses five parts. MB1 carries their raw bytes and
reconstructs the exact signed canonical public chunks at the receiver. Each
MR1 response fits the existing explicit `public_response_max_bytes=32768`
capability and uses its separately admitted transport descriptor hash.

Five receiver ACK graces cost **100 seconds**, leaving at most **140 seconds**
of the same original 240-second interval for DATA, setup, retries, verification
and cleanup. At 1,000 useful B/s, useful DATA alone takes 100 seconds; protocol
and host work still need measured headroom. These bounds make this route a
candidate for qualification, not a promise that it completes on your hardware.

The 1,000,000-byte SDK bundle-format limit is not a radio-operation limit.
The command rejects `part_count × 20 seconds >= original interval`, and rejects
remaining graces that cannot fit the actual remaining original monotonic time,
**before hardware is opened or a grant is claimed**. Data and setup need further
time. No timer or lease is extended to finish a part or a grace.

## Optional sender receipt confirmation

Pass `--confirm-receipt` on both endpoints to let the receiver close its ACK
grace as soon as the sender confirms receipt. The sender offers one authenticated
CONTROL packet only after the existing reliable flow has accepted the final ACK.
The receiver accepts it only after verifying and durably accepting that part.
The confirmation binds the original session, peers, direction, flow, object,
resource and file window. It uses the same scheduler, authority leases, durable
nonce state, attempt journal and radio worker as DATA and ACKs.

This adds one 79-byte wire packet and one charged sender attempt per part; the
32-byte control payload fits the existing negotiated bound. `RF_DONE` proves
that local transmission completed, not that the receiver heard it. If the
confirmation is lost, unsupported or invalid, the receiver keeps its original
20-second grace. It never restarts that grace or extends a lease. With the flag
off, behavior stays the same. Mixed settings also keep the full receiver grace.
An uncertain write or failed cleanup remains a command failure.

Successful confirmations avoid the five sequential waits in the normal case.
The preflight still reserves all remaining graces so that loss retains the
original fallback. Always measure actual DATA/ACK delivery, verified-file
availability and complete command closure separately. In the two recorded 100 kB
samples, all ten receiver confirmations were accepted. Both endpoints closed
in **53.879 and 56.957 seconds from scope issue**, or **1,856.0 and 1,755.7 useful B/s**.
This initial repeat does not establish a capacity distribution or a guarantee for another setup.

## Build and authenticate the public bundle offline

Use public file bytes you can distribute under the builder's CC0-1.0 metadata.
Transport encryption does not turn this public-resource example into protected
application storage. Supply your own Ed25519 publisher key as 64 lowercase hex
characters in a private `0600` file. The command does not generate or enroll a
trusted publisher key:

```sh
.venv/bin/python examples/radio_bundle.py build \
  --input input.bin --output new-pack --signing-key publisher-key.hex \
  --pack-id my-public-file --created-at 2026-10-15T00:00:00Z
```

It writes the existing signed pack format and prints its manifest digest and
whole-file descriptor. Independently provision the publisher's public key in
your existing public-resource trust-root file. Use the
[`knowledge_pack` trust-root contract](../src/meshticulum/resources/knowledge_pack.py)
(`key_id`, `public_key_hex`, active status and permitted `reference` pack class).
The signer/key and trust-root decision are yours; no radio discovery or first-use
key acceptance occurs. Transfer the signed manifest/signature and independently
checked trust-root hash through your trusted administration channel. The sender
also needs the canonical chunk files. The receiver does not need payload files
before DATA arrives.

## Approve five original response grants

Use the [same private grant-directory rules](README.md#prepare-the-file-and-approve-the-sessions),
actual host boots, independently distributed pairwise secret, explicit admission,
fresh per-part sessions and exclusive workers. All five grants at each endpoint
share the **same original issued/expiry epoch values**, no more than 240 seconds
apart. They must be approved before the operation. This example does not issue
grants, widen an old grant or run a native product controller.

For each part, the following offline command prints the existing protocol
`object` and `service` fields for your application to approve. It prints no
secret, native permission or complete grant:

```sh
.venv/bin/python examples/radio_bundle.py describe \
  --pack new-pack --manifest-sha256 "$MANIFEST_SHA256" \
  --trust-root trust.json --trust-root-sha256 "$TRUST_SHA256" \
  --part 0 --file-expires-epoch-us "$ORIGINAL_EXPIRES_US"
```

Use `meshticulum.service-run.v1`, `radio_modem=gfsk250`, exact object fields,
`kind=knowledge`, `format=MR1.response`, the original application expiry,
`public_response_max_bytes=32768`, explicit representation `MB1`, and the exact
`public_transport_descriptor_sha256`. The descriptor binds the signed chunk
content address and the response length/hash. Both endpoints admit the same
values. An MB1 payload under a legacy or merely relabelled grant refuses.

The prepared MR1 request binding is deterministic from the authenticated pack
entry and approved response length. This example pushes the preapproved response;
it does not claim that an additional request was sent over RF or charge a
fictional request. The service/AEAD route and response object remain exact.

Per-run profile limits remain at most 256 attempts and 60,000,000 estimated
airtime microseconds. HAT DATA uses distinct approved run journals; all old
journals and debt remain. MCU boot attempts/airtime remain cumulative across
all five sessions, and the first MCU boot/version is pinned in local file state.
A reset or changed MCU identity refuses instead of resetting a file budget.
This route does not enable a 100 kB MCU DATA sender.

## Run each approved pair

Create a separate root-owned `0700` state directory on each host. Its file-window
record retains the first actual host boot, epoch/monotonic observation and
original monotonic end. Every later worker/owner check uses that retained end;
a wall-clock rollback cannot renew it. Do not edit, delete or reuse this state
to bypass a failure or deadline. The receiver's existing SDK quarantine also
retains its publisher binding and original boot/deadlines.

Start each receiver command first, then its HAT sender promptly:

```sh
sudo .venv/bin/python examples/radio_bundle.py transfer \
  --interface usb --device "$BY_ID" --usb-serial "$USB_SERIAL" \
  --confirm-receipt \
  --unit "$RECEIVER_UNIT" --grant-directory "$RECEIVER_GRANT" \
  --grant-sha256 "$RECEIVER_GRANT_SHA256" --state "$PRIVATE_STATE" \
  --pack received-pack --manifest-sha256 "$MANIFEST_SHA256" \
  --trust-root trust.json --trust-root-sha256 "$TRUST_SHA256" \
  --admission admission.json --admission-sha256 "$ADMISSION_SHA256" \
  --firmware-source SOURCE.json --firmware-source-sha256 "$SOURCE_SHA256" \
  --file-issued-epoch-us "$ORIGINAL_ISSUED_US" \
  --file-expires-epoch-us "$ORIGINAL_EXPIRES_US" --part 0
```

On the sender, use the corresponding sender grant, `--interface hat`, sender
unit, sender state directory, `--confirm-receipt` and full `--pack new-pack`; omit `--device` and
`--usb-serial`. Keep all hashes, original clocks and part index consistent.
No additional `--input` is used: the signed canonical source chunk supplies DATA.
The command arms its hardware and may transmit encrypted DATA or ACKs.

The receiver verifies AEAD, exact MR1 response binding, MB1 reconstruction,
publisher signature/content address and the original whole-file descriptor.
It durably accepts the part, then continues the same flow until a valid sender
receipt confirmation arrives or the existing 20-second ACK grace ends. The
grace start is never renewed. Cleanup disarms and
retires the session; uncertain attempts and failed cleanup remain failures.
After both commands close successfully, coordinate the next approved pair
through your application/operator channel. Parts must arrive in order; local
sender receipt markers and reverified receiver quarantine prevent skipping them.
There is no new cross-host daemon, RF grant issuer or automatic retry campaign.

For part 4 of a 100 kB file, the receiver re-verifies all five parts and the
signed whole-file length/SHA-256, then durably publishes
`$PRIVATE_STATE/receiver/bundle.bin` within the original file interval. Its result
includes the SDK's `publisher_verified`/whole-file result. Sender flow receipt
alone does not prove this publication. Missing/corrupt parts, wrong signatures,
expired clocks, changed boot, replay or uncertain writes cannot become success.

Keep the original state, grants, session stores, attempt journals and failures.
Source models are feasibility evidence only. Your exact hardware, ownership,
physical completion, useful goodput and notice/release qualification remain
separate gates.
