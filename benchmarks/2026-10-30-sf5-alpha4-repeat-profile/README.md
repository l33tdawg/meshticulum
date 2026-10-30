# Alpha 4 SF5 repeat qualification

Three fresh B-to-A USB transfers used the exact `1.0.0a4` candidate wheel,
the repaired explicit train12 policy, and firmware `mtb1-e2eaa9f94c` on a
direct near-bench GFSK/SF5 path. Every sample delivered the same 23,900-byte
signed file, accepted the publisher and whole-file verification gates, and
reached the sender's authenticated final ACK before the separate physical
drain and endpoint-closure checks.

| Sample | Sender startup to authenticated receipt | Useful goodput | DATA attempts | Retransmissions |
| --- | ---: | ---: | ---: | ---: |
| run 13 | 7.496496 s | **3,188.156 B/s / 3.1882 kB/s** | 129 | 1 |
| run 14 | 7.487278 s | **3,192.081 B/s / 3.1921 kB/s** | 129 | 1 |
| run 15 | 7.589117 s | **3,149.246 B/s / 3.1492 kB/s** | 129 | 1 |

The minimum was **3,149.246 B/s**, the median was **3,188.156 B/s**, and the
maximum was **3,192.081 B/s**. All three clear the required 2.8 kB/s checkpoint
and the 3.0 kB/s stretch target.

Each transfer sent 128 DATA frames and received 12 authenticated ACK snapshots
from the receiver. The sender and receiver had zero duplicate flow events.
Each had one extra DATA attempt beyond the 128-frame file, so it did not repeat
the 13-retransmission stall seen in the retained run11 diagnostic. The run11
failure remains preserved and is not relabeled.

Both endpoint children in every sample exited zero with no forced cleanup.
The receiver reconstructed and verified the complete file at SHA-256
`95071c46c6486bfb0bb90c382c06f649424d804a1be5563ab4e15c697fd5453e`.
Authenticated receipt to drained endpoint completion took 6.794–7.557 ms.
Global device quiescence is not claimed.

After each run, an expired-scope read-only probe matched the result's final
native counters, retained exact native profiles and receive reasons, closed
serial ownership, and issued no RF, reset or grant command. The same-boot
per-sample native advances were:

| Sample | Receiver attempts / reserved airtime / drops | Sender attempts / reserved airtime / drops |
| --- | ---: | ---: |
| run 13 | +12 / +146,880 µs / 0 | +129 / +4,593,680 µs / 0 |
| run 14 | +12 / +146,880 µs / 0 | +129 / +4,593,680 µs / 0 |
| run 15 | +12 / +146,880 µs / 0 | +129 / +4,593,680 µs / 0 |

The separate attempt journals recorded 12 receiver and 129 sender attempts per
sample, each with zero fenced or recovered-unknown attempts. Reserved
airtime-journal totals are retained separately from the MCU's cumulative
estimate; they are not substituted for measured RF airtime.

## Scope and limitations

The timing boundary starts at the public sender child process and ends when the
final authenticated peer ACK is applied. It does not include final physical
drain, session retirement, reader verification, cleanup or the controller-side
join. The rates use 23,900 useful file bytes and decimal kB/s.

This is three finite repeated near-bench samples. It establishes repeatable
delivery above 3.0 kB/s for this exact workload, radio profile, firmware,
software candidate and paired A/B path. It does not establish sustained
throughput over larger files, a completion-time distribution, field range,
LoRa-profile behavior, energy or EIRP measurements, wider hardware
qualification, MeshCore/Reticulum superiority, private production or global
quiescence. Historical debt remains incomplete and unknown historical actual
costs remain null.

The machine-readable sample timings, native deltas, evidence hashes and privacy
filter are in [RESULTS.json](RESULTS.json). The exact runtime source is
`c8b82a527efca916415dc87eb4836991d55d7e80`; the measured wheel SHA-256 is
`f40ef1947bfc53208b711546e7a195e5932f6ac7d745a001b158c012f8f9c64c`.
