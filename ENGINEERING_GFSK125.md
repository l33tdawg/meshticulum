# GFSK125 transport candidate

This isolated candidate implements an explicit GFSK125 path through the generic authority, serial worker, direct-SPI HAT worker and SX1262 firmware. It adds a distinct 47-byte typed profile and conservative packet airtime. Legacy LoRa profiles, ARM/status framing and default worker selection retain their existing behavior; the accepted flow files are unchanged. All evidence here comes from source tests and modeled transports. A native Lantern pilot and physical qualification remain necessary.

The starting tuple is 125,000 bit/s, 31,250 Hz deviation, 312,000 Hz receiver bandwidth and Gaussian BT 0.5. Packets use 32 preamble bits, 64 sync bits (`MTGFSK01`), one length byte, 1..255 ciphertext bytes, two non-inverted CRC bytes (initial FFFF, polynomial 1021), no address byte and whitening seed 01FF. These settings fit the documented SX1262 GFSK bandwidth table; 500 kbps/1 MHz is excluded from this candidate. Register definitions and bit/byte units follow [Semtech's rev2.2 datasheet, sections 6.2.2.1 and 13.4](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/datasheet/SX1261_2%20V2-2.pdf).

SetModulationParams is `00 20 00 09 19 00 80 00`: bitrate divider 8193, Gaussian BT 0.5 code 09,312kHz bandwidth code 19 and deviation divider 32768. SetPacketParams for 255 bytes is `00 20 07 40 00 01 ff 02 01`. The host and C++ record parsers agree on the exact bounds and sizes. Semtech requires the preamble detector to be shorter than the following sync. RadioLib caches this detector privately and rewrites it on RX/TX staging. This initial public-API variant uses a 32-bit detector and 64-bit sync, preserving that inequality through later staging. Semtech recommends 8/16-bit detection; a future cache-aware adapter may use16 with32 sync. A one-off raw register override is unsafe because staging overwrites it.

[RadioLib 7.7.1 beginFSK](https://github.com/jgromes/RadioLib/blob/7.7.1/src/modules/SX126x/SX126x.cpp) finishes with shaping disabled. The helper therefore sets BT 0.5 explicitly, then the eight-byte sync, variable-length mode, CRC and whitening. Board TCXO voltage/regulator selection comes from the caller's existing board configuration; HeltecV4's current pinned target specifies 1.8 V. It stops on every configuration failure. No RX/TX or ARM is issued by this helper.

The same pinned RadioLib airtime function accounts for preamble, sync, payload and CRC, but omits the variable-length byte. Meshticulum's candidate accounts for all 120 framing bits plus 8 bits per ciphertext byte. At 125 kbps each bit is 8 us; maximum physical airtime is17,280us. This conservative value must drive host and MCU reservation/evidence instead of the undercounted library result.

| Packet | Ciphertext bytes | Airtime, including preamble/sync/length/CRC |
|---|---:|---:|
| DATA ceiling |255|17,280us|
| ACK |70|5,440us|
| One DATA + one ACK |325|22,720us|

The runtime pins a 193-byte flow quantum, a 255-byte sealed DATA ceiling and 70-byte sealed ACK. A half-duplex one-ACK-per-DATA model has an 8,494.72 B/s flow ceiling. Applying the 8164/8192 MR1 application factor gives 8,465.68 useful B/s before request/setup/retry and host costs. At 5,000 useful B/s this leaves 15,748.07 us beyond the 22,720 us airtime cycle; at 5 KiB/s it leaves 14,846.47 us.

These are calculated ceilings and scheduling budgets, not measured performance or range. ACK batching changes the ceiling; do not present the one-ACK model as a measurement of the frozen flow's exact schedule. Add PA ramp, oscillator/RX rearm, USB/SPI work and ACK timeout margin separately. Keeping the oscillator warm inside the original lease helps. Current 36-byte USB chunks with 2 ms gaps can consume about 16 ms for a 289-byte host TX frame by themselves; GFSK alone cannot remove that host bottleneck.

The implemented interfaces are:

| Interface | Explicit GFSK behavior |
|---|---|
| Profile | `GfskProfile.to_dict()` uses schema `meshticulum.gfsk-profile.v1` and `sync_word_hex`; `from_dict(..., board_id=...)` validates the board context. |
| Generic admission | Caller supplies `GfskAdmission` with country, frequency/power lists and finite caps. Core supplies no native device or regional policy. |
| Authority plan | Requires `radio_modem: gfsk125` and the GFSK admission; profile and modem marker participate in the existing route/authority bindings. |
| Serial worker | Explicit `GfskSerialWorker`; GFSK ARM 6/reply 0x86 carry 47 bytes. GFSK STATUS 7/reply 0x87 carry 66 bytes. GFSK RECEIVE 0x91 carries RSSI and ciphertext, with no SNR. |
| HAT worker | Explicit `GfskHATWorker` and `GfskSX1262`; packet type 00, the pinned modulation/packet fields and original finite RX/TX deadlines. |
| Accounting | Host and MCU use the same conservative formula. Worker/driver dispatch is pinned before and around authorization. Unknown writes stay fenced and paid. A modem change or disarm never refunds boot debt. |

Firmware starts in LoRa mode. An admitted GFSK ARM switches it to GFSK, and DISARM retains that mode for later read-only status queries. The wrong modem's status query receives `BAD_COMMAND`; Lantern may try legacy STATUS first and GFSK STATUS only after that exact refusal. This identifies the current status layout without changing the PHY. Every other error remains fatal. A later admitted LoRa ARM reinitializes the LoRa configuration while preserving boot attempts and reserved airtime.

Source controls cover typed profile refusal, caller admission, real generic authority/AEAD/BenchFlow composition, durable reservations, unknown outcomes, malicious dispatch changes, direct-SPI command/RX/TX models and the actual firmware loop's mode transitions, replay and expiry. A separate check executes the pinned RadioLib configuration and RX/TX cache paths through a fake SPI HAL. These checks establish source behavior, not over-the-air delivery or throughput.

Lantern must add its opt-in native profile/admission, owner contract, profile decoding/digests, mode-aware serial status check and runtime closure. Both endpoints must independently admit the same mode. Its firmware build inputs must include `gfsk_profile.hpp` alongside `main.cpp` and `protocol.hpp`, with a new source identity. Native provisioning, installation, device ownership, flash and finite pilot issuance remain in Lantern. Encrypted packet contents, resource framing and frozen flow classes remain unchanged.
