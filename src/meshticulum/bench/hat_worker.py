"""Exclusive SPI driver with durable attempts, matching the USB worker outcome API.

The caller owns LinuxHAT and a separately provisioned, current bench authority.
No default permissive authority and no automatic antenna confirmation exist.
A new driver reset requires a fresh run; interrupted resource transfer resumes
through a newly provisioned outer session, never by replaying an old RF attempt.
"""
import hashlib
import json
import math
import uuid

from .linux_hat import HardwareError, require
from .serial_protocol import Outcome, RadioProfile
from .serial_worker import AttemptResult, estimated_airtime_us
from .sx1262 import SX1262


def journal_binding(context_digest, host_boot_id, profile):
    require(type(context_digest) is bytes and len(context_digest) == 32, 'invalid_run_context')
    require(type(host_boot_id) is str and str(uuid.UUID(host_boot_id)) == host_boot_id,
            'invalid_host_boot')
    return hashlib.sha256(b'meshticulum.hat-attempts.v1\0' + context_digest
                          + host_boot_id.encode('ascii') + profile.encode(board_id=2)).digest()


class HATWorker:
    def __init__(self, transport, *, journal, profile, context_digest, authorize,
                 authority_deadline, antenna_confirmed):
        require(type(profile) is RadioProfile and callable(authorize), 'invalid_hat_authority')
        require(type(antenna_confirmed) is bool, 'invalid_antenna_confirmation')
        require(type(authority_deadline) in (int, float) and math.isfinite(authority_deadline),
                'invalid_authority_deadline')
        require(journal.binding == journal_binding(context_digest, transport.boot_id, profile),
                'wrong_hat_journal_binding')
        state = journal.snapshot()
        require(not state['fenced'] and state['attempts'] == 0, 'new_driver_requires_fresh_run')
        require(journal.max_attempts <= profile.max_boot_attempts
                and journal.max_airtime_us <= profile.max_boot_estimated_airtime_us,
                'host_limits_exceed_driver_limits')
        self.io, self.journal, self.profile = transport, journal, profile
        self.authorize, self.deadline = authorize, authority_deadline
        self.antenna_confirmed, self.fenced = antenna_confirmed, False
        self.radio = SX1262(transport)
        self.lease_deadline = min(authority_deadline, transport.monotonic() + profile.lease_ms / 1000)
        self._live()
        self.radio.configure(profile)

    def _live(self, needed=0):
        require(not self.fenced and self.authorize() is True, 'hat_authority_refused')
        require(self.io.monotonic() + needed < min(self.deadline, self.lease_deadline),
                'hat_authority_expired')

    def transmit(self, packet):
        require(self.antenna_confirmed is True, 'hat_antenna_not_confirmed')
        require(type(packet) is bytes and 1 <= len(packet) <= 255, 'invalid_hat_packet')
        estimate = estimated_airtime_us(self.profile, len(packet))
        reservation = math.ceil(estimate * 1.10) + 1000
        timeout = reservation / 1000000 + 0.5
        self._live(timeout)
        request = self.journal.reserve(packet, reservation)
        outcome, evidence, driver = 'UNKNOWN', 'unsettled', None
        try:
            try:
                self._live(timeout)
            except HardwareError:
                outcome, evidence = 'NOT_SENT', 'authority_changed_before_spi'
                return AttemptResult(request, outcome, reservation, None)
            self.radio.start_transmit(packet, authority_deadline=min(self.deadline, self.lease_deadline),
                                      antenna_confirmed=self.antenna_confirmed)
            while driver is None:
                driver = self.radio.poll_transmit()
                if driver is None:
                    self.io.sleep(0.001)
            outcome = 'RF_DONE' if driver.outcome is Outcome.RF_DONE else 'UNKNOWN'
            evidence = json.dumps({'driver_estimate_us': driver.estimated_airtime_us,
                'host_driver_elapsed_us': driver.driver_elapsed_us, 'irq': driver.irq_flags,
                'error': driver.driver_error}, separators=(',', ':'))
        except (OSError, HardwareError):
            outcome, evidence, driver = 'UNKNOWN', 'spi_or_evidence_failure', None
        finally:
            if outcome == 'UNKNOWN':
                self.fenced = True
                try:
                    self.radio.contain()
                except OSError:
                    pass
            try:
                self.journal.settle(request, packet, outcome, evidence)
            except BaseException:
                self.fenced = True
                self.radio.contain()
                raise
        return AttemptResult(request, outcome, reservation, driver)

    def receive(self, timeout=0.0):
        self._live()
        require(type(timeout) in (int, float) and 0 <= timeout <= 0.1, 'invalid_receive_timeout')
        until = min(self.io.monotonic() + timeout, self.deadline, self.lease_deadline)
        try:
            while True:
                self._live()
                if not self.radio.receiving:
                    self.radio.start_receive(authority_deadline=min(self.deadline, self.lease_deadline))
                received = self.radio.poll_receive()
                if received is not None:
                    self._live()
                    return self.io.monotonic(), received
                if self.io.monotonic() >= until:
                    return None
                self.io.sleep(0.001)
        except (OSError, HardwareError):
            self.fenced = True
            self.radio.contain()
            raise

    def disarm(self):
        self.lease_deadline = 0
        self.radio.close()
