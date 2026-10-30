# SF5 32-symbol-preamble trial: startup failure

The trial failed before file DATA was sent. There is no authenticated file
receipt, completed-file verification, or useful-file goodput measurement.
The 3,000 B/s goal and above-2,800 B/s checkpoint remain unqualified.

It retained the preceding trial's exact SDK/v7 policy, SF5/BW500 profile,
firmware and both MCU boots, changing the preamble from 12 to 32 symbols.
The sync word remained `0x42`; no firmware upgrade or reset occurred.

| Observed incremental cost | Receiver | Sender |
| --- | ---: | ---: |
| Native transmit attempts | 16 startup ACKs | 0 |
| Native reserved estimated airtime | 195,840 µs | 0 µs |
| Host journal reserved estimated airtime | 231,440 µs | 0 µs |
| Native receive-drop counter increase | 0 | 16 |
| Exported receive packets | 0 | 0 |

The sender was ready before the receiver and its loop ran. None of its input
reached the decryptor. The receiver ended with
`reliable_flow_startup_grant_unanswered`. The coordinator then stopped the
sender; its missing local report was recovered from the original remote files.
This was not a sender setup failure.

The sender's only changed native receive-reason category was
`IRQ_RESTART_HEADER_ONLY`, by 16. On this firmware that category can include
selective IRQ clearing while continuous receive remains active. It does not
prove 16 actual RX restarts, identify a packet-by-packet cause, or establish why
this trial failed. No receive packet was exported in either direction.

After the original scope expired on both controller clocks, separate bounded
read-only observations found unchanged native boot identities and counters,
empty closed queues, no active transmission or containment work, closed device
channels and unchanged tracked services. The observations created no RF
authority and did not retry or reset the failed run.

[RESULTS.json](RESULTS.json) retains the failed outcome and separates the original
incomplete, null journal totals from the recovered diagnostic totals above.
The full original allocation remains reserved in campaign history; no refund
was issued. All previous history is retained. A failed startup is not a zero-rate successful transfer.

The later [terminal repair-train source change](../../docs/TERMINAL_REPAIR_TRAIN.md)
is separate from this physical trial and has no hardware result here.
