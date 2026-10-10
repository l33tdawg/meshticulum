"""Bounded serial worker for one provisioned bench run.

Composition must supply the exclusively held USB port, a root-provisioned
session context and its live authority callback. This module does not grant
authority or authenticate raw RECEIVE frames. No network listener is present.
Driver RF_DONE is physical-driver evidence, never a peer delivery receipt.
"""
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import math
import secrets

from .linux_hat import HardwareError, require
from .serial_protocol import (Decoder, FirmwareIdentity, Frame, Message, Outcome,
                              RadioProfile, RawReceive, SerialError, TxEvidence)


def estimated_airtime_us(profile, size):
    """Source LoRa airtime estimate: explicit header, CRC, SF7..12.

    Same PHY assumptions as the coprocessor; includes no measured airtime or
    host latency. Reservations add margin separately and never receive refunds.
    """
    profile.encode(board_id=2)
    require(type(size) is int and 1 <= size <= 255, 'invalid_radio_size')
    symbol_us = (1 << profile.spreading_factor) * 1000000 / profile.bandwidth_hz
    low_rate = int(symbol_us >= 16000)
    coded = max(0, math.ceil((8 * size + 44 - 4 * profile.spreading_factor) /
                             (4 * (profile.spreading_factor - 2 * low_rate))))
    symbols = profile.preamble + 12.25 + coded * profile.coding_rate
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


class SerialWorker:
    def __init__(self, channel):
        self.channel = channel
        self.decoder = Decoder()
        self.identity = None
        self.journal = self.profile = self.authorize = None
        self.deadline = self.armed_until = 0.0
        self.fenced = False
        self._pending = deque()
        self._received = deque()
        self.dropped_receive = self.unmatched_replies = 0

    def _ingest(self, data):
        for frame in self.decoder.feed(data):
            if self.identity is not None:
                require(frame.boot == self.identity.boot, 'radio_boot_changed')
                if frame.type is Message.RECEIVE:
                    received = RawReceive.from_frame(frame, boot=self.identity.boot)
                    if len(self._received) == 32:
                        self.dropped_receive += 1
                    else:
                        self._received.append((self.channel.monotonic(), received))
                    continue
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
        require(not self.fenced and self.authorize is not None
                and self.authorize() is True, 'radio_authority_refused')
        require(self.channel.monotonic() + needed < self.deadline, 'radio_authority_expired')

    def arm(self, *, journal, profile, context_digest, authorize, authority_deadline):
        require(self.identity is not None and self.journal is None, 'invalid_arm_state')
        require(type(profile) is RadioProfile and callable(authorize), 'invalid_arm_parameters')
        require(type(authority_deadline) in (int, float) and math.isfinite(authority_deadline),
                'invalid_authority_deadline')
        require(journal.binding == journal_binding(context_digest, self.identity, profile),
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
            self._live(profile.lease_ms / 1000 + 0.5)
            request = journal.command_request()
            frame = Frame(Message.ARM, self.identity.boot, request,
                          profile.encode(board_id=self.identity.board_id))
            started = self.channel.monotonic()
            self._send(frame, started + 2.0)
            reply = self._wait(request, started + 2.0)
            require(reply.type is Message.ARM_REPLY and reply.body == frame.body, 'arm_not_confirmed')
            self.armed_until = started + profile.lease_ms / 1000 - 0.1
        except BaseException:
            self.fenced = True
            raise

    def transmit(self, packet):
        require(self.profile is not None and type(packet) is bytes and 1 <= len(packet) <= 255,
                'invalid_transmit')
        estimate = estimated_airtime_us(self.profile, len(packet))
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
        return AttemptResult(request, result, reservation, driver)

    def receive(self, timeout=0.0):
        self._live()
        require(type(timeout) in (int, float) and 0 <= timeout <= 0.1, 'invalid_receive_timeout')
        if not self._received:
            try:
                self._ingest(self.channel.read(timeout))
            except (OSError, HardwareError, SerialError):
                self.fenced = True
                raise
        return self._received.popleft() if self._received else None

    def disarm(self):
        require(self.journal is not None and self.identity is not None, 'missing_owner')
        self.armed_until = 0.0
        request = self.journal.command_request(containment=True)
        deadline = self.channel.monotonic() + 2.0
        self._send(Frame(Message.DISARM, self.identity.boot, request), deadline)
        reply = self._wait(request, deadline)
        require(reply.type is Message.DISARM_REPLY and reply.body == b'\0', 'disarm_not_confirmed')
