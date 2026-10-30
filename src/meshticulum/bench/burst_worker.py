"""Opt-in LoRa DATA queue over the exclusively held bench serial channel.

Linux retains crypto, peer ACKs and retries. Queue acceptance is charged carrier
handover, not RF completion. Physical accounting/release survives flow retirement
and can finish after authority expiry; none of those reads permits another TX.
"""
from collections import deque
import hashlib
import json
import math

from .attempts import AttemptError
from .burst_protocol import (CAPABILITY, CANCEL, TOKEN, INTER_PACKET_RX_US,
    REVERSE_LISTEN_US, MIN_MCU_RATE_PPM, CLOCK_MARGIN_US, BurstEvent, BurstStatus,
    ClockSnapshot, OpenBinding, ack_frame, check_capability, data_frame,
    open_frame, query_frame, validate_policy, policy_depth, capability_for_depth,
    capability_request_body)
from .linux_hat import HardwareError, require
from .host_timing import Phase, phase, timed
from .serial_protocol import Frame, Message, Outcome, RadioProfile, SerialError, TxEvidence
from .serial_worker import AttemptResult, SerialWorker
from .tx_burst import (BurstContractError, PacketDescriptor, Stage, TERMINAL,
                      START_GUARD_US, context_sha256)


_FAILURES = (OSError, HardwareError, SerialError, BurstContractError, AttemptError)


class BurstSerialWorker(SerialWorker):
    """Explicit keyless MCU queue; synchronous transmit remains ACK/control.

    One event-loop owner serializes reservations and mutations. In particular,
    terminal ACKs are never written from an event callback inside another wait.
    """
    def __init__(self, channel, *, policy, host_timing=None):
        copied = validate_policy(policy)
        self._burst_policy = tuple(sorted(copied.items()))
        self._depth = policy_depth(copied)
        self._burst_capability = None
        self._open = None
        self._arm_request = None
        self._burst_deadline = None
        self._burst_clock = None
        self._burst_count = 0
        self._physical = {}
        self._logical = {}
        self._released_events = deque(maxlen=self._depth)
        self._release_replies = deque(maxlen=self._depth)
        self._terminals = deque()
        self._physical_events = deque(maxlen=512)
        self._routed = {}
        self._waiting = 0
        self._cancelled = False
        self.last_burst_status = None
        self._last_clock = None
        self._last_clock_received_us = None
        super().__init__(channel, host_timing=host_timing)

    @property
    def data_burst_policy(self):
        return dict(self._burst_policy)

    @property
    def pending_physical(self):
        return tuple(self._physical)

    @property
    def pending_logical_ids(self):
        return frozenset(self._logical)

    @property
    def has_capacity(self):
        return (self._open is not None and not self.fenced
                and len(self._physical) < self._depth and self._burst_count < self._depth)

    @property
    def pending_airtime_us(self):
        if not self._physical:
            return 0
        # Active work retains its full estimate conservatively. This is a
        # backlog bound, not measured airtime or a credit/debt refund.
        pending = sum(record['descriptor'].estimated_airtime_us + START_GUARD_US
                      + INTER_PACKET_RX_US for record in self._physical.values()
                      if record['event'] is None or record['event'].stage not in TERMINAL)
        cooldown = REVERSE_LISTEN_US
        if self.last_burst_status is not None:
            cooldown = max(cooldown, self.last_burst_status.next_start_us
                           - self.last_burst_status.now_us)
        return pending + cooldown

    def physical_snapshot(self, request=None):
        """Bounded local diagnostics; MCU timestamps are not peer receipts."""
        require(request is None or type(request) is int and request > 0,
                'invalid_physical_snapshot_request')
        return [dict(event) for event in self._physical_events
                if request is None or event['request'] == request]

    def _send(self, frame, deadline):
        if frame.type is Message.ARM:
            self._arm_request = frame.request
        return super()._send(frame, deadline)

    def _ingest(self, data):
        super()._ingest(data)
        while self._pending:
            frame = self._pending.popleft()
            if frame.type is Message.BURST_EVENT:
                self._event(frame)
            elif frame in self._release_replies:
                continue  # Exact delayed duplicate of a confirmed release.
            else:
                require(sum(map(len, self._routed.values())) < 64, 'burst_reply_router_full')
                bucket = self._routed.setdefault(frame.request, deque())
                require(len(bucket) < 4, 'burst_request_router_full')
                bucket.append(frame)

    def _pop_reply(self, request):
        bucket = self._routed.get(request)
        if not bucket:
            return None
        frame = bucket.popleft()
        if not bucket:
            del self._routed[request]
        return frame

    def _wait(self, request, deadline):
        self._waiting += 1
        try:
            while True:
                reply = self._pop_reply(request)
                if reply is not None:
                    return reply
                left = deadline - self.channel.monotonic()
                require(left > 0, 'serial_reply_deadline')
                self._ingest(self.channel.read(min(left, .05)))
        finally:
            self._waiting -= 1

    def discover(self, *, expected_board_id, expected_version):
        identity = super().discover(expected_board_id=expected_board_id,
                                    expected_version=expected_version)
        # Read-only discovery has no journal yet; its request need not mutate.
        import secrets
        request = secrets.randbits(63) or 1
        deadline = self.channel.monotonic() + 1
        self._send(Frame(Message.BURST_CAPABILITIES, identity.boot, request,
                         capability_request_body(self._depth)), deadline)
        reply = self._wait(request, deadline)
        check_capability(reply, boot=identity.boot, request=request, depth=self._depth)
        self._burst_capability = (identity, capability_for_depth(self._depth))
        return identity

    def _capability(self):
        require(self._burst_capability == (self.identity, capability_for_depth(self._depth))
                and self._burst_capability[0] is self.identity
                and type(self.profile) is RadioProfile,
                'burst_firmware_capability_changed')
        require(policy_depth(dict(self._burst_policy)) == self._depth,
                'burst_policy_changed')

    def _sample_clock(self):
        request = self.journal.command_request(containment=True)
        sent = self.channel.monotonic()
        self._send(Frame(Message.BURST_CLOCK, self.identity.boot, request), sent + .05)
        reply = self._wait(request, sent + .05)
        received = self.channel.monotonic()
        value = ClockSnapshot.from_frame(reply, boot=self.identity.boot, request=request)
        sent_us, received_us = math.floor(sent * 1_000_000), math.ceil(received * 1_000_000)
        if self._last_clock is not None:
            require(sent_us >= self._last_clock_received_us
                    and value.now_us >= self._last_clock.now_us
                    and (value.arm_request, value.arm_end_us)
                    == (self._last_clock.arm_request, self._last_clock.arm_end_us),
                    'burst_clock_identity_changed')
        self._last_clock, self._last_clock_received_us = value, received_us
        return value, sent_us, received_us

    def arm(self, **parameters):
        require(parameters.get('profile').__class__ is RadioProfile, 'burst_requires_lora')
        if self._depth == 8:
            from .gfsk_profile import radio_is_sf5
            require(radio_is_sf5(parameters['profile']), 'burst_eight_requires_sf5')
        super().arm(**parameters)
        try:
            self._capability()
            self._live()
            clock, sent, received = self._sample_clock()
            end = clock.map_deadline(host_deadline_us=math.floor(self.deadline * 1_000_000),
                sent_us=sent, received_us=received, arm_request=self._arm_request)
            context = context_sha256(parameters['context_digest'],
                                    self.profile.encode(board_id=self.identity.board_id))
            token = self.journal.command_request()
            frame = open_frame(self.identity.boot, token, context, self._arm_request, end,
                               depth=self._depth)
            self._live()
            deadline = self.channel.monotonic() + .2
            self._send(frame, deadline)
            reply = self._wait(token, deadline)
            self._open = OpenBinding.from_frame(reply, boot=self.identity.boot,
                request=token, expected_body=frame.body, depth=self._depth)
            self.assert_drained()
            require(not self.fenced, 'burst_native_fenced')
        except BaseException:
            self._poison('burst_open_failure')
            raise

    @timed(Phase.QUEUE_CLOCK)
    def _new_burst(self):
        require(not self._physical and self._burst_count == 0, 'burst_not_drained')
        clock, sent, received = self._sample_clock()
        self._burst_deadline = clock.map_deadline(
            host_deadline_us=math.floor(self.deadline * 1_000_000),
            sent_us=sent, received_us=received, arm_request=self._arm_request,
            original_cap_us=self._open.deadline_us, burst=True)
        self._burst_clock = clock

    def _metadata(self, logical_id, owner_frame_id, estimate):
        return json.dumps(dict(schema='meshticulum.queued-attempt.v1',
            boot=self.identity.boot.hex(), context=self._open.context_sha256.hex(),
            open_token=self._open.token, original_deadline_us=self._open.deadline_us,
            deadline_us=self._burst_deadline, estimated_airtime_us=estimate, kind='DATA',
            logical_id=logical_id.hex(), owner_frame_id=owner_frame_id),
            sort_keys=True, separators=(',', ':')).encode('ascii')

    @timed(Phase.QUEUE_CALL)
    def queue_data(self, packet, *, logical_id, owner_frame_id=None):
        self._capability()
        require(type(packet) is bytes and 1 <= len(packet) <= 255, 'invalid_transmit')
        require(type(logical_id) is bytes and len(logical_id) == 32 and any(logical_id),
                'invalid_pending_data_identity')
        require(owner_frame_id is None or type(owner_frame_id) is int and owner_frame_id > 0,
                'invalid_pending_owner_frame')
        with phase(self.host_timing, Phase.QUEUE_PUMP):
            self._pump_physical()
        self._live(.05)
        require(self.has_capacity, 'burst_queue_capacity')
        require(logical_id not in self._logical, 'duplicate_pending_data')
        if self._burst_deadline is None:
            try:
                self._new_burst()
            except _FAILURES:
                self._poison('burst_clock_failure')
                raise
        estimate = self._airtime(self.profile, len(packet))
        # All shape/airtime validation precedes durable debt. Replacing only
        # the journal-assigned (already validated) request cannot strand an
        # otherwise invalid descriptor after reservation.
        template = PacketDescriptor.from_ciphertext(1, packet, estimate, self._burst_deadline)
        reservation = math.ceil(estimate * 1.10) + 1000
        metadata = self._metadata(logical_id, owner_frame_id, estimate)
        with phase(self.host_timing, Phase.QUEUE_RESERVE):
            request = self.journal.reserve(packet, reservation, queue_metadata=metadata)
        descriptor = PacketDescriptor(request, template.size, template.digest,
                                      template.estimated_airtime_us, template.deadline_us)
        record = dict(packet=packet, descriptor=descriptor, logical_id=logical_id,
                      reservation=reservation, event=None, event_body=None,
                      host_unknown=False, terminal_reported=False)
        self._physical[request] = record
        self._logical[logical_id] = request
        self._physical_events.append(dict(event='submit', request=request,
            host_mono_us=math.floor(self.channel.monotonic() * 1_000_000),
            token=self._open.token, deadline_us=self._burst_deadline,
            packet_sha256=descriptor.digest.hex(), bytes=descriptor.size,
            estimated_airtime_us=estimate, owner_frame_id=owner_frame_id))
        try:
            try:
                self._live(.05)
            except HardwareError:
                self._settle_queued(request, packet, 'NOT_SENT',
                                           'authority_changed_before_write', None)
                self._forget(request)
                return AttemptResult(request, 'NOT_SENT', reservation, None)
            frame = data_frame(self.identity.boot, self._open.token, descriptor, packet)
            deadline = min(self.deadline, self.channel.monotonic() + .25)
            # The only newly reserved mutation is written before allocating any
            # release command. No batch of IDs can be overtaken by a later ACK.
            with phase(self.host_timing, Phase.QUEUE_SERIAL_WRITE):
                self._send(frame, deadline)
            self._burst_count += 1
            self._waiting += 1
            try:
                with phase(self.host_timing, Phase.QUEUE_ADMISSION_WAIT):
                    return self._admit(record, request, packet, reservation, deadline)
            finally:
                self._waiting -= 1
        except _FAILURES:
            self._poison('ambiguous_burst_write_or_evidence')
            return AttemptResult(request, 'UNKNOWN', reservation, None)
        except BaseException:
            self._poison('interrupted_burst_write')
            raise

    def _admit(self, record, request, packet, reservation, deadline):
        """Original serialized admission path, timed independently of writing."""
        while record['event'] is None:
            reply = self._pop_reply(request)
            if reply is not None:
                require(reply.type is Message.REFUSED and len(reply.body) == 1,
                        'unexpected_burst_admission_reply')
                self._settle_queued(request, packet, 'REFUSED',
                    'firmware_reason_' + str(reply.body[0]), None)
                self._burst_count -= 1
                self._forget(request)
                return AttemptResult(request, 'REFUSED', reservation, None)
            left = deadline - self.channel.monotonic()
            require(left > 0, 'burst_admission_deadline')
            self._ingest(self.channel.read(min(left, .05)))
        require(record['event'].stage is not Stage.UNKNOWN, 'burst_admission_unknown')
        return AttemptResult(request, 'QUEUED', reservation, None)

    @timed(Phase.QUEUE_TERMINAL_SETTLE)
    def _settle_queued(self, *args):
        return self.journal.settle_queued(*args)

    def _event(self, frame):
        event = BurstEvent.from_frame(frame, boot=self.identity.boot)
        record = self._physical.get(event.request)
        if record is None:
            require((event.request, frame.body) in self._released_events,
                    'foreign_burst_event')
            return
        event.check_packet(record['descriptor'], token=self._open.token)
        previous = record['event']
        if previous is not None:
            if record['event_body'] == frame.body:
                return
            require(previous.stage not in TERMINAL, 'burst_terminal_event_changed')
            require(event.at_us >= previous.at_us and event.stage > previous.stage,
                    'burst_event_regressed')
            require(not (previous.stage in (Stage.ISSUED, Stage.STARTED)
                         and event.stage is Stage.NOT_SENT), 'issued_burst_cannot_be_unsent')
        require(event.at_us >= self._burst_clock.now_us, 'burst_event_clock_regressed')
        if event.stage not in TERMINAL:
            require(event.at_us < event.deadline_us, 'burst_activity_after_deadline')
        if event.stage in TERMINAL:
            outcome = ('UNKNOWN' if record['host_unknown'] or event.stage is Stage.UNKNOWN
                       else 'RF_DONE' if event.stage is Stage.RF_DONE else 'NOT_SENT')
            evidence = json.dumps(dict(stage=event.stage.name, event_us=event.at_us,
                driver_estimate_us=event.estimated_airtime_us,
                driver_elapsed_us=event.driver_elapsed_us,
                irq=event.irq_flags, error=event.driver_error), separators=(',', ':'))
            self._settle_queued(event.request, record['packet'], outcome, evidence, frame.body)
            if outcome == 'UNKNOWN':
                self.fenced = True
            if not record['terminal_reported']:
                driver = (TxEvidence(Outcome.RF_DONE if outcome == 'RF_DONE' else Outcome.UNKNOWN,
                    event.estimated_airtime_us, event.driver_elapsed_us,
                    event.irq_flags, event.driver_error) if outcome != 'NOT_SENT' else None)
                self._terminals.append(AttemptResult(event.request, outcome, record['reservation'],
                    driver, 'coprocessor_monotonic' if driver is not None else None))
                record['terminal_reported'] = True
        record['event'], record['event_body'] = event, frame.body
        self._physical_events.append(dict(event='physical', request=event.request,
            host_mono_us=math.floor(self.channel.monotonic() * 1_000_000),
            mcu_event_us=event.at_us, stage=event.stage.name,
            driver_estimated_airtime_us=event.estimated_airtime_us,
            driver_elapsed_us=event.driver_elapsed_us,
            driver_irq_flags=event.irq_flags, driver_error=event.driver_error))

    def _poison(self, reason):
        self.fenced = True
        if self.journal is None:
            return
        self.journal.fence()
        for request, record in self._physical.items():
            if record['event'] is not None and record['event'].stage in TERMINAL:
                continue
            self._settle_queued(request, record['packet'], 'UNKNOWN', reason, None)
            record['host_unknown'] = True
            self._physical_events.append(dict(event='unknown', request=request,
                host_mono_us=math.floor(self.channel.monotonic() * 1_000_000), reason=reason))
            if not record['terminal_reported']:
                self._terminals.append(AttemptResult(request, 'UNKNOWN', record['reservation'], None))
                record['terminal_reported'] = True

    def _forget(self, request):
        record = self._physical.pop(request)
        self._logical.pop(record['logical_id'])
        if not self._physical:
            self._burst_deadline = self._burst_clock = None
            self._burst_count = 0

    def _status(self):
        require(self._open is not None, 'burst_not_open')
        request = self.journal.command_request(containment=True)
        deadline = self.channel.monotonic() + .2
        self._send(Frame(Message.BURST_STATUS, self.identity.boot, request,
                         TOKEN.pack(self._open.token)), deadline)
        status = BurstStatus.from_frame(self._wait(request, deadline),
                                        boot=self.identity.boot, request=request, depth=self._depth)
        require((status.token, status.context_sha256, status.arm_request, status.open_deadline_us)
                == (self._open.token, self._open.context_sha256,
                    self._open.arm_request, self._open.deadline_us), 'burst_status_binding_changed')
        self.last_burst_status = status
        if status.fenced:
            self.fenced = True
            self.journal.fence()
        return status

    def _flush_releases(self):
        require(not self._waiting, 'nested_burst_release')
        for request in tuple(self._physical):
            record = self._physical[request]
            event = record['event']
            if event is None or event.stage not in TERMINAL:
                continue
            if event.stage is Stage.UNKNOWN and self._status().containment_required:
                continue
            with phase(self.host_timing, Phase.RELEASE_REQUEST):
                command = self.journal.command_request(containment=True)
            frame = ack_frame(self.identity.boot, command, event)
            # Release has no RF side effect. Replay the same exact command if
            # its reply was lost after native slot release; never allocate a
            # newer mutation until this serialized transaction is resolved.
            for attempt in range(2):
                deadline = self.channel.monotonic() + .1
                try:
                    with phase(self.host_timing, Phase.RELEASE_ROUNDTRIP):
                        self._send(frame, deadline)
                        reply = self._wait(command, deadline)
                    break
                except (OSError, HardwareError) as error:
                    if attempt or (isinstance(error, HardwareError)
                                   and str(error) != 'serial_reply_deadline'):
                        raise
            require(reply.type is Message.BURST_ACK_REPLY and reply.body == frame.body,
                    'burst_release_not_confirmed')
            for duplicate in self._routed.pop(command, ()):
                require(duplicate == reply, 'contradictory_burst_release_reply')
            self._release_replies.append(reply)
            with phase(self.host_timing, Phase.RELEASE_COMMIT):
                self.journal.release_queued(request, record['event_body'])
            self._released_events.append((request, record['event_body']))
            self._physical_events.append(dict(event='release', request=request,
                host_mono_us=math.floor(self.channel.monotonic() * 1_000_000),
                mcu_event_us=event.at_us, stage=event.stage.name))
            self._forget(request)

    def _pump_physical(self):
        try:
            self._ingest(self.channel.read(0))
            if not self._waiting and self._open is not None:
                self._flush_releases()
        except _FAILURES:
            self._poison('burst_physical_poll_failure')
            raise

    def poll_physical(self):
        self._pump_physical()
        results = list(self._terminals)
        self._terminals.clear()
        return results

    @property
    def has_buffered_activity(self):
        # Command replies belong to their serialized _wait(), not this pump.
        # A terminal still needs normal collection/release; it is not capacity.
        return (super().has_buffered_activity or bool(self._terminals)
                or any(record['event'] is not None and record['event'].stage in TERMINAL
                       and not record.get('host_unknown', False)
                       for record in self._physical.values()))

    def receive(self, timeout=0.0):
        self._live()
        self._pump_physical()
        return super().receive(timeout=timeout)

    def _cancel(self):
        if self._cancelled or self._open is None:
            return
        # A missing CANCEL reply can never make this owner sendable again.
        self.fenced = True
        self.journal.fence()
        request = self.journal.command_request(containment=True)
        deadline = self.channel.monotonic() + .2
        self._send(Frame(Message.BURST_CANCEL, self.identity.boot, request,
                         TOKEN.pack(self._open.token)), deadline)
        reply = self._wait(request, deadline)
        require(reply.type is Message.BURST_CANCEL_REPLY and len(reply.body) == CANCEL.size,
                'burst_cancel_not_confirmed')
        token, fenced, containment = CANCEL.unpack(reply.body)
        require(token == self._open.token and fenced in (0, 1) and containment in (0, 1),
                'burst_cancel_binding_changed')
        self._cancelled = True
        if fenced:
            self.fenced = True
            self.journal.fence()

    def cancel_pending(self):
        """Cleanup-only exact-owner cancellation; never checks/grants RF live."""
        require(self.identity is not None and self.journal is not None, 'missing_owner')
        if self._physical:
            self._cancel()

    def drain_physical(self, timeout=2.5):
        require(type(timeout) in (int, float) and math.isfinite(timeout)
                and 0 <= timeout <= 3, 'invalid_burst_drain_timeout')
        deadline = self.channel.monotonic() + timeout
        results = []
        try:
            if self.fenced and self._physical:
                self._cancel()
            results.extend(self.poll_physical())
            while self._physical:
                results.extend(self.poll_physical())
                if not self._physical:
                    break
                left = deadline - self.channel.monotonic()
                require(left > 0, 'burst_drain_deadline')
                for request, record in tuple(self._physical.items()):
                    if record['event'] is None or record['event'].stage not in TERMINAL:
                        self._send(query_frame(self.identity.boot, self._open.token, request), deadline)
                self._ingest(self.channel.read(min(left, .05)))
            self.assert_drained()
        except _FAILURES:
            self._terminals.extendleft(reversed(results))
            self._poison('burst_drain_failure')
            raise
        return results

    def assert_drained(self):
        require(not self._physical, 'burst_physical_pending')
        if self._open is not None:
            status = self._status()
            require(not (status.pending or status.retained or status.transmitting
                         or status.containment_required), 'burst_native_not_drained')
        return True

    def transmit(self, packet):
        if self._physical:
            self._terminals.extend(self.drain_physical())
        if self._open is not None:
            # Legacy control cannot erase a delegated DATA reverse-listen
            # window. Wait under its original authority, then let the normal
            # synchronous path repeat the final live/lease checks.
            status = self._status()
            deadline = min(self.deadline, self.channel.monotonic() + .3)
            while status.next_start_us > status.now_us:
                self._live()
                left = deadline - self.channel.monotonic()
                require(left > 0, 'burst_reverse_listen_deadline')
                wait = ((status.next_start_us - status.now_us) / MIN_MCU_RATE_PPM
                        + CLOCK_MARGIN_US / 1_000_000)
                self._ingest(self.channel.read(min(left, wait, .05)))
                self._pump_physical()
                status = self._status()
        result = super().transmit(packet)
        self._pump_physical()
        return result

    def disarm(self):
        failure = None
        try:
            if self._open is not None:
                self.drain_physical()
        except BaseException as error:
            failure = error
        try:
            super().disarm()
        finally:
            self._open = None
        if failure is not None:
            raise failure
