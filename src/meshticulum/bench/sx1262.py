"""Bounded direct-SPI driver for C's confirmed SX1262 HF crystal HAT.

Command definitions and errata follow Semtech SX1261/2 DS rev1.2 sections
13–15 and the Semtech sx126x_driver reference. The manufacturer's HAT uses
DIO2 for one RF-switch control and host physical31 for the other: host HIGH
for RX, LOW for TX. No TCXO command is issued to this crystal board.

This layer grants no service authority. The exclusive host worker must reserve
a durable attempt and hold current authority before calling start_transmit.
Hardware TX and RX timers are always finite, including if the host disappears.
"""
import math
import struct
from types import MethodType

from .hat_probe import Probe
from .host_timing import Phase, phase, timed
from .linux_hat import HardwareError, LinuxHAT, require
from .serial_protocol import Outcome, RadioProfile, RawReceive, TxEvidence
from .serial_worker import estimated_airtime_us

TX_DONE, RX_DONE, HEADER_ERROR, CRC_ERROR, TIMEOUT = 1, 2, 0x20, 0x40, 0x200
RX_MASK = RX_DONE | HEADER_ERROR | CRC_ERROR | TIMEOUT
BW_CODES = {62500: 3, 125000: 4, 250000: 5, 500000: 6}
# Datasheet read commands used by this driver. Unknown commands retain the
# write settle delay; a read never starts the command-processing state machine.
_READ_OPCODES = frozenset((0xC0, 0x1D, 0x1E, 0x11, 0x12, 0x13, 0x14, 0x17))


class SX1262:
    _airtime = staticmethod(estimated_airtime_us)

    def __setattr__(self, name, value):
        if getattr(self, '_checked_rearm', False) and name in (
                '_checked_rearm', '_activity_reader', '_drained_handoff', '_rearm_transfer_epoch'):
            raise HardwareError('sx1262_rearm_binding_changed')
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if getattr(self, '_checked_rearm', False) and name in (
                '_checked_rearm', '_activity_reader', '_drained_handoff', '_rearm_transfer_epoch'):
            raise HardwareError('sx1262_rearm_binding_changed')
        object.__delattr__(self, name)

    def __init__(self, transport, *, monotonic=None, checked_rearm=False):
        require(type(checked_rearm) is bool, 'invalid_hat_checked_rearm')
        self.io = transport
        object.__setattr__(self, '_drained_handoff', None)
        self._activity_reader = None
        self._rearm_transfer_epoch = None
        if checked_rearm:
            require(type(transport) is LinuxHAT, 'hat_checked_rearm_transport_unverified')
            activity = transport.enable_radio_activity()
            names = ('transfer','_set','reset','receive_switch','close',
                     '_record_radio_activity','radio_activity_epoch')
            bindings = tuple((getattr(transport,n).__self__,getattr(transport,n).__func__) for n in names)
            def activity_reader():
                require(self.io is transport and transport._radio_activity is activity
                        and all(type(method := getattr(transport,n)) is MethodType and method.__self__ is owner
                                and method.__func__ is fn for n,(owner,fn) in zip(names,bindings)), 'hat_radio_activity_binding_changed')
                return activity()
            self._activity_reader = activity_reader
        self._checked_rearm = checked_rearm
        original_airtime=self._airtime
        hook_names=('_rx_tail_us','_receive_evidence','_packet')
        if checked_rearm:
            hook_names += ('_transfer','_write','_ready','standby','clear_irq','poll_receive','start_receive','_handoff_state')
        hooks=tuple((getattr(self,name).__self__,getattr(self,name).__func__) for name in hook_names)
        def same_hooks():
            if checked_rearm:
                return all(type(method := getattr(self,name)) is MethodType and method.__self__ is owner
                           and method.__func__ is function for name,(owner,function) in zip(hook_names,hooks))
            return all(getattr(getattr(self,name),'__self__',None) is owner
                       and getattr(getattr(self,name),'__func__',None) is function
                       for name,(owner,function) in zip(hook_names,hooks))
        clock = transport.monotonic if monotonic is None else monotonic
        require(callable(clock), 'invalid_sx1262_clock')
        # HATWorker supplies its captured host clock. Direct driver model users
        # retain their explicit transport time basis; this layer grants no authority.
        def now():
            require(self.io is transport and self._airtime is original_airtime and same_hooks(), 'sx1262_transport_changed')
            value = clock()
            require(type(value) in (int, float) and math.isfinite(value), 'invalid_sx1262_clock')
            require(self.io is transport and self._airtime is original_airtime and same_hooks(), 'sx1262_transport_changed')
            return value
        self._now = now
        self.profile = None
        self.fenced = False
        self.receiving = self.transmitting = False
        self.receive_pending = False
        self.attempts = self.reserved_us = 0
        self.rx_errors = 0
        self.receive_reasons = None
        self.receive_timeline = None
        self.tx_estimate = self.tx_started = self.tx_deadline = 0

    def _ready(self):
        end = self._now() + 0.150
        while self.io.busy():
            require(self._now() < end, 'sx1262_busy_timeout')
            self.io.sleep(0.0002)

    def _transfer(self, packet):
        object.__setattr__(self, '_drained_handoff', None)
        expected = self._rearm_transfer_epoch
        object.__setattr__(self, '_rearm_transfer_epoch', None)
        require(not self.fenced, 'sx1262_fenced')
        before = self._handoff_state() if self._checked_rearm else None
        self._ready()
        current = self._handoff_state() if self._checked_rearm else None
        if before is not None:
            require(current == before, 'sx1262_callback_radio_activity')
        if expected is not None:
            require(current == expected, 'sx1262_rearm_intervening_activity')
        result = self.io.transfer(packet)
        require(type(result) is bytes and len(result) == len(packet), 'sx1262_short_reply')
        # DS2.2 section 8.3.1: writes assert BUSY within 600 ns after NSS
        # rises; reads do not assert it. Keep a 2 us minimum settle and both BUSY
        # gates, including the post-read gate for an asynchronous internal IRQ.
        if packet[0] not in _READ_OPCODES:
            self.io.sleep(0.000002)
        self._ready()
        if before is not None:
            after = self._handoff_state()
            require(after[-1] == before[-1] + 1 and after[:-1] == before[:-1],
                    'sx1262_callback_radio_activity')
        return result

    @staticmethod
    def _valid_status(value):
        # DS13.5.1 reserves bits 7 and 0; their values are not specified.
        # C's chip returns 0xA2 on reads and 0x22 from GetStatus in STBY_RC.
        # Semtech's reference driver likewise extracts only mode and command.
        require((value >> 4) & 7 in (2, 3, 4, 5, 6)
                and (value >> 1) & 7 not in (3, 4, 5), 'sx1262_bad_status_' + hex(value))

    def status(self):
        value = self._transfer(b'\xc0\0')[1]
        self._valid_status(value)
        return value

    def _write(self, opcode, body=b''):
        self._transfer(bytes([opcode]) + body)
        return self.status()  # completed command, not an unchecked write MISO byte

    def _read(self, opcode, size, prefix=b''):
        value = self._transfer(bytes([opcode]) + prefix + bytes(size + 1))
        status, data = value[len(prefix) + 1], value[len(prefix) + 2:]
        # C's chip returns command-status 3 after its finite RX timer expires:
        # captured GetIrqStatus=0xA6, IRQ=0x0200, RX -> STBY_RC. Accept only
        # that exact receive-timeout context. Configuration, TX, missing or
        # conflicting IRQs and other command failures retain strict checks.
        rx_timeout = (opcode == 0x12 and not prefix and size == 2
                      and self.receiving and not self.transmitting
                      and (status >> 4) & 7 == 2 and (status >> 1) & 7 == 3
                      and data == TIMEOUT.to_bytes(2, 'big'))
        if not rx_timeout:
            self._valid_status(status)
        return data

    def _register(self, address, size=1):
        return self._read(0x1D, size, address.to_bytes(2, 'big'))

    def _write_register(self, address, value):
        self._write(0x0D, address.to_bytes(2, 'big') + value)

    def _bits(self, address, clear, set_bits):
        value = (self._register(address)[0] & ~clear) | set_bits
        self._write_register(address, bytes([value]))
        require(self._register(address) == bytes([value]), 'sx1262_register_readback')

    def irq(self):
        return int.from_bytes(self._read(0x12, 2), 'big')

    def clear_irq(self):
        self._write(0x02, b'\xff\xff')
        require(self.irq() == 0, 'sx1262_stale_irq')

    def standby(self):
        value = self._write(0x80, b'\0')
        require((value >> 4) & 7 == 2, 'sx1262_standby_failed')
        self.io.receive_switch(True)
        self.receiving = False

    def _packet(self, size):
        self._write(0x8C, struct.pack('!HBBBB', self.profile.preamble, 0, size, 1, 0))

    def _device_errors(self):
        require(self._read(0x17, 2) == b'\0\0', 'sx1262_device_error')

    def configure(self, profile):
        object.__setattr__(self, '_drained_handoff', None)
        require(type(profile) is RadioProfile and self.profile is None and not self.fenced,
                'sx1262_invalid_configuration_state')
        profile.encode(board_id=2)
        # Initial bench ceiling; the HAT's labelled absolute maximum is 22 dBm.
        require(profile.chip_power_dbm <= 10, 'sx1262_bench_power_ceiling')
        try:
            Probe(self.io).run()  # independently checked no-TX reset/mode/register probe
            self.profile = profile
            self.standby()
            self._write(0x8A, b'\x01')  # LoRa packet type first in configuration
            require(self._read(0x11, 1) == b'\x01', 'sx1262_wrong_packet_type')
            self._write(0x96, b'\0')  # LDO; this path does not assume a DC-DC layout
            self._write(0x07, b'\0\0')
            self._write(0x89, b'\x7f')  # all calibration blocks
            self._write(0x98, b'\xe1\xe9')  # Semtech 902–928 MHz calibration pair
            self._device_errors()
            self._write(0x86, ((profile.frequency_hz << 25) // 32000000).to_bytes(4, 'big'))
            self._write(0x9D, b'\x01')  # SX1262 DIO2 automatic RF-switch control
            self._write(0x95, b'\x04\x07\0\x01')  # SX1262 PA configuration
            self._write(0x8E, bytes([profile.chip_power_dbm, 0x04]))  # 200 us ramp
            self._bits(0x08D8, 0, 0x1E)  # PA clamp erratum
            self._write_register(0x08E7, b'\x38')  # SX1262 140 mA OCP
            symbol_us = (1 << profile.spreading_factor) * 1000000 / profile.bandwidth_hz
            self._write(0x8B, bytes([profile.spreading_factor, BW_CODES[profile.bandwidth_hz],
                                     profile.coding_rate - 4, int(symbol_us >= 16000)]))
            self._bits(0x0889, 4, 0 if profile.bandwidth_hz == 500000 else 4)
            self._packet(255)
            self._bits(0x0736, 0, 4)  # standard IQ: bit 2 SET, DS15.4
            word = bytes([(profile.sync_word & 0xF0) | 4, ((profile.sync_word & 15) << 4) | 4])
            self._write_register(0x0740, word)
            require(self._register(0x0740, 2) == word, 'sx1262_sync_readback')
            self._write(0x8F, b'\0\0')  # single half-duplex buffer; RX and TX base 0
            self._write(0x93, b'\x20')  # explicit fallback to RC standby
            self.clear_irq()
            self._device_errors()
            self.standby()
        except BaseException:
            self.contain()
            raise

    @staticmethod
    def _rtc_steps(duration_us):
        ticks = math.ceil(duration_us * 64 / 1000)
        require(1 <= ticks < 0xFFFFFF, 'sx1262_timer_not_finite')
        return ticks.to_bytes(3, 'big')

    def _room(self, deadline, seconds):
        require(type(deadline) in (int, float) and math.isfinite(deadline)
                and self._now() + seconds < deadline, 'sx1262_authority_deadline')

    def start_transmit(self, packet, *, authority_deadline, antenna_confirmed):
        object.__setattr__(self, '_drained_handoff', None)
        """Start TX, or return False while a valid completed RX awaits draining.

        False proves no TX command/FIFO write was issued by this call. A late
        RX completion after standby may already have consumed the local paid
        attempt; that charge is deliberately retained.
        """
        require(antenna_confirmed is True, 'sx1262_antenna_not_confirmed')
        require(self.profile is not None and not self.transmitting and not self.fenced,
                'sx1262_not_ready')
        require(type(packet) is bytes and 1 <= len(packet) <= 255, 'sx1262_invalid_packet')
        estimate = self._airtime(self.profile, len(packet))
        timeout_us = math.ceil(estimate * 1.10) + 100000
        self._room(authority_deadline, timeout_us / 1000000 + 0.05)
        require(self.attempts < self.profile.max_boot_attempts
                and self.reserved_us + estimate <= self.profile.max_boot_estimated_airtime_us,
                'sx1262_attempt_budget')
        was_receiving = self.receiving or self.receive_pending
        if was_receiving:
            flags = self.irq()
            if flags & RX_DONE and not flags & (CRC_ERROR | HEADER_ERROR | TIMEOUT):
                try:
                    # Freeze the shared FIFO across host permission checks and
                    # durable NOT_SENT settlement. Even a receive backend in
                    # continuous mode cannot overwrite this completed packet.
                    self.standby()
                    flags = self.irq()
                    require(flags & RX_DONE and not flags & (CRC_ERROR | HEADER_ERROR | TIMEOUT),
                            'sx1262_pending_receive_changed')
                    self.receive_pending = True
                    return False
                except BaseException:
                    self.contain()
                    raise
        self.attempts += 1
        self.reserved_us += estimate
        self.tx_estimate, self.tx_started = estimate, self._now()
        self.transmitting = True
        try:
            self.standby()
            # A packet can finish between the first IRQ sample and standby.
            # Once standby completes, its FIFO is stable. Preserve it before
            # ClearIrqStatus/WriteBuffer, for the worker's next receive pass.
            if was_receiving:
                flags = self.irq()
                if flags & RX_DONE and not flags & (CRC_ERROR | HEADER_ERROR | TIMEOUT):
                    self.transmitting = False
                    self.receive_pending = True
                    return False
            self.clear_irq()
            self._packet(len(packet))
            self._write(0x0E, b'\0' + packet)
            self._write(0x08, struct.pack('!HHHH', TX_DONE | TIMEOUT, TX_DONE | TIMEOUT, 0, 0))
            self.io.receive_switch(False)
            self._room(authority_deadline, timeout_us / 1000000 + 0.05)
            self.tx_deadline = self._now() + timeout_us / 1000000 + 0.05
            self._write(0x83, self._rtc_steps(timeout_us))
            return True
        except BaseException:
            self.contain()
            raise

    def poll_transmit(self):
        require(self.transmitting and not self.fenced, 'sx1262_no_live_transmission')
        flags = 0
        try:
            flags = self.irq()
            require(not flags & ~(TX_DONE | TIMEOUT), 'sx1262_unexpected_tx_irq')
            if flags & TX_DONE and not flags & TIMEOUT:
                elapsed = max(0, math.ceil((self._now() - self.tx_started) * 1000000))
                self.standby()
                self._device_errors()
                self.transmitting = False
                return TxEvidence(Outcome.RF_DONE, self.tx_estimate, elapsed, flags, 0)
            if flags & TIMEOUT or self._now() >= self.tx_deadline:
                raise HardwareError('sx1262_tx_timeout')
            return None
        except (OSError, HardwareError):
            elapsed = max(0, math.ceil((self._now() - self.tx_started) * 1000000))
            self.contain()
            return TxEvidence(Outcome.UNKNOWN, self.tx_estimate, elapsed, flags, -1)

    def _handoff_state(self):
        # Numeric handle/configuration identity supplements the transport epoch.
        # OS-level close/reuse outside this API remains the owner's responsibility.
        transport_state = tuple((type(value), value) for value in (
            self.io.spi_fd, self.io.lines_fd, self.io.chip_fd, self.io.lock_fd,
            self.io._spi_hz))
        return (id(self.profile), self.profile, self.fenced, self.receiving, self.transmitting,
                self.receive_pending, transport_state, self._activity_reader())

    @timed(Phase.HAT_RX_REARM)
    def start_receive(self, *, authority_deadline):
        token = self._drained_handoff
        object.__setattr__(self, '_drained_handoff', None)  # Consume even on refusal.
        require(self.profile is not None and not self.transmitting and not self.fenced,
                'sx1262_not_ready')
        require(not self.receive_pending, 'sx1262_completed_receive_pending')
        self._room(authority_deadline, 0.020)
        try:
            reuse = (self._checked_rearm and token is not None
                     and token == self._handoff_state())
            if not reuse:
                self.standby()
                self.clear_irq()
            else:
                object.__setattr__(self, '_rearm_transfer_epoch', token)
            self._packet(255)
            self._write(0x08, struct.pack('!HHHH', RX_MASK, RX_MASK, 0, 0))
            self.io.receive_switch(True)
            available_us = int((authority_deadline - self._now() - 0.01) * 1000000) - self._rx_tail_us()
            # Allow preamble acquisition across a maximum-frame-sized gap.
            # In explicit-header LoRa mode the finite RX timer stops after a
            # valid header; reception can then extend by the bounded payload.
            window_us = min(math.ceil(self._airtime(self.profile, 255) * 1.10)
                            + 250000, available_us)
            require(window_us > 0, 'sx1262_authority_deadline')
            self._write(0x82, self._rtc_steps(window_us))
            self.receiving = True
        except BaseException:
            self.contain()
            raise

    @timed(Phase.HAT_RX_POLL)
    def poll_receive(self):
        object.__setattr__(self, '_drained_handoff', None)
        require((self.receiving or self.receive_pending) and not self.fenced, 'sx1262_not_receiving')
        reasons = self.receive_reasons
        reading_packet = False
        try:
            flags = self.irq()
            if self.receive_timeline is not None:
                self.receive_timeline.irq(flags)
            if reasons is not None:
                if flags & (CRC_ERROR | HEADER_ERROR):
                    reasons.record('irq_crc_header' if flags & CRC_ERROR and flags & HEADER_ERROR
                                   else 'irq_crc' if flags & CRC_ERROR else 'irq_header')
                if flags & TIMEOUT:
                    reasons.record('irq_timeout')
            if not flags & RX_MASK:
                return None
            with phase(getattr(self, 'host_timing', None), Phase.HAT_RX_DRAIN):
                self.standby()
                # DS15.3: stop RTC and clear event after any timed RX, even explicit header.
                self._write_register(0x0902, b'\0')
                self._bits(0x0944, 0, 2)
                packet = None
                if flags & RX_DONE and not flags & (CRC_ERROR | HEADER_ERROR | TIMEOUT):
                    with phase(getattr(self, 'host_timing', None), Phase.HAT_RX_PACKET_READ):
                        reading_packet = True
                        length, offset = self._read(0x13, 2)
                        # DS2.2 table 13-68 permits LoRa payload length zero.
                        # It cannot contain an authenticated wire frame. Drop
                        # it without a FIFO read or accepted receive evidence;
                        # SPI status/length checks and IRQ cleanup stay strict.
                        if length == 0:
                            self.rx_errors += 1
                            if reasons is not None:
                                reasons.record('zero_length')
                        else:
                            data = self._read(0x1E, length, bytes([offset]))
                            packet = self._receive_evidence(data, self._read(0x14, 3))
                        reading_packet = False
                elif flags & (CRC_ERROR | HEADER_ERROR):
                    self.rx_errors += 1
                self.clear_irq()
                self.receive_pending = False
                handoff = self._handoff_state() if self._checked_rearm else None
                if packet is not None and reasons is not None:
                    reasons.record('physical_packet_returned')
                if self._checked_rearm:
                    object.__setattr__(self, '_drained_handoff', handoff)
                return packet
        except BaseException:
            if reasons is not None:
                reasons.record('poll_failure')
                if reading_packet:
                    reasons.record('packet_read_failure')
            self.contain()
            raise

    def _rx_tail_us(self):
        return 0  # Preserve the legacy LoRa receive timing contract.

    def _receive_evidence(self, data, status):
        rssi, snr, _signal_rssi = status
        signed_snr = snr - 256 if snr >= 128 else snr
        return RawReceive(-rssi / 2, signed_snr / 4, data)

    def contain(self):
        """Contain ambiguous hardware outcome by holding RESET low, then fence."""
        object.__setattr__(self, '_drained_handoff', None)
        self.fenced = True
        self.receiving = self.transmitting = False
        self.receive_pending = False
        try:
            self.io.reset(False)
        finally:
            self.io.receive_switch(True)

    def close(self):
        object.__setattr__(self, '_drained_handoff', None)
        if self.fenced:
            return
        try:
            self.standby()
        except (OSError, HardwareError):
            self.contain()
