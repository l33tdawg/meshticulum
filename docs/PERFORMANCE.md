# Performance and measurement

Meshticulum is being built to turn more of a small radio link's capacity into useful, verified application data. The retained single-radio bench has already delivered **about 1.2 kB/s median useful encrypted goodput** on three LoRa directions. The upper programme target is **1.5 kB/s**. The available records support the first number; they do not yet establish the second or a speed advantage over MeshCore or Reticulum.

## Retained radio results

These are the 2026-10-09 measurements from the earlier authenticated bench runtime. Each direction has ten separate finite transfers of 8,192 synthetic incompressible application bytes, with an independently required durable receiver hash. Rates include sender startup and receipt of the reliable-flow acknowledgement.

| Direction | Completed RF samples | Min / median / max goodput, B/s | Median / slowest observed transfer, seconds | DATA retries |
| --- | ---: | ---: | ---: | ---: |
| C→A | 10 / 10 | 1,113.9 / **1,211.1** / 1,248.2 | 6.764 / 7.355 | 0 |
| C→B | 10 / 10 | 1,182.6 / **1,231.1** / 1,273.1 | 6.654 / 6.927 | 0 |
| A→B | 10 / 10 | 688.5 / **1,188.2** / 1,232.0 | 6.894 / 11.898 | 4 |

![All ten samples per direction, their medians, and the 1.5 kB/s target](diagrams/radio-goodput.svg)

All **30 RF samples completed**; **29 of 30 reached 1,000 B/s**. The slower A→B sample remains in the table and figure. Four DATA retransmissions occurred in that direction. The directional distributions stay separate; pooling them would hide the link and retry differences.

There were **31 management-session attempts**, including one SSH provisioning failure before RF. That failure is retained separately: RF-sample completion is 30/30, while managed-attempt completion is 30/31. At ten samples per direction, nearest-rank p95 is the slowest observed sample; it is not a population tail-latency guarantee.

The allowlisted per-run data, source capture hashes and aggregate values are in [radio-results.json](../benchmarks/2026-10-09-radio/radio-results.json). All 30 original `RUN.json` files and paired collected records were replayed offline through the [operator-capture analyzer now owned by Lantern](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/firmware/meshticulum-bench/analyze_runs.py); their per-direction summaries matched the retained report exactly. That replay checks capture consistency. The original operator captures remain in the lab workspace; the published metadata does not independently attest the hardware or contain grants, secrets, device addresses, boot identities or payload content.

## Test conditions

| Item | Recorded condition |
| --- | --- |
| Radio arrangement | One radio per device; A, B and C together in the bench setup |
| Modem profile | SF7, 500 kHz bandwidth, CR4/5, 921.375 MHz, eight-symbol preamble |
| Power | 0 dBm chip setting; actual EIRP was not measured |
| Payload | 8,192 synthetic incompressible application bytes per session |
| A/B implementation | Raw LoRa test coprocessor `mtb1-9816f92488`; host serial worker |
| C implementation | Custom raw SX1262 SPI host driver |
| Host runtime archive | `8739037c030c39241253f2721ef3fa23d698dc951be014a0b9b153aa5341fe7b` |
| Runtime source manifest | `b7b2386de6e619712c817e41ced4d56fc4231f7a1ffb2f918436d51d83791bb2` |
| Repetition | Ten sessions per direction; recorded disarmed A/B MCU reset boundaries, unchanged host boots |
| Placement measurements | Distance, antenna orientation and antenna gain unavailable |

The modem's faster settings trade link margin for rate. These are finite bench transfers, not sustained shared-channel capacity, a field-range result or a minimum throughput guarantee under loss. The measured runtime predates the standalone extraction and newer MR1/private-capture service composition. Those newer paths require their own current hardware qualification.

The encrypted transport in this benchmark is the authenticated session wire carrying synthetic application bytes. It does not measure a production private inbox, recipient-envelope delivery, publisher verification timing or canonical SAGE commit.

## Exactly what “goodput” means

```text
goodput_Bps = verified_payload_bytes /
              (sender_flow_ACK_monotonic_time - sender_start_monotonic_time)
```

The interval uses **one sender's monotonic clock**. A completed sample additionally requires the receiver's durable chunk hash, correlated DATA/ACK evidence and containment records. The rate's endpoint remains the sender's flow acknowledgement; receiver hash verification is a separate required observation. We do not subtract clocks on different hosts or relabel this as one-way latency.

Use decimal units consistently:

- **B/s** means bytes per second.
- **kB/s** means 1,000 bytes per second. The measured medians are approximately 1.19–1.23 kB/s.
- **kb/s** means 1,000 bits per second. 1.5 kB/s equals 12 kb/s; 1.5 kb/s equals 187.5 B/s.

The retained records use B/s. No retained repeated sample reviewed for this publication reaches 1,500 B/s. The **1–1.5 kB/s programme target** is tracked alongside completion, latency and retry cost, rather than reported as an achieved 1.5 kB/s result.

## Where the useful-byte gains come from

The radio optimization combined several changes, which the retained bench report records together:

- A faster admitted LoRa profile.
- Four-packet bursts with coalesced selective/cumulative ACKs and conservative receiver credit.
- Correct ACK airtime accounting and shorter observed USB pacing.
- SQLite WAL with `synchronous=FULL`, preserving durable reservations while reducing repeated journal-file work.

The A/B MCU image remained fixed during that optimization. These observations show the combined runtime/profile outcome; they do not isolate the effect of each change or attribute all gains to protocol design.

The newer [compact envelope codec](../src/meshticulum/privacy/mesh_binary_envelope.py) removes outer text/Base64 overhead while retaining the same sealed ciphertext and sender-signature contract. [MR1](../src/meshticulum/resources/resource.py) transports bounded raw resource blocks instead of carrying HTTP/batch wrappers over the radio. [Path scheduling](../src/meshticulum/resources/swarm.py) learns from verified resource timings and accounts for shared bottlenecks. These are further source mechanisms to qualify; their savings must not be retroactively added to the older radio measurement.

## What remains unmeasured

Measured RF airtime, energy, EIRP and one-way latency remain **null** in the published record. Worker airtime reservations are source estimates; driver-operation elapsed time is not a substitute for an RF instrument. A reported local `RF_DONE`, peer receipt, durable storage and application acceptance each have a different evidence boundary.

The earlier bench also retained explicit-session revocation and verified-chunk recovery, and operator-coordinated forwarding of a stored radio-received object. Those demonstrations do not establish byte-offset recovery after a device power loss, autonomous routing or a later C→A LoRa→B IP service campaign. The finite native public [mixed and loss/resume/IP-return checkpoints](IP_TRANSPORT.md#recorded-native-public-resume-and-ip-return) carry their own pins, verification and terminal evidence. Their tiny service timings include management and final-ACK windows and do not establish sustained capacity. Historical results remain bound to their exact runtime and conditions.

The historical extraction validation and installed-wheel checkpoint are recorded in [VALIDATION.json](../provenance/VALIDATION.json). Native-job and optional SAGE application controls now belong to [Lantern](https://github.com/l33tdawg/sage-lantern/tree/codex/meshticulum-boundary/software/meshticulum-integration). Later source results must identify the exact core and Lantern commits and their validation scope; they do not retroactively qualify the historical runtime. Source controls, modeled workers and loopback HTTP serve a different purpose from a hardware benchmark.

## A fair MeshCore / Reticulum comparison

No matched MeshCore or Reticulum hardware sample is present in this record. Their fields remain **null**; the documentation does not rank their speed. See [the architectural comparison](COMPARISON.md) for capabilities and design differences.

The planned comparison must hold the radio hardware, antenna/placement, modem settings, power, topology, useful encrypted payload and finite medium budget constant. It must identify each exact software/firmware version and use comparable trust and completion semantics. A message acknowledgement, completed Resource and durable verified object must not silently become the same endpoint.

Capture cold and warm costs separately, including discovery/session setup where applicable. Keep requests, responses, ACKs, retransmissions, timeout/refusal rates, latency distributions and reverse-medium traffic. Retain every attempted run and unavailable metric. Report single-hop, stored relay and mixed-media tests separately. Faster PHY settings or a best-case burst alone cannot support an upstream speed comparison.

The recorded finite public campaigns exercised authenticated IP forwarding and, separately, IP loss followed by **fresh C↔B LoRa admission**, verified-piece resume under the original resource deadline, and return to a locally measured faster IP path. Broader private, lifecycle and sustained-performance qualification remains open. [Architecture](ARCHITECTURE.md) shows that design; [ROADMAP.md](ROADMAP.md) records the remaining gates.

## Reproduce the published figure and arithmetic

The published JSON contains each sample's payload size, sender interval, goodput, retries and matching packet counts. Check the arithmetic and per-direction summaries with:

```sh
python tools/verify_benchmark_report.py
```

Rebuild the static figure in a separate documentation environment with Matplotlib:

```sh
python -m pip install matplotlib
python tools/render_benchmark_plot.py
```

The generic [path report](../tools/benchmarks/meshticulum_path_report.py) validates bounded metadata traces and groups different source/profile/path/authority/clock bindings separately. It retains failures and missing metrics without making requests. Input provenance is a caller claim; its offline arithmetic does not authenticate a capture or qualify physical mixed-path capacity. Native controller and device-run evidence collection belong to [Lantern's pilot runbook](https://github.com/l33tdawg/sage-lantern/blob/codex/meshticulum-boundary/docs/lantern/MESHTICULUM_PUBLIC_PILOT.md).

These commands process published metadata and touch no devices. Replaying the original capture-consistency analysis requires the retained lab records; running a fresh hardware benchmark requires current device ownership, explicit finite grants and the checks described in [HARDWARE.md](HARDWARE.md).
