# Optional HAT receive observations

`radio_file.py transfer --hat-receive-reasons` enables fixed counters on the HAT interface. The option defaults off, rejects USB, and works independently of `--profile-host`. The original file owner binds the option. Enabling it adds no SPI operations, clock reads, packet identifiers or radio configuration changes. Existing error propagation, containment, ownership and deadline checks remain in place.

The `hat_receive_reasons` object is exposed by the flow snapshot and the public transfer result. A failed public transfer carries the same object on its exception when a worker was constructed. Collection remains local to that worker; a new worker starts a new observation lifetime. This is diagnostic state, not authority or persistent RF accounting.

| Counter | Existing observation |
| --- | --- |
| `irq_crc`, `irq_header`, `irq_crc_header` | CRC/header bits in an existing poll's IRQ snapshot; the three classes are mutually exclusive per observation. |
| `irq_timeout` | Timeout bit in that same snapshot; may overlap an error class. |
| `zero_length` | Existing receive-buffer length reply reports zero. No new FIFO read is attempted. |
| `packet_read_failure` | Exception during the existing length/FIFO/packet-status/evidence reads. |
| `physical_packet_returned` | Driver returns a nonempty physical packet after IRQ cleanup; this precedes worker rearm and authentication and is not accepted DATA. |
| `poll_failure` | Exception inside the driver's receive poll; includes packet-read failures. |
| `rearm_attempt`, `rearm_failure` | Receive-method RX start/rearm call and exception, including authority refusal before SPI. These exclude initial construction and TX-to-RX restoration. |
| `receive_failure` | Worker receive body raises; overlaps driver/rearm failures and includes ownership/lease/authorization rejection. |

All counters saturate at uint32 maximum; `saturated_mask` marks increments that could not be represented. A zero mask permits exact counter differences only within the same worker lifetime. The fixed names and bounded counts keep output bounded; no event history is retained. Snapshots are detached copies.

The counters intentionally overlap and must not be added into a loss total. In particular, a malformed or unrelated radio frame can trigger an error without identifying a missing authenticated DATA frame. The existing upper-layer `rx_refused` observation remains separate: these counters neither authenticate packets nor classify cryptographic refusal. No claim of RF delivery or improved throughput follows from modeled tests.

Source tests compare exact SPI command traces with collection disabled/enabled for valid, empty, CRC/header/timeout, read failure and worker rearm failure paths, and verify original exceptions and journal debt are unchanged.
