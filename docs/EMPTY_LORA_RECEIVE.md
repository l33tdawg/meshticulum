# Rejecting an empty LoRa receive

The SX1262 driver rejects an empty PHY payload without delivering a packet or
claiming an authenticated wire receive. It increments the existing `rx_errors`
counter, skips FIFO and packet-status reads, and completes the existing RTC and
IRQ cleanup. The worker then uses its normal fresh authority checks to rearm
within the original finite lease. No transmit command or attempt refund occurs.

Semtech's SX1261/2 datasheet rev 2.2 (December 2024), section 13.4.6.2,
table 13-68 on page 97 permits LoRa PayloadLength values 0x00 through0xFF.
Section 13.5.2 describes GetRxBufferStatus as returning the last payload length
and FIFO offset. The official Semtech driver returns that unsigned byte after
successful HAL transfer without a nonzero requirement:

- [Semtech product and official document links](https://www.semtech.com/products/wireless-rf/lora-connect/sx1262)
- [Semtech-authored rev 2.2 PDF mirror, table 13-68](https://w2.electrodragon.com/Chip-dat/SemTech-dat/SX1262-dat/DS_SX1261_2%20V2-2.pdf#page=97)
- [Official sx126x_driver v2.3.2, sx126x_get_rx_buffer_status](https://github.com/Lora-net/sx126x_driver/blob/v2.3.2/src/sx126x.c#L794-L810)

Previously the nonzero check converted this untrusted input into fatal hardware
containment. All BUSY, SPI reply type/size, status, register readback, and IRQ
cleanup checks remain unchanged and fatal on failure. The inherited GFSK receive paths use the same empty-input rejection; their
nonempty packet-status filtering is unchanged. The length is already an
unsigned byte from an exact-sized validated SPI reply; zero handling introduces
no wider accepted payload range or wire admission.

The observed campaign exception proves the driver read zero length after its
status checks and RX_DONE gate. It does not distinguish an actual empty PHY
frame from a transient radio or SPI anomaly. Later read-only snapshots were
obtained after containment held RESET low and the subsequent GPIO acquisition
released it; they cannot establish the original failing SPI response's integrity.
This change fixes the fatal nonzero assumption, not a demonstrated RF cause or
throughput issue. Tests use modeled SPI and do not claim physical qualification.
