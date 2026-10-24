# MCU DATA queue: one LoRa comparison

On 24 October 2026, two Heltec V4 boards completed one fixed-order synchronous/queued comparison at an operator-reported approximately 20 m across two rooms. Both antennas were fitted; Unit C was excluded. Each B→A transfer verified one signed 8,164-byte part at SF7/BW500, CR4:5, 921.375 MHz and 0 dBm configured chip power.

| Mode | Sender startup → authenticated receipt | Useful goodput |
| --- | ---: | ---: |
| Synchronous | 8.766668 s | 931.255 B/s |
| MCU DATA queue | 7.526076 s | 1,084.762 B/s |

The queue improved useful goodput by **16.484%** and shortened this interval by **1.240592 seconds**. It was 22.025% above the earlier 888.965 B/s historical sample; that older sample is a separate source/firmware checkpoint. The fresh pair used the same modem and `mtb1-6cfde1fdd4` firmware.

The measured SDK came from `f06ebeb3e687da4688657311830e95326a0107d2`, wheel SHA-256 `7998a705ab6a5641ecb9191f08e9b63ba7a676612dcfa7455e291e6177149ad0`. Its package metadata still said `1.0.0a1`; it is distinct from the published 17 October alpha and from the later version-rebuilt `1.0.0a2` assets. Neither older installed-SDK proofs nor this measurement silently qualifies a rebuilt wheel.

All 46 queued DATA requests reached RF_DONE and explicit release. Peak retained depth was four; pending and worker-pending counts ended at zero. The sender then used one synchronous CONTROL packet; the receiver sent 18 ACKs. Both modes had zero retransmissions, new UNKNOWN outcomes or receive-drop delta, and both endpoint processes exited normally with host boots, MCU boots and service lifetimes unchanged. Keys, authenticated ACKs, credit and retry decisions remained on Linux.

Authenticated receipt preceded checked physical endpoint completion by **27.456 ms** for the queue, versus 3.329 ms for synchronous mode. The successful pinned completion gate checked empty/drained STATUS; its raw reply body and RX-rearm timestamps were not independently retained. Reported MCU timestamp gaps are consistent with the configured 5 ms inter-packet listening and 100 ms reverse-listening floors, without independently measuring the full RX-active interval.

The MCU driver elapsed sums stayed essentially unchanged: 4.983012 s synchronous and 4.989588 s queued. The queue overlaps host submissions with RF work. Queue-accept timing and driver timing overlap, so summing them would double-count time; host/USB/journal timing is not a CPU-only measurement.

An initial deployment attempt failed before RF and restored the old application images. A later synchronous baseline exhausted an under-sized airtime budget after 45 acknowledged DATA packets and stopped before its queued run. Both failures remain retained. The later host diagnostics and operation-journal cap fix preceded this successful fresh pair. Its original 480-attempt/20,000,000-host-estimated-us ceiling includes the failed baseline costs; no old fence was cleared or debt refunded.

The [sanitized record](results.json) retains source/artifact pins, timing arithmetic, counter deltas, journal charges and closure hashes. It omits private host/device identifiers, raw operator captures and provisioning material. This one pair establishes the reported measurement, with sustained capacity, field range, wider loss/recovery, private production, energy/EIRP and matched upstream superiority still open.
