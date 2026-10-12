"""Exclusive SPI driver with durable attempts, matching the USB worker outcome API.

The caller owns LinuxHAT and a separately provisioned, current bench authority.
No default permissive authority and no automatic antenna confirmation exist.
A new driver reset requires a fresh run; interrupted resource transfer resumes
through a newly provisioned outer session, never by replaying an old RF attempt.
"""
import hashlib
import json
import math
import os
import threading
import time
from types import BuiltinFunctionType, MethodType
import uuid

from .linux_hat import LinuxHAT, HardwareError, require
from .serial_protocol import Outcome, RadioProfile
from .serial_worker import AttemptResult, estimated_airtime_us
from .sx1262 import SX1262


_NATIVE_MONOTONIC = time.monotonic


def journal_binding(context_digest, host_boot_id, profile):
    require(type(context_digest) is bytes and len(context_digest) == 32, 'invalid_run_context')
    require(type(host_boot_id) is str and str(uuid.UUID(host_boot_id)) == host_boot_id,
            'invalid_host_boot')
    return hashlib.sha256(b'meshticulum.hat-attempts.v1\0' + context_digest
                          + host_boot_id.encode('ascii') + profile.encode(board_id=2)).digest()


class HATWorker:
    _profile_type = RadioProfile
    _driver_type = SX1262
    _airtime = staticmethod(estimated_airtime_us)

    __slots__ = ('__dispatch', '__dict__')

    def __setattr__(self, name, value):
        if name == '_HATWorker__dispatch' and hasattr(self, name):
            raise HardwareError('hat_owner_guard_changed')
        object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if name == '_HATWorker__dispatch':
            raise HardwareError('hat_owner_guard_changed')
        object.__delattr__(self, name)

    def __init__(self, transport, *, journal, profile, context_digest, authorize,
                 authority_deadline, antenna_confirmed, monotonic=None,
                 time_basis='host-monotonic'):
        require(type(profile) is self._profile_type and callable(authorize), 'invalid_hat_authority')
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
        profile_type,driver_type,airtime=self._profile_type,self._driver_type,self._airtime
        self.io, self.journal, self.profile = transport, journal, profile
        self.authorize, self.deadline = authorize, authority_deadline
        self.antenna_confirmed, self.fenced = antenna_confirmed, False
        # Native deadlines come directly from Node/RunAuthority's host monotonic
        # epoch. Never translate an arbitrary caller clock into a fresh lease.
        if time_basis == 'host-monotonic':
            require(type(transport) is LinuxHAT and monotonic is None
                    and type(_NATIVE_MONOTONIC) is BuiltinFunctionType
                    and _NATIVE_MONOTONIC.__module__ == 'time'
                    and _NATIVE_MONOTONIC.__name__ == 'monotonic'
                    and transport.monotonic is _NATIVE_MONOTONIC, 'hat_clock_basis_unverified')
            native_clock = _NATIVE_MONOTONIC
        else:
            require(time_basis == 'model-monotonic' and not isinstance(transport, LinuxHAT)
                    and callable(monotonic), 'hat_clock_basis_unverified')
            native_clock = monotonic
        self._clock, self.time_basis = native_clock, time_basis
        self.radio = self._driver_type(transport, monotonic=native_clock)
        self.lease_deadline = min(authority_deadline, native_clock() + profile.lease_ms / 1000)
        original_end = min(authority_deadline, self.lease_deadline)
        pid, thread = os.getpid(), threading.get_ident()
        original_radio = self.radio
        original_boot = transport.boot_id
        transport_clock = transport.monotonic
        def same_transport_clock():
            current = transport.monotonic
            return (type(current) is MethodType and type(transport_clock) is MethodType
                    and current.__self__ is transport_clock.__self__
                    and current.__func__ is transport_clock.__func__) or current is transport_clock
        driver_clock = original_radio._now
        driver_airtime=original_radio._airtime
        failed = False
        def owned(*, contain=False, disarm=False):
            nonlocal failed
            if contain or disarm:
                failed = True
                valid_owner = (os.getpid() == pid and threading.get_ident() == thread
                               and original_radio.io is transport)
                if disarm:
                    require(valid_owner, 'hat_cleanup_owner_changed')
                    original_radio.close()
                elif valid_owner:
                    original_radio.contain()
                return
            require(not failed and not self.fenced and not original_radio.fenced
                    and os.getpid() == pid and threading.get_ident() == thread
                    and self.io is transport and self.radio is original_radio
                    and original_radio.io is transport and transport.boot_id == original_boot
                    and self.journal is journal and self.profile is profile
                    and self.authorize is authorize
                    and self._clock is native_clock and self.time_basis == time_basis
                    and same_transport_clock()
                    and original_radio._now is driver_clock
                    and self._profile_type is profile_type and self._driver_type is driver_type
                    and self._airtime is airtime and original_radio._airtime is driver_airtime, 'hat_owner_or_clock_changed')
            require(type(self.deadline) in (int, float) and math.isfinite(self.deadline)
                    and type(self.lease_deadline) in (int, float)
                    and math.isfinite(self.lease_deadline), 'invalid_hat_deadline')
            return min(original_end, self.deadline, self.lease_deadline)
        self._owned = owned
        def dispatch(*, contain=False, disarm=False):
            # This closure retains original ownership independently of the mutable
            # public worker fields, including replacements between operations.
            if contain or disarm:
                return owned(contain=contain, disarm=disarm)
            require(self.__dispatch is dispatch and self._owned is owned,
                    'hat_owner_guard_changed')
            return owned()
        self.__dispatch = dispatch
        self._live()
        self.radio.configure(profile)

    @property
    def _dispatch(self):
        return self.__dispatch

    def _live(self, needed=0):
        require(not self.fenced, 'hat_authority_refused')
        owned, authorize = self._dispatch, self.authorize
        owned()
        # Any caller transport callback runs BEFORE the fresh permission check.
        # The last lease sample uses the captured clock, never that callback.
        self.io.monotonic()
        require(self._dispatch is owned and self.authorize is authorize, 'hat_authority_changed')
        owned()
        require(not self.fenced and authorize() is True, 'hat_authority_refused')
        require(self._dispatch is owned and self.authorize is authorize, 'hat_authority_changed')
        end = owned()
        now = self._clock()
        require(type(now) in (int, float) and math.isfinite(now), 'invalid_hat_clock')
        require(now + needed < end, 'hat_authority_expired')
        require(self._dispatch is owned and self.authorize is authorize, 'hat_authority_changed')
        owned()
        return now

    def transmit(self, packet):
        require(not self.fenced, 'hat_authority_refused')
        require(self.antenna_confirmed is True, 'hat_antenna_not_confirmed')
        require(type(packet) is bytes and 1 <= len(packet) <= 255, 'invalid_hat_packet')
        owned = self._dispatch
        receive_deadline = owned()
        radio, transport, journal, profile = self.radio, self.io, self.journal, self.profile
        estimate = self._airtime(profile, len(packet))
        reservation = math.ceil(estimate * 1.10) + 1000
        timeout = reservation / 1000000 + 0.5
        self._live(timeout)
        request = journal.reserve(packet, reservation)
        outcome, evidence, driver = 'UNKNOWN', 'unsettled', None
        def receive_failure(error):
            self.fenced = True
            # The original owner contains; a substituted driver's transport
            # must never be used to handle a failed RX handoff.
            try:
                owned(contain=True)
            except Exception:
                pass
            if not isinstance(error, Exception):
                raise error
        try:
            try:
                self._live(timeout)
            except HardwareError:
                outcome, evidence = 'NOT_SENT', 'authority_changed_before_spi'
                return AttemptResult(request, outcome, reservation, None)
            started = radio.start_transmit(packet, authority_deadline=owned(),
                                           antenna_confirmed=self.antenna_confirmed)
            if started is False:
                # A completed RX remained intact before any TX/FIFO write.
                # Recheck the original owner/lease; the paid reservation is
                # settled, never refunded, and ordinary receive drains it.
                self._live()
                outcome, evidence = 'NOT_SENT', 'completed_receive_pending_before_tx'
                return AttemptResult(request, outcome, reservation, None)
            while driver is None:
                driver = radio.poll_transmit()
                if driver is None:
                    transport.sleep(0.001)
            require(type(profile) is RadioProfile or driver.estimated_airtime_us==estimate<=reservation,
                    'gfsk_driver_estimate_changed')
            outcome = 'RF_DONE' if driver.outcome is Outcome.RF_DONE else 'UNKNOWN'
            evidence = json.dumps({'driver_estimate_us': driver.estimated_airtime_us,
                'host_driver_elapsed_us': driver.driver_elapsed_us, 'irq': driver.irq_flags,
                'error': driver.driver_error}, separators=(',', ':'))
            if outcome == 'RF_DONE':
                # The attempt was durably reserved before RF. Once TX_DONE is
                # known, listen while its result is saved, rather than leaving
                # the radio in standby through journal fsync. RX failure is
                # separate and cannot erase this known transmit evidence.
                try:
                    self._live()
                    require(self.radio is radio and self.io is transport and radio.io is transport,
                            'hat_driver_changed_after_transmit')
                    radio.start_receive(authority_deadline=min(receive_deadline, self.deadline,
                                                              self.lease_deadline))
                except BaseException as error:
                    receive_failure(error)
        except (OSError, HardwareError):
            outcome, evidence, driver = 'UNKNOWN', 'spi_or_evidence_failure', None
        finally:
            if outcome == 'UNKNOWN':
                self.fenced = True
                try:
                    owned(contain=True)
                except OSError:
                    pass
            try:
                journal.settle(request, packet, outcome, evidence)
            except BaseException:
                self.fenced = True
                owned(contain=True)
                raise
        if outcome == 'RF_DONE' and not self.fenced:
            # Retain the fresh check after durable settlement: fsync may have
            # crossed the original deadline or a revocation boundary. No
            # packet is returned during settlement, and failed authority now
            # contains the already-listening radio without rewriting TX_DONE.
            try:
                self._live()
            except BaseException as error:
                receive_failure(error)
        clock_basis = ('host_monotonic' if self.time_basis == 'host-monotonic'
                       else 'model_monotonic') if driver is not None else None
        return AttemptResult(request, outcome, reservation, driver, clock_basis)

    def receive(self, timeout=0.0):
        require(type(timeout) in (int, float) and 0 <= timeout <= 0.1, 'invalid_receive_timeout')
        owned = self._dispatch
        try:
            owned()
            radio, transport = self.radio, self.io
            now = self._live()
            until = min(now + timeout, owned())
            while True:
                if not radio.receiving and not radio.receive_pending:
                    radio.start_receive(authority_deadline=owned())
                    # Configuration runs transport operations; recheck before poll.
                    self._live()
                # Already-listening polls follow the fresh entry check directly.
                # Only the captured clock is called inside the driver thereafter.
                received = radio.poll_receive()
                if received is not None:
                    now = self._live()
                    # poll_receive drains the packet in standby. Rearm while
                    # this original worker still owns the radio, so the next
                    # burst packet can arrive during the caller's decrypt,
                    # flow and credit processing. Keep the original finite
                    # deadline and fresh authority checks around SPI setup.
                    radio.start_receive(authority_deadline=owned())
                    now = self._live()
                    return now, received
                if not radio.receiving:
                    # Finite RX timeout and rejected header/CRC also leave
                    # the chip in standby. Restore RX before an empty return,
                    # so a peer ACK can arrive during upper-loop processing.
                    # The original finite lease and fresh permission still
                    # bound both the SPI operation and the returned state.
                    self._live()
                    radio.start_receive(authority_deadline=owned())
                    self._live()
                now = self._clock()
                require(type(now) in (int, float) and math.isfinite(now)
                        and now < owned(), 'hat_authority_expired')
                if now >= until:
                    return None
                transport.sleep(0.001)
                self._live()
        except BaseException:
            self.fenced = True
            try:
                owned(contain=True)
            except Exception:
                pass
            raise

    def disarm(self):
        self.lease_deadline = 0
        self._dispatch(disarm=True)


from .gfsk_profile import GfskProfile, estimated_airtime_us as gfsk_airtime_us
from .gfsk_sx1262 import GfskSX1262


class GfskHATWorker(HATWorker):
    """Explicit GFSK125 admission path; legacy HATWorker remains LoRa only."""
    _profile_type = GfskProfile
    _driver_type = GfskSX1262
    _airtime = staticmethod(gfsk_airtime_us)
