# Alpha.5: one verified 64 KiB LoRa file

On 13 November 2026, measured SDK source
`794ea7111aa51aa9c91867bc6f28b610f3ae79af` delivered **65,536 useful bytes
in 17.439448 seconds: 3,757.917 B/s**, from first sender process startup to
final authenticated receipt. The receiver independently verified the whole
file hash. Both endpoints closed, the original scope expired, and the sender
native receive-drop counter stayed at zero.

| Boundary | Elapsed | Useful goodput |
| --- | ---: | ---: |
| Sender startup → authenticated receipt (canonical) | 17.439448 s | 3,757.917 B/s |
| First host DATA queue submission → receipt | 16.716547 s | 3,920.427 B/s |

The second boundary excludes startup and is not RF-start timing. Receipt timing
precedes final retirement, endpoint closure and independent file verification.
Neither result reaches 4,000 B/s. At this file size, the canonical interval would
need to fall by another 1.055448 seconds to reach that target.

## Tested configuration

The path used a Heltec MCU serial sender and a Linux SX1262 HAT receiver with
firmware `mtb1-86f7307b7c`: LoRa SF5, 500 kHz bandwidth, CR4/5, 921.375 MHz,
32-symbol preamble, sync word 18 and 0 dBm requested chip power. Configured
power is not measured EIRP; distance and antenna performance were not newly
qualified. The sender reported CPU 160 MHz and APB 80 MHz; these are software
readbacks, not an oscillator calibration.

This run enabled the bounded 32-frame train with at most three host-pending
frames, authenticated DATA end markers, compiled imports, C-side 4 MHz SPI and
checked receive rearm. The native queue limit, 5 ms listening guard, 100 ms
reverse floor, original deadlines and durable reservations remained enforced.
Normal per-boot admission was used; continuation was disabled.

The firmware recipe enabled `--native-profile`, `--rx-header-preserve`,
`--esp32-bulk-spi`, `--skip-unchanged-erratum-writes`, `--burst-wake-hint`,
`--heltec-spi-hz 8000000` and `--heltec-cpu-mhz 160`. These are explicit
experimental build options, not changes to default build settings.

Six DATA retransmissions were needed. All 14 end markers were accepted;
receiver marker-to-ACK-call delay had a 6.421 ms median. The receiver recorded
nine header-error observations and no CRC errors. These counters do not
identify the RF cause of a missing packet. Complete bounded flow/physical
traces and the 2,052-call receive timeline had no evictions.

The sender charged **347 attempts / 14,021,410 estimated airtime µs**; the
receiver charged **17 / 245,905 µs**. Native sender accounting separately added
**347 / 12,431,280 µs**. Host and native estimates use different accounting
boundaries and must not be substituted for one another.

## Retained failure and limits

The preceding 64 KiB attempt did not complete and has no valid throughput
result. It charged 358 sender attempts; two terminal outcomes were missing
from the pre-close trace and remain retained. Host-budget exhaustion was inferred from the retained run evidence; a separate
source regression reproduced its conversion into an uncertain carrier
handover. That inference is not a directly logged cause of the earlier run. The corrected
SDK recognizes only a proven pre-insert budget refusal and drains already
admitted work within the original bound before containment if needed.

A separately authorized recovery preceded this successful run. The prior
native floor of 1,370 attempts / 49,077,920 estimated airtime µs remains in the
history. Resetting hardware did not refund it or authorize the next transfer;
that transfer received separate finite approval.

This is one finite bench measurement, with diagnostic overhead. It establishes
neither sustained throughput, field range, a completion distribution, nor a
causal gain from any single option. The rebuilt Alpha.5 release artifacts have
separate source-correspondence and installation checks; this is not a fresh
RF execution of that rebuilt wheel. Earlier Alpha.4 repeat records remain
unchanged. See [sanitized values and evidence digests](RESULTS.json) and
[release status](../../docs/RELEASE_READINESS.md).
