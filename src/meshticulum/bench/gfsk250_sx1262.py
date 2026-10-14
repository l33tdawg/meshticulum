"""Opt-in GFSK250 SPI driver; never grants access to a radio or RF window."""
from .gfsk250_profile import Gfsk250Profile, estimated_airtime_us, sx1262_plan
from .sx1262 import SX1262
from .hat_probe import Probe
from .linux_hat import require
from .serial_protocol import Gfsk250RawReceive


class Gfsk250SX1262(SX1262):
    _airtime = staticmethod(estimated_airtime_us)

    def _packet(self, size):
        self._write(0x8C, sx1262_plan(self.profile, size).packet)

    def configure(self, profile):
        require(type(profile) is Gfsk250Profile and self.profile is None and not self.fenced,
                'gfsk250_invalid_configuration_state')
        profile.encode(board_id=2)
        require(profile.chip_power_dbm <= 10, 'gfsk_bench_power_ceiling')
        try:
            Probe(self.io).run(); self.profile=profile; self.standby()
            self._write(0x8A,b'\0'); require(self._read(0x11,1)==b'\0','gfsk_wrong_packet_type')
            self._write(0x96,b'\0'); self._write(0x07,b'\0\0'); self._write(0x89,b'\x7f')
            self._write(0x98,b'\xe1\xe9'); self._device_errors()
            self._write(0x86,((profile.frequency_hz<<25)//32000000).to_bytes(4,'big'))
            self._write(0x9D,b'\x01'); self._write(0x95,b'\x04\x07\0\x01')
            self._write(0x8E,bytes([profile.chip_power_dbm,0x04])); self._bits(0x08D8,0,0x1E)
            self._write_register(0x08E7,b'\x38')
            plan=sx1262_plan(profile); self._write(0x8B,plan.modulation)
            self._bits(0x06D1,0x18,0x08); self._bits(0x089B,0x1C,0)
            self._bits(0x08B8,0x10,0x10); self._bits(0x06AC,0x70,0); self._bits(0x0889,0,4)
            self._write_register(0x06C0,plan.sync_word); self._write_register(0x06BC,plan.crc_initial)
            self._write_register(0x06BE,plan.crc_polynomial)
            self._bits(0x06B8,1,1); self._write_register(0x06B9,b'\xff'); self._packet(255)
            require(self._register(0x06C0,8)==plan.sync_word and self._register(0x06BC,2)==plan.crc_initial
                    and self._register(0x06BE,2)==plan.crc_polynomial,'gfsk_packet_register_readback')
            self._write(0x8F,b'\0\0'); self._write(0x93,b'\x20')
            self.clear_irq(); self._device_errors(); self.standby()
        except BaseException:
            self.contain(); raise

    def _rx_tail_us(self):
        return self._airtime(self.profile,255)

    def _receive_evidence(self,data,status):
        rx_status,rssi_sync,_rssi_average=status
        if rx_status & 0xFC or not rx_status & 0x02:
            self.rx_errors+=1; return None
        return Gfsk250RawReceive(-rssi_sync/2,data)
