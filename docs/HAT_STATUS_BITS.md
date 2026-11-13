# SX126x probe status decoding

The non-transmitting HAT probe now interprets only GetStatus chip-mode bits 6:4 and command-status bits 3:1. Reserved bits 7 and 0 are not required to be zero. A returned 0xa2 therefore represents the same defined fields as 0x22. Expected RC/XOSC standby mode matching remains exact, and command-status 3/4/5 remains rejected.

This matches the existing SX1262._valid_status decoder. The [Semtech datasheet section 13.5.1](https://files.waveshare.com/wiki/SX1262-XXXM-LoRaWAN-GNSS-HAT/DS_SX1261-2_V1.2.pdf) defines these fields and marks the other two bits reserved. No permissive fallback or new command-status exception is introduced.

Source audit covered src/meshticulum/bench, examples and firmware/meshticulum-bench for reserved-zero masks 0x81/129, shifted mode/status fields and GetStatus decoders. Only hat_probe.Probe.status had the reserved-zero assumption. SX1262._valid_status already extracts defined fields; its narrowly contextual finite-RX timeout exception is unchanged. SX1262.standby checks its expected mode after normal status validation. The GFSK packet-status flag masks refer to a different register response and are unchanged. Serial protocol 0x81 is a message opcode, not a chip-status mask.

Tests run the complete probe sequence with all four reserved-bit combinations, including actual 0xa2/0xb2, and compare command order/restored registers to baseline. All 256 status bytes are checked for each standby mode: invalid mode and command failures remain refused. This is source/model evidence, not a physical qualification. The probe still performs its existing bounded reset/register roundtrip and issues no RF transmit command.
