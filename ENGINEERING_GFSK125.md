# GFSK125 implementation stage

This isolated candidate adds a 47-byte typed GFSK record, conservative packet airtime, SX1262 command vectors and a firmware configuration helper. LoRa's RadioProfile, 30-byte ARM body, firmware loop and frozen flow remain unchanged. The current workers reject GfskProfile. This is the first implementation stage; the new mode still needs its own admitted worker and firmware command before a physical test.

The starting tuple is125,000 bit/s, 31,250 Hz deviation, 312,000 Hz receiver bandwidth and Gaussian BT 0.5. Packets use 32 preamble bits,64 sync bits (`MTGFSK01`), one length byte, 1..255 ciphertext bytes, two non-inverted CRC bytes (initial FFFF, polynomial 1021), no address byte and whitening seed 01FF. These settings fit the documented SX1262 GFSK bandwidth table;500 kbps/1 MHz is excluded from this candidate. Register definitions and bit/byte units follow [Semtech's rev2.2 datasheet, sections6.2.2.1 and13.4](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/datasheet/SX1261_2%20V2-2.pdf).

SetModulationParams is `00 20 00 09 19 00 80 00`: bitrate divider 8193, Gaussian BT 0.5 code 09,312kHz bandwidth code 19 and deviation divider 32768. SetPacketParams for 255 bytes is `00 20 07 40 00 01 ff 02 01`. The host and C++ record parsers agree on the exact bounds and sizes. Semtech requires the preamble detector to be shorter than the following sync. RadioLib caches this detector privately and rewrites it on RX/TX staging. This initial public-API variant uses a 32-bit detector and 64-bit sync, preserving that inequality through later staging. Semtech recommends 8/16-bit detection; a future cache-aware adapter may use16 with32 sync. A one-off raw register override is unsafe because staging overwrites it.

[RadioLib 7.7.1 beginFSK](https://github.com/jgromes/RadioLib/blob/7.7.1/src/modules/SX126x/SX126x.cpp) finishes with shaping disabled. The helper therefore sets BT 0.5 explicitly, then the eight-byte sync, variable-length mode, CRC and whitening. Board TCXO voltage/regulator selection comes from the caller's existing board configuration; HeltecV4's current pinned target specifies 1.8 V. It stops on every configuration failure. No RX/TX or ARM is issued by this helper.

The same pinned RadioLib airtime function accounts for preamble, sync, payload and CRC, but omits the variable-length byte. Meshticulum's candidate accounts for all 120 framing bits plus 8 bits per ciphertext byte. At 125 kbps each bit is 8 us; maximum physical airtime is17,280us. This conservative value must drive host and MCU reservation/evidence instead of the undercounted library result.

| Packet | Ciphertext bytes | Airtime, including preamble/sync/length/CRC |
|---|---:|---:|
| DATA ceiling |255|17,280us|
| ACK |70|5,440us|
| One DATA + one ACK |325|22,720us|

The runtime pins a 193-byte flow quantum, a 255-byte sealed DATA ceiling and70-byte sealed ACK. A half-duplex one-ACK-per-DATA model has an 8,494.72 B/s flow ceiling. Applying the8164/8193 MR1 application factor gives 8,465.68 useful B/s before request/setup/retry and host costs. At5,000 useful B/s this leaves 15,748.07us beyond the22,720us airtime cycle; at5KiB/s it leaves 14,846.47us.

These are calculated ceilings and scheduling budgets, not measured performance or range. ACK batching changes the ceiling; do not present the one-ACK model as a measurement of the frozen flow's exact schedule. Add PA ramp, oscillator/RX rearm, USB/SPI work and ACK timeout margin separately. Keeping the oscillator warm inside the original lease helps. Current 36-byte USB chunks with 2 ms gaps can consume about 16 ms for a 289-byte host TX frame by themselves; GFSK alone cannot remove that host bottleneck.

The next small integration patches are:

1. Add distinct GFSK ARM/ARM_REPLY and status records with a modem/version tag. Keep legacy LoRa opcode/body/status interpretation exact; never overload SF/BW fields to mean GFSK. Include GFSK ARM in the same monotonic mutation/replay guard and finite lease accounting.
2. Add a serial GFSK worker that binds47-byte profile, boot, request, ciphertext and journal. Before TX, reserve conservative airtime once; unknown writes stay fenced and paid. MCU uses the same formula and reports matching evidence. Configuring a new modem never refunds previous attempts.
3. Extend generic SX1262 SPI configuration with packet type 00 and the explicit GFSK modulation/packet/register settings. Preserve finite RX/TX timers, FIFO ownership and CRC rejection. GFSK packet-status RSSI layout differs from LoRa; do not fabricate a LoRa SNR measurement.
4. Keep encrypted wire packets and accepted flow classes unchanged. Existing255-byte ciphertext capacity remains. No automatic modem/rate fallback or per-packet PHY negotiation.
5. Lantern adds an exact opt-in GFSK admission/owner/profile and finite test only after core source controls pass. Each endpoint must independently admit the same mode. A separate reviewed firmware build/flash and physical qualification follow; no production, publication or alpha authority comes from this source stage.
