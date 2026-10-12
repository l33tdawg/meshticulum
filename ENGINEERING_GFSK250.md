# Explicit GFSK250 candidate

This source candidate adds an opt-in 250 kbps SX1262 transport. It has not
been flashed or physically qualified. GFSK125 and LoRa keep their existing
profiles, serial messages and defaults.

`Gfsk250Profile` fixes 250000 bit/s, 62500 Hz deviation, 467000 Hz receiver
bandwidth, Gaussian BT 0.5, a 32-bit preamble and the existing 64-bit sync word.
The packet retains its length byte, whitening and two-byte CRC. Encrypted
frames remain limited to 255 bytes; the flow and encryption formats do not
change. The minimum nominal receive bandwidth is 375 kHz plus combined
frequency error, leaving 92 kHz of margin in the selected 467 kHz setting.
That margin does not establish range or regulatory permission.

The host profile is a distinct frozen class and JSON schema
`meshticulum.gfsk250-profile.v2`. Its 47-byte serial record starts with `GF`
and version 2. Version 1 is still GFSK125 and rejects the new record. Generic
grants require both `radio_modem="gfsk250"` and `Gfsk250Admission`; the grant,
route and profile digest bind this selection.

Serial commands are ARM 8, STATUS 9 and read-only CAPABILITIES 10; their
replies are 0x88, 0x89 and 0x8a. Raw receive is 0x92. `Gfsk250SerialWorker`
requires the caller's exact firmware identity and a matching current-boot
capability reply before ARM. `Gfsk250HATWorker` selects the separate SPI driver.
Legacy workers and admission classes reject the new profile. The firmware
requires the new ARM opcode and version together; no rate is inferred from
a legacy command.

Calculated physical airtime is 8640 us for 255 ciphertext bytes and 2720 us
for a 70-byte ACK. These are packet-bit calculations, not throughput results.
Host reservations retain their margin; every attempted write remains paid.
The original absolute authority deadline, clipped ARM lease, boot counters,
replay checks, unknown-write fencing and finite cleanup remain in force.

Firmware preparation includes the new header in its source manifest and
derived `mtb1-*` identity. A consuming application must separately review
its profile/policy admission, exact firmware pins, runtime/source lock and
new finite operation before native testing. This module authorizes none of
those actions and introduces no product-specific deployment code.

Register encodings follow [Semtech SX1261/2 rev2.2, section 6.2.1](https://m5stack-doc.oss-cn-shenzhen.aliyuncs.com/1198/DS_SX1261_2_V2-2.pdf)
and the pinned RadioLib 7.7.1 APIs. No 500 kbps or 1 MHz receiver support is
added by this candidate.
