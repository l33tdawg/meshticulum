# Third-party LoRa backend contract

Status: experimental offline conformance contract. This page and its fixture
open no hardware, network, credential store or radio authority. The tracking
issue is [#44](https://github.com/l33tdawg/meshticulum/issues/44).

## Scope

A backend adapter translates one board/transport implementation into the
existing `RadioProfile`, `SerialWorker`/HAT worker boundaries and durable
attempt semantics. The contract must not change the accepted reliable-flow
implementation, wire session format or application admission rules.

The adapter supplies explicit board facts. A country code, chip name or generic
SX1262 label does not establish a board overlay, pin map, antenna path,
regulatory permission or compatible firmware.

## Normative descriptor

The reusable fixture is
[`tests/backend_conformance.py`](../tests/backend_conformance.py). Its
`BackendDescriptor` requires:

| Field | Requirement |
| --- | --- |
| `backend_name` | Stable printable backend/adapter name |
| `transport_kind` | `serial`, `spi` or `other`; not a user-controlled command |
| `board_id` | A profile-bound board value accepted by the current protocol |
| `firmware` | Exact expected firmware/application version |
| `boot_id` | 16-byte boot/session identity expected before ARM |
| `bounds` | Explicit waveform, power and cumulative budget values |

`BackendBounds` maps these values into the existing `RadioProfile`:

- frequency, bandwidth, spreading factor and coding rate;
- chip power, preamble and sync word;
- lease duration;
- cumulative boot attempt and reserved-airtime ceilings.

Every bound is a backend-supplied integer. Validation is fail-closed and occurs
before an attempt can be reserved. The descriptor is JSON-serializable evidence,
but it is not a grant or device identity proof.

## Lifecycle contract

A conforming adapter must preserve these ordered boundaries:

1. **Identity**: exchange HELLO and require the exact board, firmware, boot and
   transport identity before constructing a worker authority.
2. **Profile**: encode and read back the exact board-specific profile. Reject
   unsupported modulation, waveform or bounds before ARM.
3. **ARM**: echo the complete profile, bind it to the attempt journal and live
   caller authority, and refuse transmission without a confirmed arm reply.
4. **Transmit**: reserve durable attempt/airtime debt before the first carrier
   write. Accept only driver evidence bound to the exact boot, request and
   ciphertext hash.
5. **Receive**: return raw ciphertext plus bounded radio metadata. It is not
   authenticated delivery; the existing wire/session and application layers
   must validate it.
6. **Unknown/refusal**: retain reservations and debt. An unknown outcome fences
   the run rather than manufacturing a successful completion.
7. **Disarm**: require an explicit confirmation and make later transmission
   impossible. Cleanup never creates a fresh authority window.

## Offline conformance harness

[`FakeBackendChannel`](../tests/backend_conformance.py) is a deterministic
serial-channel fixture with selectable identity, ARM, refusal, transmit,
unknown, digest and boot faults. The tests in
[`test_third_party_backend_contract.py`](../tests/test_third_party_backend_contract.py)
cover:

- descriptor/profile validation and board power bounds;
- firmware/board identity rejection before ARM;
- ARM echo, transmit evidence, raw receive and disarm ordering;
- refusal without completion evidence;
- unknown result with retained debt and a write fence;
- expiry refusal before reservation.

This is a template, not a hardware driver. A contributor should copy the
descriptor/fields into a new adapter test while keeping the real worker,
journal, authority and flow boundaries unchanged.

## Claim matrix

| Evidence | May establish | Does not establish |
| --- | --- | --- |
| Descriptor/profile validation | Adapter maps explicit bounds to accepted contracts | Board compatibility, RF path or regulatory permission |
| Fake-channel tests | State-machine, refusal, debt and cleanup logic | Physical transmission, firmware/hardware behavior or range |
| HELLO/ARM readback | Exact factory/boot/profile identity for that test | Authenticated delivery or application acceptance |
| Worker transmit evidence | Driver observed the requested carrier operation | Peer receipt, durable content verification or throughput |
| Receive event | Raw bytes reached the modeled/real transport adapter | Session authentication or trusted content |

A real backend must later provide current boot/firmware evidence, a fresh
finite authority, antenna/regulatory confirmation, matched transmit/receive
observations, peer receipt, content verification and bounded cleanup. Those
observations need their own source, hardware, profile and date pins.

## Contributor checklist

1. Pin the exact board, overlay/pin map, radio chip, firmware and boot identity.
2. Declare all profile and cumulative budget bounds as backend inputs.
3. Implement the identity/ARM/transmit/receive/disarm lifecycle without
   changing `flow.py` or `flow_port.py`.
4. Add adapter-specific fake-channel fault controls and deterministic tests.
5. Keep unknown outcomes fenced and reservations retained.
6. Document the later hardware evidence required by the claim matrix.

Meshtastic presets, MeshCore/Reticulum wire compatibility, autonomous routing,
country-to-frequency inference and real RF qualification are out of scope for
this contract.
