# Matched SF7/BW500 and SF8/BW500 bench comparison

On 18 October 2026, six fresh one-chunk transfers completed on a direct
one-metre SX1262 path: three SF7/BW500 samples and three SF8/BW500 samples.
Each pair used the same 8,164-byte payload. Every receiver verified the exact
resource and every sender accepted the authenticated peer receipt.

| Profile | Sender ARM → authenticated receipt, median | Useful rate | Receiver ARM → verified, median |
| --- | ---: | ---: | ---: |
| SF7 / BW500 / CR4/5 | **7.820767 s** | **1.043887 kB/s (8.351096 kbit/s)** | 8.812348 s · 926.427 B/s |
| SF8 / BW500 / CR4/5 | **11.195342 s** | **0.729232 kB/s (5.833856 kbit/s)** | 12.172387 s · 670.698 B/s |

SF8 took **1.431×** as long as SF7 at the sender ARM-to-receipt boundary, so
the observed useful-rate ratio was **1.431×** in SF7's favour. Including sender
process startup, the medians were 8.090763 s and 11.466193 s; the remaining
samples and exact timings are in `results.json`.

## Fixed conditions

- Source: `2cfcbb34f6da84923ee5a3dd137cc8fafa548bbd`
- Public SDK archive SHA-256: `209c577b2dd503aa8812ecd584119f024782b07107ea006de36be6071cba5d66`
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
- Payload: three identical-within-pair 8,164-byte resources
- Proof: exact manifest and chunk verification at the receiver; authenticated
  receipt confirmation at both endpoints

## Integrity and debt

The final campaign used 89 receiver attempts and 264 sender attempts, with no
fenced or recovered-unknown attempts. All six authenticated receipt
confirmations completed, and no receive drop was recorded.

The receiver MCU remained on the same boot generation: attempts advanced from
**119 to 208**, and
reserved estimated airtime from **5,254,272 to 9,156,480 microseconds**. The
89-attempt delta matches the six receiver journals above.

An earlier campaign attempt found the existing admission ceiling was expressed
as 256 cumulative attempts even after the MCU had already paid more than that.
The resulting ARM was refused before the requested transfer. The fix adds a
separate cumulative LoRa admission with explicit bounded headroom; the legacy
`RadioAdmission` remains at its 256-attempt/60-second limit. The MCU rebooted
from boot generation 38 to 39 earlier in the campaign; no kernel USB reset was
logged, and the final three-pair result above is continuous within boot
generation 39. The cause of that earlier reboot remains unknown and is retained
as a limitation.

## Scope

This is a finite near-bench comparison, not a completion distribution or a
field-range measurement. It does not measure RF airtime at the antenna, energy,
EIRP or a different installation. The one-chunk example verifies the exact
resource bytes but does not invoke whole-file assembly. SF7 and SF8 are both
public bench profiles; these samples support only the conditions and source
recorded here.
