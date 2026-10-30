"""Bounded serial worker for one provisioned bench run.

Composition must supply the exclusively held USB port, a root-provisioned
session context and its live authority callback. This module does not grant
authority or authenticate raw RECEIVE frames. No network listener is present.
Driver RF_DONE is physical-driver evidence, never a peer delivery receipt.
"""
from collections import deque
from dataclasses import dataclass, replace
import hashlib
import json
import math
import secrets

from .linux_hat import HardwareError, require
from .host_timing import check_timing
from .serial_protocol import (Decoder, FirmwareIdentity, Frame, Message, Outcome,
                              RadioProfile, RawReceive, SerialError, TxEvidence)


def estimated_airtime_us(profile, size):
    """Source LoRa airtime estimate: explicit header, CRC, SF5..12.

    Semtech SX1261/2 section 6.1.4, matching the coprocessor's RadioLib
    SX126x::calculateTimeOnAir. SF5/6 use their shorter coded header and longer
    synchronization suffix. This is not measured airtime or host latency;
    reservations add margin separately and never receive refunds.
    """
    profile.encode(board_id=2)
    require(type(size) is int and 1 <= size <= 255, 'invalid_radio_size')
    symbol_us = (1 << profile.spreading_factor) * 1000000 / profile.bandwidth_hz
    low_rate = int(symbol_us >= 16000)
    short_sf = profile.spreading_factor <= 6
    coded = max(0, math.ceil((8 * size + (36 if short_sf else 44) - 4 * profile.spreading_factor) /
                             (4 * (profile.spreading_factor - 2 * low_rate))))
    symbols = profile.preamble + (14.25 if short_sf else 12.25) + coded * profile.coding_rate
    return math.ceil(symbols * symbol_us)


def journal_binding(context_digest, identity, profile):
    require(type(context_digest) is bytes and len(context_digest) == 32, 'invalid_run_context')
    return hashlib.sha256(b'meshticulum.radio-attempts.v1\0' + context_digest + identity.boot
                          + bytes([identity.board_id]) + identity.version.encode('ascii')
                          + profile.encode(board_id=identity.board_id)).digest()


@dataclass(frozen=True, slots=True)
class AttemptResult:
    request: int
    outcome: str
    reserved_estimated_us: int
    driver: TxEvidence | None
    driver_elapsed_clock_basis: str | None = None


class SerialWorker:
    _profile_type = RadioProfile
    _arm_message = Message.ARM
    _arm_reply = Message.ARM_REPLY
    _receive_message = Message.RECEIVE
    _receive_record = RawReceive
    _airtime = staticmethod(estimated_airtime_us)
    _exact_driver_estimate = False

    __slots__ = ('__mode_check','__dict__')

    def __setattr__(self,name,value):
        if name=='_SerialWorker__mode_check' and hasattr(self,name):raise HardwareError('serial_modem_guard_changed')
        object.__setattr__(self,name,value)

    def __delattr__(self,name):
        if name=='_SerialWorker__mode_check':raise HardwareError('serial_modem_guard_changed')
        object.__delattr__(self,name)

    def __init__(self, channel, *, host_timing=None):
        self.host_timing = check_timing(host_timing)
        self.channel = channel
        names=('_profile_type','_arm_message','_arm_reply','_receive_message','_receive_record','_airtime','_exact_driver_estimate')
        original=tuple(getattr(self,name) for name in names)
        def mode_check():
            require(self.__mode_check is mode_check and all(getattr(self,name) is value for name,value in zip(names,original)),
                    'serial_modem_dispatch_changed')
        self.__mode_check=mode_check
        self.decoder = Decoder()
        self.identity = None
        self.journal = self.profile = self.authorize = None
        self.arm_lease_ms = None
        self.arm_confirmed_monotonic = self.arm_lease_end_upper_bound = None
        self.deadline = self.armed_until = 0.0
        self.fenced = False
        self._pending = deque()
        self._received = deque()
        self.dropped_receive = self.unmatched_replies = 0

    def _ingest(self, data):
        for frame in self.decoder.feed(data):
            if self.identity is not None:
                require(frame.boot == self.identity.boot, 'radio_boot_changed')
                if frame.type is self._receive_message:
                    received = self._receive_record.from_frame(frame, boot=self.identity.boot)
                    if len(self._received) == 32:
                        self.dropped_receive += 1
                    else:
                        self._received.append((self.channel.monotonic(), received))
                    continue
                require(frame.type not in (Message.RECEIVE, Message.GFSK_RECEIVE, Message.GFSK250_RECEIVE), 'wrong_receive_modem')
            require(len(self._pending) < 64, 'serial_reply_queue_full')
            self._pending.append(frame)

    def _wait(self, request, deadline):
        while True:
            while self._pending:
                frame = self._pending.popleft()
                if self.identity is not None:
                    require(frame.boot == self.identity.boot, 'radio_boot_changed')
                if frame.request == request:
                    return frame
                self.unmatched_replies += 1
            left = deadline - self.channel.monotonic()
            require(left > 0, 'serial_reply_deadline')
            self._ingest(self.channel.read(min(left, 0.05)))

    def _send(self, frame, deadline):
        self.channel.write(frame.encode(), min(deadline, self.channel.monotonic() + 1.0))

    def discover(self, *, expected_board_id, expected_version):
        require(self.identity is None, 'already_discovered')
        request = secrets.randbits(63) or 1
        deadline = self.channel.monotonic() + 3.0
        self._send(Frame(Message.HELLO, bytes(16), request), deadline)
        reply = self._wait(request, deadline)
        self.identity = FirmwareIdentity.from_reply(reply, request=request,
            expected_board_id=expected_board_id, expected_version=expected_version)
        return self.identity

    def _live(self, needed=0.0):
        check=self.__mode_check;check()
        require(not self.fenced and self.authorize is not None
                and self.authorize() is True, 'radio_authority_refused')
        require(self.__mode_check is check,'serial_modem_guard_changed');check()
        require(self.channel.monotonic() + needed < self.deadline, 'radio_authority_expired')

    def arm(self, *, journal, profile, context_digest, authorize, authority_deadline):
        require(self.identity is not None and self.journal is None, 'invalid_arm_state')
        require(type(profile) is self._profile_type and callable(authorize), 'invalid_arm_parameters')
        require(type(authority_deadline) in (int, float) and math.isfinite(authority_deadline),
                'invalid_authority_deadline')
        admitted_body = profile.encode(board_id=self.identity.board_id)
        admitted_binding = journal_binding(context_digest, self.identity, profile)
        require(journal.binding == admitted_binding,
                'wrong_radio_journal_binding')
        require(not journal.snapshot()['fenced'], 'recovered_run_requires_new_session')
        require(journal.max_attempts <= profile.max_boot_attempts
                and journal.max_airtime_us <= profile.max_boot_estimated_airtime_us,
                'host_limits_exceed_firmware_limits')
        self.journal, self.profile, self.authorize = journal, profile, authorize
        self.deadline = authority_deadline
        self._live()
        try:
            self.disarm()
            self._live()
            request = journal.command_request()
            self._live()
            require(self.profile is profile and self.journal is journal and self.authorize is authorize and self.deadline == authority_deadline
                    and journal.binding == admitted_binding and profile.encode(board_id=self.identity.board_id) == admitted_body,
                    'radio_arm_binding_changed')
            started = self.channel.monotonic()
            # The admitted profile remains the immutable journal/crypto ceiling.
            # Preparation and disarm can consume its setup reserve. Narrow only
            # the actual MCU lease, allowing the existing two-second ARM reply
            # bound and 100ms margin before the original absolute authority end.
            room_us = math.floor(authority_deadline * 1000000) - math.ceil(started * 1000000) - 2100000
            lease_ms = min(profile.lease_ms, room_us // 1000)
            require(lease_ms >= 1000, 'radio_authority_expired')
            armed_profile = profile if lease_ms == profile.lease_ms else replace(profile, lease_ms=lease_ms)
            frame = Frame(self._arm_message, self.identity.boot, request,
                          armed_profile.encode(board_id=self.identity.board_id))
            self._send(frame, started + 2.0)
            reply = self._wait(request, started + 2.0)
            confirmed = self.channel.monotonic()
            require(confirmed < started + 2.0, 'serial_reply_deadline')
            require(reply.type is self._arm_reply and reply.body == frame.body, 'arm_not_confirmed')
            self.arm_lease_ms = lease_ms
            self.arm_confirmed_monotonic = confirmed
            self.arm_lease_end_upper_bound = confirmed + lease_ms / 1000
            self.armed_until = started + lease_ms / 1000 - 0.1
        except BaseException:
            self.fenced = True
            raise

    def transmit(self, packet):
        self.__mode_check()
        require(self.profile is not None and type(packet) is bytes and 1 <= len(packet) <= 255,
                'invalid_transmit')
        estimate = self._airtime(self.profile, len(packet))
        reservation = math.ceil(estimate * 1.10) + 1000
        timeout = reservation / 1000000 + 1.5
        self._live(timeout)
        require(self.channel.monotonic() + timeout < self.armed_until, 'radio_lease_too_short')
        request = self.journal.reserve(packet, reservation)  # committed before any write
        result, evidence, driver = 'UNKNOWN', 'unsettled', None
        try:
            try:
                self._live(timeout)
            except HardwareError:
                result, evidence = 'NOT_SENT', 'authority_changed_before_write'
                return AttemptResult(request, result, reservation, None)
            deadline = self.channel.monotonic() + timeout
            self._send(Frame(Message.TRANSMIT, self.identity.boot, request, packet), deadline)
            # After write, settle the physical attempt even if service authority
            # expires. The caller separately refuses delivery under expired authority.
            saw_start = False
            while True:
                reply = self._wait(request, deadline)
                if reply.type is Message.REFUSED:
                    require(len(reply.body) == 1 and not saw_start, 'contradictory_refusal')
                    result, evidence = 'REFUSED', 'firmware_reason_' + str(reply.body[0])
                    break
                driver = TxEvidence.from_reply(reply, boot=self.identity.boot,
                                               request=request, packet=packet)
                require(driver.estimated_airtime_us <= reservation, 'driver_estimate_exceeded_reservation')
                require(not self._exact_driver_estimate or driver.estimated_airtime_us == estimate, 'wrong_modem_driver_estimate')
                if driver.outcome is Outcome.STARTED:
                    saw_start = True
                    continue
                result = 'RF_DONE' if driver.outcome is Outcome.RF_DONE else 'UNKNOWN'
                evidence = json.dumps({'driver_estimate_us': driver.estimated_airtime_us,
                    'driver_elapsed_us': driver.driver_elapsed_us, 'irq': driver.irq_flags,
                    'error': driver.driver_error}, separators=(',', ':'))
                break
        except (OSError, HardwareError, SerialError):
            result, evidence, driver = 'UNKNOWN', 'transport_or_evidence_failure', None
        finally:
            if result == 'UNKNOWN':
                self.fenced = True
            try:
                self.journal.settle(request, packet, result, evidence)
            except BaseException:
                self.fenced = True
                raise
        return AttemptResult(request, result, reservation, driver,
                             'coprocessor_monotonic' if driver is not None else None)

    def receive(self, timeout=0.0):
        self._live()
        require(self.channel.monotonic() < self.armed_until, 'radio_lease_expired')
        require(type(timeout) in (int, float) and 0 <= timeout <= 0.1, 'invalid_receive_timeout')
        if not self._received:
            try:
                self._ingest(self.channel.read(timeout))
            except (OSError, HardwareError, SerialError):
                self.fenced = True
                raise
        require(self.channel.monotonic() < self.armed_until, 'radio_lease_expired')
        return self._received.popleft() if self._received else None

    @property
    def has_buffered_activity(self):
        """Only receive work the next step can consume, not unmatched replies."""
        return bool(self._received)

    def wait_for_activity(self, timeout):
        """Non-consuming wake hint; None means the channel lacks this capability.

        Callers must use their bounded sleep fallback for None, rather than
        interpreting unsupported readiness as an immediate timeout.
        """
        require(type(timeout) in (int, float) and 0 <= timeout <= .005
                and math.isfinite(timeout), 'invalid_serial_activity_timeout')
        if self.has_buffered_activity:
            return True
        wait = getattr(self.channel, 'wait_readable', None)
        if not callable(wait):
            return None
        ready = wait(timeout)
        require(type(ready) is bool, 'invalid_serial_activity_result')
        return ready

    def disarm(self):
        require(self.journal is not None and self.identity is not None, 'missing_owner')
        self.armed_until = 0.0
        request = self.journal.command_request(containment=True)
        deadline = self.channel.monotonic() + 2.0
        self._send(Frame(Message.DISARM, self.identity.boot, request), deadline)
        reply = self._wait(request, deadline)
        require(reply.type is Message.DISARM_REPLY and reply.body == b'\0', 'disarm_not_confirmed')


from .gfsk_profile import GfskProfile, estimated_airtime_us as gfsk_airtime_us
from .serial_protocol import GfskRawReceive


class GfskSerialWorker(SerialWorker):
    """Explicit finite GFSK worker. Constructing it opens no port or authority."""
    _profile_type = GfskProfile
    _arm_message = Message.GFSK_ARM
    _arm_reply = Message.GFSK_ARM_REPLY
    _receive_message = Message.GFSK_RECEIVE
    _receive_record = GfskRawReceive
    _airtime = staticmethod(gfsk_airtime_us)
    _exact_driver_estimate = True


from .gfsk250_profile import Gfsk250Profile, CAPABILITY, estimated_airtime_us as gfsk250_airtime_us
from .serial_protocol import Gfsk250RawReceive


class Gfsk250SerialWorker(SerialWorker):
    """Separate ARM contract, requiring a current-boot read-only capability proof.

    Discover still requires the caller's exact firmware version. An older or
    mismatched firmware cannot reach ARM, even if its HELLO is otherwise valid.
    """
    _profile_type = Gfsk250Profile
    _arm_message = Message.GFSK250_ARM
    _arm_reply = Message.GFSK250_ARM_REPLY
    _receive_message = Message.GFSK250_RECEIVE
    _receive_record = Gfsk250RawReceive
    _airtime = staticmethod(gfsk250_airtime_us)
    _exact_driver_estimate = True
    __slots__ = ('__capability_check', '__discover250')

    def __init__(self, channel, *, host_timing=None):
        super().__init__(channel, host_timing=host_timing)
        binding = None
        discover_identity = super().discover
        send, wait = self._send, self._wait
        live_function = self._live.__func__
        def check():
            require(self.__capability_check is check and self.__discover250 is discover250
                    and getattr(self._live, '__self__', None) is self
                    and getattr(self._live, '__func__', None) is live_function,
                    'gfsk250_capability_guard_changed')
            require(binding is not None, 'gfsk250_firmware_capability_absent')
            identity, values = binding
            require(self.channel is channel and self.identity is identity
                    and (identity.boot, identity.board_id, identity.version) == values,
                    'gfsk250_firmware_capability_changed')
        def discover250(expected_board_id, expected_version):
            nonlocal binding
            require(self.__capability_check is check and self.__discover250 is discover250,
                    'gfsk250_capability_guard_changed')
            identity = discover_identity(expected_board_id=expected_board_id, expected_version=expected_version)
            request = secrets.randbits(63) or 1
            deadline = channel.monotonic() + 3.0
            send(Frame(Message.GFSK250_CAPABILITIES, identity.boot, request), deadline)
            reply = wait(request, deadline)
            require(self.channel is channel and channel.monotonic() < deadline
                    and reply.type is Message.GFSK250_CAPABILITIES_REPLY and reply.boot == identity.boot
                    and reply.body == CAPABILITY, 'gfsk250_firmware_capability_absent')
            binding = (identity, (identity.boot, identity.board_id, identity.version))
            check()
            return identity
        self.__capability_check = check
        self.__discover250 = discover250

    def __setattr__(self, name, value):
        if name in ('_Gfsk250SerialWorker__capability_check', '_Gfsk250SerialWorker__discover250', '_live') and hasattr(self, name):
            raise HardwareError('gfsk250_capability_guard_changed')
        super().__setattr__(name, value)

    def __delattr__(self, name):
        if name in ('_Gfsk250SerialWorker__capability_check', '_Gfsk250SerialWorker__discover250', '_live'):
            raise HardwareError('gfsk250_capability_guard_changed')
        super().__delattr__(name)

    def discover(self, *, expected_board_id, expected_version):
        return self.__discover250(expected_board_id, expected_version)

    def arm(self, **parameters):
        self.__capability_check()
        return super().arm(**parameters)

    def transmit(self, packet):
        self.__capability_check()
        return super().transmit(packet)

    def receive(self, timeout=0.0):
        self.__capability_check()
        return super().receive(timeout)

    def _live(self, needed=0.0):
        check = self.__capability_check
        require(callable(check), 'gfsk250_firmware_capability_absent')
        check()
        super()._live(needed)
        check()
