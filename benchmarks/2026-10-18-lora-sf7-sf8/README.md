# Matched SF7/BW500 and SF8/BW500 bench comparison

On 18 October 2026, two complete campaigns produced six matched pairs on a
direct one-metre SX1262 path. Each pair used the same fresh 8,164-byte payload
at SF7/BW500 and SF8/BW500. All 12 receivers verified the exact resource and
all 12 senders accepted the authenticated peer receipt.

| Run | SF7 sender ARM → receipt, median | SF7 useful rate | SF8 sender ARM → receipt, median | SF8 useful rate | SF8 / SF7 |
| --- | ---: | ---: | ---: | ---: | ---: |
| **Combined, six pairs** | **7.404932 s** | **1.102508 kB/s (8.820067 kbit/s)** | **11.340178 s** | **0.719918 kB/s (5.759345 kbit/s)** | **1.531×** |
| Final campaign, three pairs | 7.285935 s | 1.120515 kB/s (8.964121 kbit/s) | 11.316693 s | 0.721412 kB/s (5.771297 kbit/s) | 1.553× |
| Control campaign, three pairs | 7.648962 s | 1.067334 kB/s (8.538675 kbit/s) | 11.363662 s | 0.718430 kB/s (5.747443 kbit/s) | 1.486× |

At the receiver ARM-to-verified boundary, the combined medians were
**8.600729 s at SF7 (949.222 B/s)** and **12.157059 s at SF8 (671.544 B/s)**.
Including sender process startup through authenticated receipt, the combined
medians were **7.675360 s** and **11.602588 s**. The control campaign was within
4.7% of the final campaign's SF7 rate and within 0.4% of its SF8 rate; the
combined result is the headline because it includes that observed run variation.

## Fixed conditions

- Source: `f8952155146f821be1eefebd10664141484a22b9`
- SDK source archive SHA-256: `12fd962ffcaa4307a5902c3788ead3cad8142d6a6d7979a32095ec9d0bb76d21`
- Sender: Orange Pi 5 Plus SX1262 HAT
- Receiver: Heltec V4 SX1262 MCU
- Frequency: 921.375 MHz
- Bandwidth: 500 kHz
- Coding rate: 4/5
- Chip power: 0 dBm
- Preamble: 8 symbols
- Sync word: `0x42`
- Approximate separation: 1 metre, owner estimate
- Original grant window: 180 seconds
- Payload: three fresh 8,164-byte resources per campaign, each used at both
  profiles, for six matched pairs and 12 one-chunk transfers
- Proof: exact manifest and chunk verification at the receiver; authenticated
  receipt confirmation at both endpoints

## Integrity and 1,024-attempt headroom

The final campaign used 85 receiver attempts and 264 sender attempts. The
control used 87 and 264. Across both campaigns the receiver journals total
**172 attempts** and the sender journals **528**, with no fenced or
recovered-unknown attempts.

The pre-campaign MCU status was boot generation 39 with **208 attempts** and
**9,156,480 microseconds** of reserved estimated airtime. The read-only
post-campaign status is the same boot, **380 attempts** and
**16,719,744 microseconds**, disarmed with zero receive drops. The 172-attempt
advance matches the combined receiver journals.

The public LoRa admission caps cumulative boot attempts at **1,024**. The
baseline left 816 attempts; the control used 87, leaving 729 for the final
campaign; the final used 85, leaving **644 attempts of headroom**. The
reserved-airtime counter advanced by **7,563,264 microseconds**, leaving
**43,280,256 microseconds** under the campaign's 60,000,000-microsecond profile
cap. The receiver journals' reservation sum is 8,491,660 microseconds; the
firmware counter and journal reservations use different accounting bases, and
neither is an RF-energy measurement.

An earlier setup in the campaign rebooted the MCU from boot generation 38 to 39
with no logged USB reset; the cause remains unknown. Both complete campaigns are
continuous within boot generation 39.

## Scope

This is a finite near-bench comparison, not a completion distribution or a
field-range measurement. It does not measure RF airtime at the antenna, energy,
EIRP or a different installation. The one-chunk example verifies the exact
resource bytes but does not invoke whole-file assembly. SF7 and SF8 are both
public bench profiles; these samples support only the conditions and source
recorded here.
