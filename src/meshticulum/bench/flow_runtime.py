# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""One authenticated MT05 flow composed with real worker and authority adapters.

No shared StartupGrant is fabricated: the receiver's first authenticated ACK
establishes credit. The caller supplies a held, provisioned authority, its exact
contract, cipher, exclusive worker, durable attempt journal and host clocks.
This module opens no IP data channel and does not import personal SAGE code.
"""
from collections import Counter, deque
import hashlib
import importlib
import json
import math
from pathlib import Path
import struct
import sys
import types

from ..contracts.carrier import CarrierProfile
from ..contracts.errors import ContractError
from ..contracts.identity import FlowKey
from ..contracts.negotiation import CommitOutcome, NegotiatedContract
from ..scheduler.budget import CounterSample, Ledger
from ..scheduler.scheduler import FrameRole, Scheduler, SchedulerPolicy
from .wire import FrameKind, WireError, require
from .serial_protocol import TxEvidence
from .host_timing import Phase, check_timing, phase, timed
from .gfsk_profile import radio_airtime_us,radio_is_fast,radio_is_sf5
from .file_capability import admitted_object_limit, admitted_frame_limit
from .turn_end import admitted as turn_end_admitted
from .burst_protocol import policy_depth, policy_flow_window, policy_repair_fill, policy_terminal_retry, policy_scalar_fill_pair, policy_repeat_retry, policy_repair_train, policy_train_frames


def _flow_modules():
    # Canonical package identities; accepted flow/port source remains unchanged.
    return (importlib.import_module('meshticulum.flow.flow'),
            importlib.import_module('meshticulum.flow.flow_port'))


class BenchFlow:
    """A finite one-way transfer; ACKs travel through the same admitted worker.

    Profile costs are conservative source estimates. The scheduler additionally
    observes the durable worker's charged reservations, never a made-up RF
    counter. Overlap remains charged twice by the frozen ledger. Actual TX_DONE
    evidence stays in the worker journal, separate from peer receipt. Per-TX
    diagnostics retain driver elapsed time and clock basis beside host worker
    call time; neither is a refund or peer-delivery assertion.
    """
    def __init__(self, *, authority, worker, cipher, journal, flow_key, contract,
                 profile, ledger, scheduler_epoch, broker_epoch, sends, clock,
                 host_timing=None):
        self.host_timing = check_timing(host_timing)
        require(type(flow_key) is FlowKey and type(contract) is NegotiatedContract
                and type(profile) is CarrierProfile and type(ledger) is Ledger,
                'invalid_flow_composition')
        require(cipher.grant.session == flow_key.session and contract.route.session == flow_key.session,
                'flow_session_mismatch')
        require(profile.payload_capacity == cipher.grant.max_plaintext == 208,
                'unsupported_flow_frame_capacity')
        require(type(sends) is bool and callable(clock), 'invalid_flow_direction')
        operation_limits = getattr(authority, 'operation_limits', None)
        require(operation_limits is None
                or journal.max_attempts <= operation_limits.attempts
                and journal.max_airtime_us <= operation_limits.estimated_airtime_us,
                'bench_journal_limits_exceed_operation_grant')
        self.authority, self.worker, self.cipher = authority, worker, cipher
        self.journal, self.clock, self.ledger = journal, clock, ledger
        self.sends = sends
        policy = getattr(authority, 'data_burst_policy', None)
        self.data_burst = sends and policy is not None
        self._selective_repair_fill = (self.data_burst and policy_repair_fill(policy))
        self._selective_terminal_retry = (self.data_burst and policy_terminal_retry(policy))
        self._scalar_fill_pair = self.data_burst and policy_scalar_fill_pair(policy)
        self._selective_repeat_retry = self.data_burst and policy_repeat_retry(policy)
        self._selective_repair_train = self.data_burst and policy_repair_train(policy)
        self._repair_train_active = self._repair_train_offer = False
        self._repair_train_mode = None
        self._repair_train_domains = set()
        self._last_queued_domain = None
        self._pace_selective_repeats = False
        self._paced_fast_offers, self._repeat_not_before = {}, {}
        self._scalar_fill_extra = False
        self._repair_fill_active = False
        self._physical_data, self._pending_data_domains = {}, {}
        if self.data_burst:
            from .burst_worker import BurstSerialWorker
            require(isinstance(worker, BurstSerialWorker)
                    and worker.data_burst_policy == policy, 'missing_admitted_burst_worker')
            worker.assert_drained()
        self._receipt_payload = self._receipt_link = self._receipt_ready = None
        self._receipt_dispatching = self._receipt_requested = self._receipt_attempted = False
        self.receipt_confirmed = False
        self.receipt_confirmation_outcome = None
        self._last_ack_snapshot = None
        self._ack_persistence_streak = 0
        self._ack_persistence_due_us = 0
        self._final_ack_repeat_due_us = None
        self._final_ack_repeat_requested = False
        self._final_ack_repeat_satisfied = False
        self.events = deque(maxlen=512)
        self.counts = Counter()
        self.dispatch_budget_failure = None
        self.received = bytearray()
        self.last_activity_us = self.clock()[1]
        flow, port = _flow_modules()
        self.next_send_us = 0
        self._refused_turn_until_us = 0
        self._train_progress_receipt = None
        self.receive_gap_us = math.ceil(radio_airtime_us(authority.profile,
            flow.ACK_BYTES + 47) * 1.10) + 60000
        self._ack_reservation_us = math.ceil(radio_airtime_us(authority.profile,
            flow.ACK_BYTES + 47) * 1.10) + 1000
        limits = contract.limits
        self.maximum_bytes = min(limits.max_queued_bytes, limits.max_unconsumed_bytes)
        require(self.maximum_bytes <= admitted_object_limit(authority.plan), 'bench_transfer_too_large')
        # Owner credit uses the admitted frame bound (208). Derive the DATA
        # overhead from the actual MT3 wire encoder, not older adapter prose:
        # the reviewed encoding uses fifteen bytes, leaving 193 useful bytes.
        selected_depth = policy_depth(policy) if policy is not None else 4
        selected_window = policy_flow_window(policy) if policy is not None else 4
        selected_train = policy_train_frames(policy) if policy is not None else selected_depth
        self._selective_stall_retry = (self._selective_repair_train
                                       and selected_train in (12, 24, 32))
        require(selected_depth == 4 or radio_is_sf5(authority.profile),
                'burst_eight_requires_sf5')
        if selected_window != selected_depth:
            # The wider logical window is an exact original admission, never
            # an inferred upgrade or a silent clamp of a smaller contract.
            require(contract is authority.contract and limits.max_payload_bytes == 208
                    and limits.window_bytes == 208 * selected_window
                    and limits.max_reassembly_frames == selected_window,
                    'burst_flow_window_contract_mismatch')
        window = min(selected_window, limits.window_bytes // limits.max_payload_bytes,
                     limits.max_reassembly_frames)
        require(window >= 1, 'bench_window_too_small')
        # Logical credit/reordering may span several physical trains. ACK
        # coalescing and each reverse turn use the separately admitted train.
        self.burst_frames = min(selected_train, window) if radio_is_fast(authority.profile) else 1
        fast_sf5 = radio_is_sf5(authority.profile)
        self._sf5_ack_recovery_eligible = not sends and policy is not None and fast_sf5
        self.burst_quiet_us = math.ceil(profile.forward_seconds * 1000000) + 60000
        if self.burst_frames > 1:
            # USB/driver completion can span more than one PHY packet. Keep
            # fast SF5 quiet at250 ms when its retry floor grows: retry and
            # inter-frame silence are different clocks. Other profiles retain
            # their negotiated control window.
            source_rto = getattr(authority, 'source_initial_rto_seconds', profile.initial_rto_seconds)
            quiet_rto = min(source_rto, .5) if fast_sf5 else source_rto
            self.burst_quiet_us = max(self.burst_quiet_us, math.ceil(quiet_rto * 500000))
        self.burst_max_hold_us = math.ceil(getattr(authority, 'source_initial_rto_seconds',
                                                  profile.initial_rto_seconds) * 1000000)
        if fast_sf5:
            # Scale the legacy coalescing hold only up to the original native
            # two-second bound. Quiet or total-hold expiry can end a sparse or
            # slow train; a full train earns its ACK immediately in step().
            self.burst_max_hold_us = min(self.burst_frames * self.burst_quiet_us, 2_000_000)
        self.burst_sent = 0
        self._turn_end_enabled = turn_end_admitted(authority.plan)
        self._turn_end_dispatch = None
        self._turn_end_highest_received = 0
        require(not self._turn_end_enabled or (self.burst_frames == 32
                and cipher._turn_end_enabled is True and cipher._turn_end_binding() is True
                and (not sends or self.data_burst)), 'turn_end_runtime_not_admitted')
        self.burst_received = 0
        self.burst_started_us = 0
        self.ack_due_us = 0
        quantum = cipher.grant.max_plaintext - flow.MIN_FRAME_BYTES
        require(flow.data_frame_fits(quantum, profile.payload_capacity), 'invalid_inner_frame_bound')
        self.scheduler = Scheduler(local_peer=authority.local_peer, ledger=ledger,
            epoch=scheduler_epoch, broker_epoch=broker_epoch,
            policy=SchedulerPolicy(control_rate_us_per_second=1000000, control_burst_us=6000000))
        class RetainedOwner(port.MeshticulumFlowOwner):
            # The frozen owner remembers settled Python object IDs. Retain the
            # corresponding live objects for this finite run so allocator ID
            # reuse cannot be mistaken for a duplicate completion. The author's
            # source remains unchanged; this compatibility bound is explicit.
            def offer(owner, frame, *, kind):
                # Only this opt-in adapter record extends the frozen owner's
                # strict MT05 parser. Its existing CONTROL candidate, handle,
                # leases and settlement path account the exact bytes unchanged.
                if self._receipt_payload is not None and frame == self._receipt_payload:
                    if (kind != port.CONTROL_KIND or not self._receipt_dispatching
                            or not self.sends or not self.flow.complete
                            or len(frame) > min(owner.bound, owner.contract.limits.max_control_bytes)):
                        raise ContractError('receipt_confirmation_not_sendable')
                    owner._armed = owner._candidate_for({}, frame, FrameRole.CONTROL, port.CONTROL_KIND)
                    return FrameRole.CONTROL
                return super().offer(frame, kind=kind)

            def prepare(owner, *args, **kwargs):
                if len(owner.retained) >= 1024:
                    raise ContractError('bench_handle_retention_limit')
                handle = super().prepare(*args, **kwargs)
                if handle is not None:
                    owner.retained.append(handle)
                return handle
        self.owner = RetainedOwner(owner_id=authority.owner_id,
            flow=flow_key, contract=contract, profile=profile)
        self.owner.retained = []
        epoch, mono = self.clock()
        self.scheduler.open_flow(flow_key, contract, profile, now_us=mono,
                                 now_epoch=epoch / 1000000, attempt_owner=self.owner)
        class PhysicalLink(port.FlowPortLink):
            def queued_bytes(link, sender):
                queued = super().queued_bytes(sender)
                if self.data_burst:
                    delay = self.worker.pending_airtime_us
                    require(type(delay) is int and delay >= 0, 'invalid_physical_queue_bound')
                    queued += math.ceil(delay * link.profile(sender).capacity_bytes_per_second / 1000000)
                return queued
        self._link_class = PhysicalLink
        self.link = PhysicalLink(scheduler=self.scheduler, owner=self.owner,
            flow=flow_key, authorize=authority.service_lease, broker=authority.broker_lease,
            carrier=self._send, monotonic_us=lambda: self.clock()[1],
            epoch_clock=lambda: self.clock()[0] / 1000000)
        self.flow = flow.ReliableFlow('a' if sends else 'b', self.link,
            flow.FlowLimits(frame_payload_bytes=quantum, window_frames=window,
                initial_credit_frames=window, max_reassembly_frames=window,
                max_delivered_bytes=self.maximum_bytes, max_queued_bytes=self.maximum_bytes,
                max_total_frames=min(admitted_frame_limit(authority.plan, public32=authority.public32), limits.max_total_frames),
                max_retries=limits.max_retry_attempts,
                initial_rto_s=profile.initial_rto_seconds,
                min_rto_s=profile.min_rto_seconds, max_rto_s=profile.max_rto_seconds,
                ack_delay_s=0.01, persist_interval_s=max(.5,
                    2 * profile.forward_seconds + profile.reverse_control_seconds + .15),
                # Separate endpoint processes can take more than four seconds
                # to reach first DATA. Keep startup failure bounded at eight
                # seconds instead of losing a valid transfer to host startup.
                max_persist_acks=16,
                selective_fast_retry=self.data_burst and fast_sf5,
                selective_terminal_retry=self._selective_terminal_retry,
                selective_stall_retry=self._selective_stall_retry,
                selective_repeat_retry=self._selective_repeat_retry),
            flow_id=flow_key.flow_id, generation=1, sends=sends, receive=not sends,
            startup=None)
        self.sender_control_hold_us = (max(self.burst_quiet_us,
            math.ceil(self.flow.limits.persist_interval_s * 1000000)) + self.receive_gap_us
            if self.burst_frames > 1 else self.receive_gap_us)
        if fast_sf5 and self.burst_frames > 1:
            # A partial burst is first acknowledged after quiet. If that ACK
            # is lost, leave room for one existing persistence interval too.
            # Authenticated progress still releases this fallback early.
            self.sender_control_hold_us = (self.burst_quiet_us
                + math.ceil(self.flow.limits.persist_interval_s * 1000000)
                + max(self.receive_gap_us, self.burst_quiet_us))
        self.receiver_ack_quiet_us = self.burst_quiet_us
        self._receiver_quiet150_eligible = fast_sf5 and selected_train in (24, 32) and self.burst_frames == selected_train
        self._receiver_quiet_override = False
        self.parse_frame, self.ack_kind = flow.parse_frame, flow.KIND_ACK
        self.closed = False
        self.update_counter()

    @timed(Phase.FLOW_ACCOUNTING)
    def update_counter(self):
        state = self.journal.snapshot()
        require(not state['fenced'], 'worker_run_fenced')
        now = self.clock()[1]
        self.ledger.observe(CounterSample(now, state['reserved_estimated_airtime_us'],
                                         self.ledger.epoch), now)

    def _send(self, frame):
        if self._turn_end_enabled and (self._turn_end_dispatch is not None
                or self.sends and self.worker._turn_end_seal is not None):
            if self.sends:
                self.worker._turn_end_failed = True
            require(False, 'turn_end_admission_closed')
        # The authority checks exact object identity of both currently held
        # leases and the immutable request, as well as the OS radio owner.
        self.authority.require_dispatch(frame)
        if self._receipt_payload is not None and frame.payload == self._receipt_payload:
            require(self._receipt_dispatching and self.sends and self.flow.complete
                    and not self._receipt_attempted and frame.request.role is FrameRole.CONTROL,
                    'invalid_receipt_confirmation_transmit')
            self._receipt_attempted = True
            kind = FrameKind.CONTROL
        else:
            parsed = self.parse_frame(frame.payload)
            require(parsed is not None, 'invalid_inner_transmit')
            kind = FrameKind.ACK if parsed['kind'] == self.ack_kind else FrameKind.DATA
            require((frame.request.role is FrameRole.CONTROL) == (kind is FrameKind.ACK),
                    'inner_lane_mismatch')
        if self._scalar_fill_extra and (kind is not FrameKind.DATA
                or parsed['seq'] != self.flow._next_seq):
            # Defensive carrier boundary: the extra opportunity is fresh DATA
            # only. Any prepared framework reservation stays charged normally;
            # no extra cipher nonce or physical attempt is created here.
            self.counts['scalar_fill_nonfresh_refused'] += 1
            self.owner.settle_sending()
            return CommitOutcome.REFUSED_CARRIER
        if self._repair_train_offer and kind is not FrameKind.DATA:
            self.counts['repair_train_noneligible_refused'] += 1
            self._hold_repair_reverse_turn()
            return CommitOutcome.REFUSED_CARRIER
        ack_diagnostics = {}
        if kind is FrameKind.ACK:
            snapshot = (parsed['ack_seq'], parsed['sack'], parsed['credit'], parsed['grant_revision'])
            ack_snapshot = ('startup' if self._last_ack_snapshot is None and not parsed['ack_seq']
                            and not parsed['sack'] else 'unchanged'
                            if snapshot == self._last_ack_snapshot else 'new')
            ack_diagnostics = dict(ack_snapshot=ack_snapshot,
                ack_seq=parsed['ack_seq'], ack_sack=parsed['sack'], ack_credit=parsed['credit'],
                ack_revision=parsed['grant_revision'], ack_burst_received=self.burst_received,
                ack_wait_reason=('startup' if self.flow.startup_pending else 'complete'
                    if self.flow.complete else 'full_train' if self.burst_received >= self.burst_frames
                    else 'quiet' if self.burst_received else 'persistence'))
        domain = logical_id = plaintext_digest = None
        if self.data_burst and kind is FrameKind.DATA:
            require(self.worker.data_burst_policy == self.authority.data_burst_policy,
                    'burst_policy_changed')
            domain = (frame.request.flow, parsed['flow_id'], parsed['generation'], parsed['seq'])
            plaintext_digest = hashlib.sha256(frame.payload).digest()
            pending = self._pending_data_domains.get(domain)
            if pending is not None:
                require(pending[0] == plaintext_digest, 'pending_data_plaintext_changed')
                self.counts['pending_DATA_refused'] += 1
                self.owner.settle_sending()
                return CommitOutcome.REFUSED_CARRIER
            if self._repair_train_offer:
                # The explicit extra offer must remain a distinct existing
                # flight. Check again at the carrier boundary, before sealing
                # or reserving a physical attempt; preparation may take time.
                now_us = self.clock()[1]
                require(type(now_us) is int and now_us / 1000000 >= self.link.now,
                        'repair_train_clock_regressed')
                if (not self._repair_train_active or domain in self._repair_train_domains
                        or parsed['seq'] not in self.flow._in_flight
                        or any(now_us / 1000000 - p.sent_at >= p.rto
                               for p in self.flow._in_flight.values())):
                    self.counts['repair_train_noneligible_refused'] += 1
                    self._hold_repair_reverse_turn()
                    return CommitOutcome.REFUSED_CARRIER
            logical_id = hashlib.sha256(b'meshticulum.pending-data.v1\0'
                + self.cipher.grant.digest + struct.pack('!QQQ', *domain[1:])
                + plaintext_digest).digest()
        elif self.data_burst:
            # ACK/control keep their synchronous canonical-handover path.
            self._collect_physical(self.worker.drain_physical())
        seal = None
        if (self._turn_end_enabled and self.data_burst and kind is FrameKind.DATA
                and self.worker.train_identity is not None and self.worker.has_capacity):
            fresh = (parsed['flow_id'] == self.flow.flow_id and parsed['generation'] == self.flow.generation
                     and parsed['seq'] == self.flow._next_seq
                     and parsed['payload'] == bytes(self.flow._outbound[:self.flow.limits.frame_payload_bytes]))
            allowance = min(self.flow.limits.window_frames, max(0, self.flow._peer_credit))
            tail = (self.worker.train_identity[3] == 31 or fresh and (
                self.flow.queued_bytes() <= len(parsed['payload'])
                or self.flow.in_flight() + 1 >= allowance))
            if tail:
                seal = self.worker.reserve_turn_end(logical_id=logical_id,
                    owner_frame_id=frame.request.frame_id, plaintext=frame.payload)
        if seal is None:
            with phase(self.host_timing, Phase.CIPHER_SEAL):
                packet = self.cipher.seal(frame.payload, kind, authorize=self.authority.live)
        else:
            bound = (self.authority, self.cipher, self.flow, frame, frame.request, frame.payload)
            self._turn_end_dispatch = seal
            try:
                with phase(self.host_timing, Phase.CIPHER_SEAL):
                    packet = self.cipher.seal(frame.payload, FrameKind.DATA_TURN_END,
                                              authorize=self.authority.live)
                self.worker.validate_turn_end(seal)
                require((self.authority is bound[0] and self.cipher is bound[1]
                         and self.flow is bound[2] and frame is bound[3]
                         and frame.request is bound[4] and frame.payload is bound[5]
                         and self.worker is seal.worker and self._turn_end_enabled is True
                         and self.cipher._turn_end_enabled is True
                         and self.cipher._turn_end_binding() is True
                         and turn_end_admitted(self.authority.plan)),
                        'turn_end_candidate_changed')
                if fresh:
                    require(parsed['seq'] == self.flow._next_seq
                            and parsed['payload'] == bytes(self.flow._outbound[:self.flow.limits.frame_payload_bytes]),
                            'turn_end_fresh_candidate_changed')
                self.worker.bind_turn_end_packet(seal, packet)
            except BaseException:
                seal.worker._turn_end_failed = True
                raise
        worker_started_us = self.clock()[1]
        if seal is not None:
            require(self.worker is seal.worker and self.authority is bound[0]
                    and self.cipher is bound[1] and self.flow is bound[2]
                    and self._turn_end_enabled is True and self.cipher._turn_end_enabled is True,
                    'turn_end_dispatch_binding_changed')
            seal.worker.validate_turn_end(seal)
        try:
            result = (self.worker.queue_turn_end(seal, packet) if seal is not None else
                  self.worker.queue_data(packet, logical_id=logical_id,
                                       owner_frame_id=frame.request.frame_id)
                  if self.data_burst and kind is FrameKind.DATA else self.worker.transmit(packet))
        finally:
            self._turn_end_dispatch = None
        worker_finished_us = self.clock()[1]
        if seal is not None:
            require(self.worker is seal.worker and self.authority is bound[0]
                    and self.cipher is bound[1] and self.flow is bound[2]
                    and self._turn_end_enabled is True and self.cipher._turn_end_enabled is True,
                    'turn_end_completion_binding_changed')
            seal.worker.validate_turn_end(seal, increment=1)
        require(type(worker_started_us) is int and type(worker_finished_us) is int
                and worker_finished_us >= worker_started_us, 'invalid_worker_timing')
        if result.outcome == 'QUEUED':
            if self.burst_frames == 32:
                self._train_progress_receipt = None
            require(self.data_burst and kind is FrameKind.DATA and result.driver is None,
                    'invalid_queue_acceptance')
            self._pending_data_domains[domain] = (plaintext_digest, result.request)
            if self._selective_repair_train:
                self._last_queued_domain = domain
                if self._repair_train_offer:
                    self._repair_train_domains.add(domain)
                    self.counts['repair_train_extra'] += 1
                    if self._repair_train_mode == 'stall':
                        self.counts['stall_repair_extra'] += 1
            self._physical_data[result.request] = dict(domain=domain,
                packet_sha256=hashlib.sha256(packet).hexdigest(), bytes=len(packet),
                queue_submit_mono_us=worker_started_us, queue_accept_mono_us=worker_finished_us,
                queue_accept_elapsed_us=worker_finished_us - worker_started_us,
                owner_frame_id=frame.request.frame_id, carrier_outcome='QUEUED')
            if seal is not None:
                self._physical_data[result.request]['wire_kind'] = 'DATA_TURN_END'
            self.counts['tx_QUEUED'] += 1
            if seal is not None:
                self.counts['tx_DATA_turn_end'] += 1
            self.events.append(dict(event='tx_queued', mono_us=worker_finished_us,
                outer_kind='DATA', request=result.request,
                **{key: value for key, value in self._physical_data[result.request].items()
                   if key != 'domain'}))
            self.last_activity_us = worker_finished_us
            self.burst_sent += 1
            self.next_send_us = (0 if self.burst_frames > 1 else worker_finished_us
                                 + self.worker.pending_airtime_us + self.receive_gap_us)
            if self.burst_sent >= self.burst_frames:
                self.next_send_us = (worker_finished_us + self.worker.pending_airtime_us
                                     + self.sender_control_hold_us)
                self.burst_sent = 0
                self._repair_fill_active = False
                self._repair_train_active = False
                self._repair_train_domains.clear()
                self.owner.settle_sending()
            elif parsed['last'] and self._repair_fill_active:
                # A partial final train still owes its reverse turn. Another
                # flight can become due inside this accepted handover, after
                # the pre-pump expiry check, so clearing the flag alone would
                # allow that retry to join a fresh-DATA-only fill.
                self._hold_repair_reverse_turn()
            return CommitOutcome.ACCEPTED
        if self.data_burst and kind is FrameKind.DATA and result.outcome == 'UNKNOWN':
            # Physical proof may already be buffered even though queue handover
            # itself failed. Keep that request without inventing an accepted
            # flow handover or losing the separately fenced physical record.
            self._physical_data[result.request] = dict(domain=None,
                packet_sha256=hashlib.sha256(packet).hexdigest(), bytes=len(packet),
                queue_submit_mono_us=worker_started_us, queue_accept_mono_us=None,
                queue_accept_elapsed_us=None, owner_frame_id=frame.request.frame_id,
                carrier_outcome='UNKNOWN')
            self.counts['queue_UNKNOWN'] += 1
            self.events.append(dict(event='queue_unknown', mono_us=worker_finished_us,
                                    request=result.request, outer_kind='DATA'))
            self.owner.settle_sending()
            return CommitOutcome.UNKNOWN
        driver = result.driver
        require(driver is None or type(driver) is TxEvidence, 'invalid_driver_timing')
        basis = result.driver_elapsed_clock_basis if driver is not None else None
        require(driver is None or basis in ('coprocessor_monotonic', 'host_monotonic', 'model_monotonic'),
                'invalid_driver_clock_basis')
        self.counts['tx_' + result.outcome] += 1
        self.events.append({'event': 'tx', 'mono_us': worker_finished_us,
            'outer_kind': kind.name, 'packet_sha256': hashlib.sha256(packet).hexdigest(),
            'bytes': len(packet), 'request': result.request, 'outcome': result.outcome,
            'worker_call_elapsed_us': worker_finished_us - worker_started_us,
            'driver_estimated_airtime_us': driver.estimated_airtime_us if driver is not None else None,
            'driver_elapsed_us': driver.driver_elapsed_us if driver is not None else None,
            'driver_elapsed_clock_basis': basis,
            'driver_irq_flags': driver.irq_flags if driver is not None else None,
            'driver_error': driver.driver_error if driver is not None else None,
            **ack_diagnostics})
        self.last_activity_us = self.clock()[1]
        if kind is FrameKind.CONTROL:
            self.receipt_confirmation_outcome = result.outcome
            if result.outcome == 'RF_DONE':
                return CommitOutcome.ACCEPTED
            if result.outcome in ('REFUSED', 'NOT_SENT'):
                if self.burst_frames == 32:
                    self._train_progress_receipt = None
                return CommitOutcome.REFUSED_CARRIER
            self.owner.settle_sending()
            return CommitOutcome.UNKNOWN
        # A single half-duplex radio needs time to hear the reverse ACK before
        # another DATA start. This is an explicit initial bench pacing policy,
        # not a measured optimum or a permanent protocol stop-and-wait rule.
        self.next_send_us = (0 if self.burst_frames > 1
                             else self.last_activity_us + self.receive_gap_us)
        if result.outcome == 'RF_DONE':
            if kind is FrameKind.ACK:
                self.counts['tx_ACK_RF_DONE_' + ack_snapshot] += 1
                self._last_ack_snapshot = snapshot
                # Only idle persistence backs off. New receive/credit work or
                # a queued canonical ACK still follows the frozen flow's path.
                if ack_snapshot != 'unchanged' or self.flow._ack_dirty or self.flow._ack_pending is not None:
                    self._ack_persistence_streak = 0
                else:
                    self._ack_persistence_streak = min(2, self._ack_persistence_streak + 1)
                self._ack_persistence_due_us = worker_finished_us + math.ceil(
                    self.flow.limits.persist_interval_s * (1 << self._ack_persistence_streak) * 1000000)
                if (self._sf5_ack_recovery_eligible and self.receipt_confirmation_enabled
                        and self.flow.complete and parsed['ack_seq'] == self.flow._expected
                        and parsed['sack'] == 0):
                    if self._final_ack_repeat_due_us is not None:
                        self._final_ack_repeat_due_us = None
                        self._final_ack_repeat_satisfied = True
                        self.counts['final_ACK_reannouncement_satisfied'] += 1
                    elif not self._final_ack_repeat_satisfied and not self._final_ack_repeat_requested:
                        self._final_ack_repeat_due_us = worker_finished_us + math.ceil(
                            self.flow.limits.persist_interval_s * 1000000)
                self.burst_received = 0
                self.burst_started_us = 0
                self.ack_due_us = 0
            elif self.burst_frames > 1:
                self.burst_sent += 1
                if self.burst_sent >= self.burst_frames:
                    # Yield through the existing receiver persistence interval
                    # plus reverse-ACK/turnaround margin, so a lost first ACK
                    # can be repeated before DATA resumes. A real progress ACK
                    # releases this hold early;
                    # the frozen retry clock and credit remain unchanged.
                    self.next_send_us = self.last_activity_us + self.sender_control_hold_us
                    self.burst_sent = 0
                    # One pump can expire several flights. Fence immediately,
                    # before another retry in that same pump can use the radio.
                    self.owner.settle_sending()
            return CommitOutcome.ACCEPTED
        if result.outcome in ('REFUSED', 'NOT_SENT'):
            if self.burst_frames == 32:
                self._train_progress_receipt = None
            if (self.data_burst and kind is FrameKind.DATA and self.burst_frames in (24, 32)
                    and self.worker.train_admission_refused):
                # v10's dynamic native fit may end a short train. Keep every
                # accepted logical flight, collect its exact physical outcome,
                # and release all slots before a future CLOCK is possible.
                # Refusal remains charged; the frozen flow owns retransmission.
                self._collect_physical(self.worker.drain_physical())
                self.worker.retire_refused_train()
                self.counts['dynamic_train_refused_turn'] += 1
                previous_hold = self.next_send_us
                self._hold_repair_reverse_turn()
                if self.burst_frames == 32:
                    self.next_send_us = max(previous_hold, self.next_send_us)
                if self.burst_frames == 32 and self.worker.drained_train_pending:
                    self.worker.retire_drained_train()
                self._refused_turn_until_us = self.next_send_us
            return CommitOutcome.REFUSED_CARRIER
        self.owner.settle_sending()
        return CommitOutcome.UNKNOWN

    def _collect_physical(self, results):
        for result in results:
            require(type(result.request) is int and result.outcome in ('RF_DONE', 'NOT_SENT', 'UNKNOWN'),
                    'invalid_physical_result')
            info = self._physical_data.pop(result.request, None)
            if info is None:
                # A queue write already returned UNKNOWN to the carrier. It
                # has no accepted DATA handover, but its journal still drains.
                require(self.worker.fenced and result.outcome == 'UNKNOWN',
                        'foreign_physical_result')
                continue
            domain = info.pop('domain')
            if domain is not None:
                held = self._pending_data_domains.get(domain)
                require(held is not None and held[1] == result.request, 'physical_data_identity_changed')
                del self._pending_data_domains[domain]
            else:
                require(self.worker.fenced and info['carrier_outcome'] == 'UNKNOWN',
                        'unaccepted_physical_result')
            driver = result.driver
            require(driver is None or type(driver) is TxEvidence, 'invalid_driver_timing')
            self.counts['tx_' + result.outcome] += 1
            self.events.append(dict(event='tx', mono_us=self.clock()[1], outer_kind='DATA',
                request=result.request, outcome=result.outcome, **info,
                worker_call_elapsed_us=None, physical_result_mono_us=self.clock()[1],
                driver_estimated_airtime_us=driver.estimated_airtime_us if driver else None,
                driver_elapsed_us=driver.driver_elapsed_us if driver else None,
                driver_elapsed_clock_basis=result.driver_elapsed_clock_basis,
                driver_irq_flags=driver.irq_flags if driver else None,
                driver_error=driver.driver_error if driver else None))

    def _poll_physical(self):
        if self.data_burst:
            self._collect_physical(self.worker.poll_physical())
            if self.burst_frames == 32 and self.worker.drained_train_pending and not self.worker.train_admission_refused:
                # v11 cannot mint another deadline from an empty retained map.
                # Establish the existing reverse hold before retiring this turn.
                previous_hold = self.next_send_us
                progress_permission_unchanged = (self.worker.train_identity is not None
                    and self._train_progress_receipt == self.worker.train_identity)
                self._hold_repair_reverse_turn()
                self.next_send_us = max(previous_hold, self.next_send_us)
                self.worker.retire_drained_train()
                if progress_permission_unchanged:
                    # Authenticated progress already released this wait while
                    # this local turn binding was unchanged. This does not
                    # establish that the ACK covers this turn's DATA.
                    # Retire first; never weaken a prior hold or refusal floor.
                    self.next_send_us = max(previous_hold, self._refused_turn_until_us)
                self._train_progress_receipt = None
                self.counts['explicit_drained_train_turn'] += 1

    def _completion_ready(self):
        if not self.flow.complete:
            return False
        self._poll_physical()
        if self.data_burst:
            if self._physical_data or self.worker.pending_physical:
                return False
            self.worker.assert_drained()
            require(not self.worker.fenced, 'worker_run_fenced')
        return True

    @property
    def receipt_confirmation_enabled(self):
        return self._receipt_payload is not None

    def enable_receipt_confirmation(self, context, *, receiver_ready=None):
        """Opt in before RF work; a sender receipt can shorten receiver grace.

        The caller binds its shared original resource/file-window context. The
        grant already binds peers, incarnation, route, object and deadlines.
        Receipt is useful only after the receiver has durably accepted bytes.
        """
        require(not self.closed and self._receipt_payload is None and not self.counts
                and type(context) is bytes and len(context) == 32
                and (receiver_ready is None if self.sends else callable(receiver_ready)),
                'invalid_receipt_confirmation_setup')
        _, port = _flow_modules()
        flow_key = self.authority.flow_key
        sender_direction = self.cipher.direction if self.sends else 1 - self.cipher.direction
        binding = dict(schema='meshticulum.radio-receipt-confirmation.v1',
            grant_sha256=self.cipher.grant.digest.hex(), flow_id=flow_key.flow_id,
            flow_initiator=dict(chain_id=flow_key.initiator.chain_id, agent_id=flow_key.initiator.agent_id),
            generation=self.flow.generation, sender_direction=sender_direction,
            application_context_sha256=context.hex())
        digest = hashlib.sha256(b'meshticulum.radio-receipt-confirmation.v1\0'
            + json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).digest()
        # The existing negotiated control-record bound is 32 bytes. This keeps
        # a domain tag and a 224-bit context digest inside that unchanged bound.
        payload = b'MRC1' + digest[:28]
        require(len(payload) <= self.owner.contract.limits.max_control_bytes,
                'receipt_confirmation_exceeds_control_bound')
        self._receipt_payload, self._receipt_ready = payload, receiver_ready
        if self.sends:
            self._receipt_link = self._link_class(scheduler=self.scheduler, owner=self.owner,
                flow=flow_key, authorize=self.authority.service_lease, broker=self.authority.broker_lease,
                carrier=self._send, monotonic_us=lambda: self.clock()[1],
                epoch_clock=lambda: self.clock()[0] / 1000000)

    def confirm_receipt(self):
        """Offer at most one accounted confirmation after authenticated ACK.

        No confirmation ACK is needed: loss preserves the receiver's existing
        finite grace. Unknown writes/fences still fail this command.
        """
        require(self.receipt_confirmation_enabled and self.sends and not self.closed
                and self._completion_ready() and self.authority.application_live(),
                'receipt_confirmation_before_receipt')
        if not self._receipt_requested:
            self._receipt_requested = True
            self.owner.resume_sending()
            self._receipt_dispatching = True
            try:
                self._receipt_link.send('receipt-confirmation', self._receipt_payload, kind='ack')
            finally:
                self._receipt_dispatching = False
            if not self._receipt_attempted:
                self.receipt_confirmation_outcome = 'NOT_DISPATCHED'
        self._check_flow()
        # Carrier exceptions are settled UNKNOWN by the scheduler even when a
        # worker fails to raise its own fence. A completed DATA receipt cannot
        # turn that unresolved confirmation attempt into command success.
        dispatch = self._receipt_link.last_result
        require(dispatch is None or dispatch.outcome is not CommitOutcome.UNKNOWN,
                'receipt_confirmation_write_unknown')
        require(self.authority.application_live(), 'receipt_confirmation_authority_expired')
        return self.receipt_confirmation_outcome == 'RF_DONE'

    def receipt_confirmation_snapshot(self):
        return dict(enabled=self.receipt_confirmation_enabled, attempted=self._receipt_attempted,
            outcome=self.receipt_confirmation_outcome, peer_receipt_confirmed=self.receipt_confirmed)

    def send(self, data):
        require(self.sends and type(data) is bytes and 0 < len(data) <= self.maximum_bytes,
                'invalid_bench_object')
        self.flow.send(data)

    def _service_ack_recovery(self, now_us):
        """Opt-in SF5 receiver scheduling; never mutates frozen receipt/retry state.

        Preserve the first persistence opportunity, then leave longer receive
        slots between unchanged ACKs. Fresh receive work bypasses this gate.
        After successful final ACK handover, request one bounded reannouncement
        of the same canonical snapshot through the frozen public API. This can
        repair a lost final ACK without asking the sender to retransmit DATA.
        """
        if (not self._sf5_ack_recovery_eligible or not self.receipt_confirmation_enabled
                or self.closed or self.flow.failure or self.worker.fenced or self.receipt_confirmed):
            return False
        if self.flow._ack_dirty or self.flow._ack_pending is not None or self.flow.startup_pending:
            return False
        if self.flow.complete:
            if (self._final_ack_repeat_due_us is not None
                    and now_us >= self._final_ack_repeat_due_us
                    and not self._final_ack_repeat_requested and not self._final_ack_repeat_satisfied
                    and not self.receipt_confirmed and self._receipt_ready() is True
                    and self.authority.application_live()):
                # grant_startup() marks the current canonical snapshot dirty;
                # it does not fabricate credit, a revision, a nonce, or a TX.
                # The normal scheduler, cipher, worker and journal still decide
                # and account the handover under the original finite authority.
                self._final_ack_repeat_requested = True
                state = self.journal.snapshot()
                budget = self.ledger.snapshot(now_us)
                if (state['attempts'] >= self.journal.max_attempts
                        or state['reserved_estimated_airtime_us'] + self._ack_reservation_us
                        > self.journal.max_airtime_us
                        or budget['own_remaining_us'] < self.owner.cost.own_tx_us
                        or budget['shared_remaining_us'] < self.owner.cost.shared_medium_us
                        or not budget['sample_fresh'] or budget['fenced']
                        or now_us < budget['recovery_until_us']
                        or budget['pending_reservations'] >= self.ledger.policy.max_reservations
                        or len(self.owner.retained) >= 1023):
                    self._final_ack_repeat_due_us = None
                    self.counts['final_ACK_reannouncement_budget_unavailable'] += 1
                    return False
                self.counts['final_ACK_reannouncement_requested'] += 1
                self.flow.grant_startup()
            return False
        # A zero streak is the first persistence opportunity: let the frozen
        # flow use its exact existing interval, including its clock origin.
        return self._ack_persistence_streak > 0 and now_us < self._ack_persistence_due_us

    def set_receiver_ack_quiet_us(self, value):
        """One pre-activity opt-in; changes only receiver ACK coalescing."""
        require(type(value) is int and value == 150000 and not self.closed
                and not self.counts and not self._receiver_quiet_override
                and self._receiver_quiet150_eligible,
                'invalid_receiver_ack_quiet_option')
        self.receiver_ack_quiet_us = value
        self._receiver_quiet_override = True

    def enable_selective_repeat_pacing(self):
        """Default-off host restraint; never edits the frozen flow's clocks."""
        require(not self.closed and not self.counts
                and self.receipt_confirmation_enabled and not self._pace_selective_repeats,
                'invalid_selective_repeat_pacing_setup')
        self._pace_selective_repeats = True

    def _refresh_repeat_pacing(self):
        if not self._pace_selective_repeats:
            return
        live = self.flow._in_flight
        for seq, (pending, attempts) in list(self._paced_fast_offers.items()):
            if live.get(seq) is not pending:
                del self._paced_fast_offers[seq]
            elif pending.attempts > attempts:
                # Only confirmed handover spends an attempt. Preserve this
                # accepted flight's original RTO boundary, including its
                # carrier airtime, rather than renewing it on later ACKs.
                self._repeat_not_before[seq] = (pending, pending.sent_at + pending.rto)
                del self._paced_fast_offers[seq]
            elif seq not in self.flow._deferred_retries:
                del self._paced_fast_offers[seq]
        for seq, (pending, _) in list(self._repeat_not_before.items()):
            if live.get(seq) is not pending:
                del self._repeat_not_before[seq]

    def _paced_selective_candidate(self, *, terminal=False, stall=False):
        # Mirror only the frozen offer's read-only choice. Never skip a cooled
        # flight and accidentally ask the core to offer that same flight.
        required = 1 if stall else self.flow._selective_evidence_required(terminal)
        for pending in self.flow._in_flight.values():
            if (pending.fast_retry_offered or len(pending.selective_evidence) < required
                    or pending.attempts >= self.flow.limits.max_retries
                    or pending.seq in self.flow._deferred_retries
                    or self.link.now < max(pending.handed_over_at, pending.selective_evidence_at)
                    or self.link.now - pending.sent_at >= pending.rto):
                continue
            return pending
        return None

    def _selective_pacing_ready(self, *, terminal=False, stall=False):
        if not self._pace_selective_repeats:
            return True
        self._refresh_repeat_pacing()
        pending = self._paced_selective_candidate(terminal=terminal, stall=stall)
        if pending is None:
            return False
        previous = self._repeat_not_before.get(pending.seq)
        return previous is None or self.link.now >= previous[1]

    def _offer_selective(self, *, terminal=False, stall=False):
        if not self._selective_pacing_ready(terminal=terminal, stall=stall):
            return False
        pending = self._paced_selective_candidate(terminal=terminal, stall=stall)
        attempts = pending.attempts if pending is not None else None
        offered = (self.flow.retry_selective_stall(self.link.now) if stall
                   else self.flow.retry_selective_gap(self.link.now, terminal=terminal))
        if self._pace_selective_repeats and offered and pending is not None:
            # A refused offer has no new attempt; it cannot start a cooldown.
            # A deferred offer is observed after its eventual normal settlement.
            self._paced_fast_offers[pending.seq] = (pending, attempts)
            self._refresh_repeat_pacing()
        return offered

    def _begin_repair_train(self, mode):
        require(mode in ('terminal', 'stall'), 'invalid_repair_train_mode')
        self._repair_train_active = True
        self._repair_train_mode = mode
        self._repair_train_domains = {self._last_queued_domain}

    def _hold_fast_retry_turn(self):
        self._refresh_repeat_pacing()
        # Only the explicit v8 terminal policy may group distinct proven
        # holes. The first repair reached this point through the ordinary
        # exact physical drain/STATUS gate. No fresh DATA remains, and the
        # accepted flow itself proves local and peer receipt of original FINAL.
        if (self._selective_repair_train and not self._repair_train_active
                and self.flow.queued_bytes() == 0
                and self.flow._original_final_handed_over
                and self.flow._peer_original_final_received
                and self._last_queued_domain is not None
                and 0 < self.burst_sent < self.burst_frames and self.next_send_us == 0
                and self.flow.selective_retry_ready(terminal=True)):
            self._begin_repair_train('terminal')
            self.counts['repair_train_turn'] += 1
            return
        # The v9/train12 host separates the final twelve-admission train from
        # its eight physical slots. If every logical-window slot is occupied
        # and one authenticated later SACK proves that a drained DATA flight is
        # blocking all fresh work, spend the same bounded repair train without
        # waiting for an ordinary two-second RTO.
        if (self._selective_stall_retry and self._last_queued_domain is not None
                and self.flow.selective_stall_ready()):
            self._begin_repair_train('stall')
            self.counts['stall_repair_turn'] += 1
            return
        # Only an explicitly admitted v4 policy can carry fresh DATA after a
        # repair. The QUEUED repair already counts toward this same admitted
        # train; do not reset that count or erase an existing reverse hold.
        # The normal flow/owner still admit each following DATA independently.
        if (self._selective_repair_fill and self.burst_frames in (8, 12, 24, 32)
                and 0 < self.burst_sent < self.burst_frames and self.next_send_us == 0
                and self.flow.queued_bytes() > 0 and self.flow._peer_grant_rev is not None
                and self.flow.in_flight() < min(self.flow.limits.window_frames, self.flow._peer_credit)
                and self.worker.has_capacity and not self.worker.fenced
                and self.authority.application_live()):
            self.counts['selective_repair_fill_turn'] += 1
            self._repair_fill_active = True
            return
        # One selective repair owns the reverse-control opportunity; do not
        # fill more DATA from the same pump after its accepted handover.
        self._hold_repair_reverse_turn()

    def _hold_repair_reverse_turn(self):
        self._repair_fill_active = False
        self._repair_train_active = False
        self._repair_train_mode = None
        self._repair_train_domains.clear()
        self.next_send_us = self.clock()[1] + self.worker.pending_airtime_us + self.sender_control_hold_us
        self.burst_sent = 0
        self.owner.settle_sending()

    def _step_repair_train(self):
        """One extra proven, distinct terminal hole per normal host step.

        Native receive floors and the original turn deadline still apply.
        Physical terminals are collected normally between offers, while the
        bounded domain set prevents a released attempt from rejoining this
        train. No frozen evidence, credit, retry clock or attempt is mutated.
        """
        self.update_counter()
        now_us = self.clock()[1]
        require(type(now_us) is int and now_us / 1000000 >= self.link.now,
                'repair_train_clock_regressed')
        self.link.now = now_us / 1000000
        if (self.owner.no_send or self.link._held is not None
                or self.worker.fenced or not self.authority.application_live()
                or self.worker.has_buffered_activity or not self.worker.has_capacity
                or set(self.worker.pending_physical) != set(self._physical_data)
                or not 0 < self.burst_sent < self.burst_frames
                or len(self._repair_train_domains) >= self.burst_frames
                or now_us < self.next_send_us
                or any(self.link.now - p.sent_at >= p.rto for p in self.flow._in_flight.values())
                or self._repair_train_mode not in ('terminal', 'stall')):
            self._hold_repair_reverse_turn()
            return
        terminal = self._repair_train_mode == 'terminal'
        if terminal:
            ready = (self.flow.queued_bytes() == 0
                     and self.flow._original_final_handed_over
                     and self.flow._peer_original_final_received
                     and self.flow.selective_retry_ready(terminal=True))
        else:
            ready = self.flow.selective_stall_ready()
        if not ready or not self._selective_pacing_ready(terminal=terminal, stall=not terminal):
            self._hold_repair_reverse_turn()
            return
        fast_before = self.flow.fast_retransmits
        self._repair_train_offer = True
        try:
            offered = self._offer_selective(terminal=terminal, stall=not terminal)
        finally:
            self._repair_train_offer = False
        if (not offered or self.flow.fast_retransmits != fast_before + 1
                or not (self.flow.selective_retry_ready(terminal=True)
                        if terminal else self.flow.selective_stall_ready())):
            # REFUSED and DEFERRED retain normal owner/flow state. They never
            # authorize another offer or bypass the existing reverse hold.
            self._hold_repair_reverse_turn()

    def _pump_scalar_fill_pair(self, queued_before, fresh_before, fast_before):
        """At most one extra fresh scalar offer after one accepted handover.

        These hints grant no authority. Every actual offer still passes through
        the held owner, current leases, cipher and durable worker admission.
        Unreleased terminals count as occupied; buffered ingress gets the next
        ordinary step instead of being delayed behind another scalar write.
        """
        if not self._scalar_fill_pair or self.counts['tx_QUEUED'] != queued_before + 1:
            return
        fast = self.flow.fast_retransmits - fast_before
        if not ((fast == 0 and self.flow._next_seq == fresh_before + 1)
                or (fast == 1 and self.flow._next_seq == fresh_before and self._repair_fill_active)):
            return
        dispatch = self.link.last_result
        if (dispatch is None or dispatch.outcome is not CommitOutcome.ACCEPTED or not dispatch.invoked
                or self.owner.no_send or self.worker.fenced or self.flow.complete
                or self.link._held is not None or self.worker.has_buffered_activity
                or not self.worker.has_capacity or not 0 < self.burst_sent < self.burst_frames
                or len(self.worker.pending_physical) > 1
                or set(self.worker.pending_physical) != set(self._physical_data)
                or self.flow.queued_bytes() <= 0 or self.flow._peer_grant_rev is None
                or self.flow.in_flight() >= min(self.flow.limits.window_frames, self.flow._peer_credit)
                or not self.authority.application_live()):
            return
        self.update_counter()
        # Sample after live checks and after the first scalar handover. Never
        # reuse the earlier pump instant for expiry, pacing or retry admission.
        now_us = self.clock()[1]
        require(type(now_us) is int and now_us / 1000000 >= self.link.now,
                'scalar_fill_clock_regressed')
        if now_us < self.next_send_us:
            return
        self.link.now = now_us / 1000000
        if any(self.link.now - pending.sent_at >= pending.rto
               for pending in self.flow._in_flight.values()):
            if self._repair_fill_active:
                self.counts['selective_repair_fill_timeout_hold'] += 1
                self._hold_repair_reverse_turn()
            return
        self._scalar_fill_extra = True
        try:
            with phase(self.host_timing, Phase.FLOW_PUMP):
                self.flow.pump(self.link.now, max_frames=1, drain=False)
        finally:
            self._scalar_fill_extra = False
        if self.counts['tx_QUEUED'] == queued_before + 2:
            self.counts['scalar_fill_pair_extra'] += 1

    @timed(Phase.FLOW_STEP)
    def step(self):
        queued_before, fresh_before, fast_before_step = ((self.counts['tx_QUEUED'],
            self.flow._next_seq, self.flow.fast_retransmits) if self._scalar_fill_pair else (None, None, None))
        require(self.dispatch_budget_failure is None, 'bench_own_airtime_budget_exhausted')
        require(not self.closed and self.authority.live(), 'bench_authority_not_live')
        self._poll_physical()
        self.update_counter()
        # Bound unauthenticated ingress CPU and the accepted adapter's inbound
        # list even in a noisy environment. Invalid frames never reach MT05.
        for _ in range(8):
            with phase(self.host_timing, Phase.FLOW_RECEIVE):
                item = self.worker.receive(timeout=0)
            if item is None:
                break
            if self._repair_train_active:
                # Even unauthenticated ingress ends an active repair train.
                # A later progress ACK may release this hold normally, but a
                # new train must again pass the exact physical drain gate.
                self._hold_repair_reverse_turn()
            at, raw = item
            try:
                with phase(self.host_timing, Phase.CIPHER_OPEN):
                    verified = self.cipher.open(raw.packet, authorize=self.authority.live)
                turn_end = verified.kind is FrameKind.DATA_TURN_END
                require(not turn_end or (self._turn_end_enabled and not self.sends
                        and turn_end_admitted(self.authority.plan)), 'turn_end_receive_not_admitted')
                received_kind = FrameKind.DATA if turn_end else verified.kind
                if verified.kind is FrameKind.CONTROL:
                    require(self.receipt_confirmation_enabled and not self.sends
                            and not self.receipt_confirmed and self.flow.complete
                            and verified.payload == self._receipt_payload
                            and self._receipt_ready() is True
                            and self.authority.application_live(), 'invalid_receipt_confirmation')
                    self.receipt_confirmed = True
                    self.counts['rx_CONTROL'] += 1
                    self.events.append({'event': 'rx', 'mono_us': self.clock()[1],
                        'outer_counter': verified.counter, 'outer_kind': 'CONTROL',
                        'packet_sha256': hashlib.sha256(raw.packet).hexdigest(),
                        'bytes': len(raw.packet), 'rssi_dbm': raw.rssi_dbm, 'snr_db': raw.snr_db})
                    anchor = getattr(self.worker, 'anchor_receive_timeline', None)
                    if anchor is not None:
                        anchor(self.events[-1]['packet_sha256'])
                    continue
                parsed = self.parse_frame(verified.payload)
                require(parsed is not None, 'invalid_authenticated_inner_frame')
                expected = FrameKind.ACK if parsed['kind'] == self.ack_kind else FrameKind.DATA
                require(received_kind is expected, 'authenticated_inner_lane_mismatch')
            except WireError as error:
                self.counts['rx_refused'] += 1
                self.events.append({'event': 'rx_refused', 'reason': str(error),
                                    'mono_us': self.clock()[1]})
                continue
            self.counts['rx_' + received_kind.name] += 1
            self.link.now = at
            # The reviewed flow exposes absorb() for authenticated demultiplexed
            # frames. Apply and consume before pumping output so the first ACK
            # includes returned credit. Consuming after pump emitted a second
            # ACK in the next DATA slot on the real half-duplex radio.
            acknowledged_before = self.flow.acked_frames()
            # Read the frozen flow's receive-work generation, without mutating
            # it. Authentication alone does not make an invalid DATA admitted.
            receive_work_before = self.flow._ack_work_gen
            previous_receipt = (self.flow._peer_grant_rev, self.flow.repeat_grants)
            self.flow.absorb(parsed, at)
            self.last_activity_us = self.clock()[1]
            if (not self.sends and self.burst_frames > 1 and received_kind is FrameKind.DATA
                    and parsed['flow_id'] == self.flow.flow_id
                    and parsed['generation'] == self.flow.generation
                    and self.flow._ack_work_gen > receive_work_before):
                if not self.burst_received:
                    self.burst_started_us = self.last_activity_us
                self.burst_received += 1
                self.ack_due_us = (self.last_activity_us if self.burst_received >= self.burst_frames
                    or self.flow.complete else min(self.last_activity_us + self.receiver_ack_quiet_us,
                        self.burst_started_us + self.burst_max_hold_us))
                if self._turn_end_enabled:
                    newer = verified.counter > self._turn_end_highest_received
                    self._turn_end_highest_received = max(self._turn_end_highest_received, verified.counter)
                    if turn_end and newer:
                        self.ack_due_us = self.last_activity_us
                        self.counts['rx_DATA_turn_end'] += 1
                    elif turn_end:
                        self.counts['rx_DATA_turn_end_old'] += 1
            # A received authenticated ACK has already finished on air. Once
            # its flow state is applied there is no reason to spend the rest
            # of a conservative reverse-airtime wait before the next DATA.
            if (self.sends and verified.kind is FrameKind.ACK
                    and self.flow.acked_frames() > acknowledged_before):
                if self.burst_frames == 32:
                    self._train_progress_receipt = self.worker.train_identity
                self.next_send_us = max(self._refused_turn_until_us,
                                        min(self.next_send_us, self.last_activity_us))
                self.burst_sent = 0
                self._repair_fill_active = False
                self._repair_train_active = False
                self._repair_train_domains.clear()
            ack_diagnostics = {}
            if verified.kind is FrameKind.ACK:
                delta = self.flow.acked_frames() - acknowledged_before
                effect = ('startup' if previous_receipt[0] is None and self.flow._peer_grant_rev is not None
                    else 'progress' if delta else 'fresh_credit'
                    if self.flow._peer_grant_rev != previous_receipt[0] else 'unchanged'
                    if self.flow.repeat_grants > previous_receipt[1] else 'ignored')
                self.counts['rx_ACK_' + effect] += 1
                ack_diagnostics = dict(ack_effect=effect, acked_frames_delta=delta,
                    ack_seq=parsed['ack_seq'], ack_sack=parsed['sack'], ack_credit=parsed['credit'],
                    ack_revision=parsed['grant_revision'])
            self.events.append({'event': 'rx', 'mono_us': self.last_activity_us,
                'outer_counter': verified.counter, 'outer_kind': received_kind.name,
                'packet_sha256': hashlib.sha256(raw.packet).hexdigest(),
                'bytes': len(raw.packet), 'rssi_dbm': raw.rssi_dbm, 'snr_db': raw.snr_db,
                **ack_diagnostics, **({'wire_kind': 'DATA_TURN_END'} if turn_end else {})})
            anchor = getattr(self.worker, 'anchor_receive_timeline', None)
            if anchor is not None:
                anchor(self.events[-1]['packet_sha256'])
            # Reach the guarded DATA pump when a genuine receipt permits work.
            # Repeated ACKs stay queued instead of consuming the peer's bounded
            # startup/control window while each native receive is checked.
            accepted_receipt = (self.flow._peer_grant_rev, self.flow.repeat_grants) != previous_receipt
            if (self.sends and verified.kind is FrameKind.ACK and accepted_receipt
                    and self.flow.queued_bytes() > 0
                    and self.flow.in_flight() < min(self.flow.limits.window_frames, self.flow._peer_credit)):
                break
        self._poll_physical()
        if self.receipt_confirmed:
            self._check_flow()
            return self._completion_ready()
        if not self.sends and self.flow.delivered:
            data = self.flow.consume(len(self.flow.delivered))
            require(len(self.received) + len(data) <= self.maximum_bytes, 'bench_receive_limit')
            self.received.extend(data)
        step_mono_us = self.clock()[1]
        self.link.now = step_mono_us / 1000000
        if self._service_ack_recovery(step_mono_us):
            self.owner.settle_sending()
            self._check_flow()
            return self._completion_ready()
        # Coalesce the ACK before asking the frozen flow to encode it. Merely
        # pausing an already queued ACK would preserve a stale receipt snapshot
        # and cause another ACK in the next DATA slot. A short/lost burst is
        # answered after one maximum packet plus bounded host-turnaround time.
        if (not self.sends and self.burst_received and self.clock()[1] < self.ack_due_us):
            self.owner.settle_sending()
            self._check_flow()
            return self._completion_ready()
        if self._repair_train_active:
            self._step_repair_train()
            self._check_flow()
            return self._completion_ready()
        if (self._repair_fill_active and any(
                self.link.now - pending.sent_at >= pending.rto
                for pending in self.flow._in_flight.values())):
            # Read the accepted R11 deadline without changing it. An ordinary
            # retry ends this fresh-DATA-only fill before dispatch/prepare can
            # reserve another handle; the original reverse turn then applies.
            self.counts['selective_repair_fill_timeout_hold'] += 1
            self._hold_repair_reverse_turn()
        if (self.clock()[1] < self.next_send_us
                or self.data_burst and not self.worker.has_capacity):
            self.owner.settle_sending()
        else:
            self.owner.resume_sending()
            fast_before = self.flow.fast_retransmits
            with phase(self.host_timing, Phase.FLOW_DISPATCH):
                self.link.dispatch_queued()
            if self.data_burst and self.flow.fast_retransmits > fast_before:
                self._hold_fast_retry_turn()
                self._pump_scalar_fill_pair(queued_before, fresh_before, fast_before_step)
                self._check_flow()
                return self._completion_ready()
            normal_ready = stall_ready = False
            if self.clock()[1] < self.next_send_us:
                self.owner.settle_sending()
            else:
                normal_ready = (self.data_burst and self.receipt_confirmation_enabled
                    and not self._repair_fill_active
                    and self.flow.selective_retry_ready(terminal=self._selective_terminal_retry))
                stall_ready = (not normal_ready and self._selective_stall_retry
                    and self.flow.selective_stall_ready())
            if (self.data_burst and self.receipt_confirmation_enabled
                    and (normal_ready or stall_ready)
                    and self._selective_pacing_ready(
                        terminal=self._selective_terminal_retry, stall=stall_ready)
                    and not self._physical_data and not self.worker.pending_physical):
                # Peer SACK is logical evidence, never local physical drain.
                # Verify release/STATUS before offering one early retransmit;
                # the normal held owner, cipher and journal still authorize it.
                self.worker.assert_drained()
                require(not self.worker.fenced and self.authority.application_live(),
                        'selective_retry_authority_not_live')
                now_us = self.clock()[1]
                self.link.now = now_us / 1000000
                fast_before = self.flow.fast_retransmits
                offered = self._offer_selective(
                    terminal=self._selective_terminal_retry, stall=stall_ready)
                if offered:
                    if self.flow.fast_retransmits > fast_before:
                        self._hold_fast_retry_turn()
                        self._pump_scalar_fill_pair(queued_before, fresh_before, fast_before_step)
                    self._check_flow()
                    return self._completion_ready()
        with phase(self.host_timing, Phase.FLOW_PUMP):
            self.flow.pump(self.link.now, max_frames=1, drain=False)
        self._pump_scalar_fill_pair(queued_before, fresh_before, fast_before_step)
        # Keep the reviewed adapter's diagnostic history bounded too; no
        # application data is exported from it in the run receipt.
        del self.link.sent[:-64]
        self._check_flow()
        return self._completion_ready()

    def _check_flow(self):
        if self.flow.failure:
            raise WireError('reliable_flow_' + self.flow.failure)
        if self.worker.fenced:
            raise WireError('worker_run_fenced')
        require(not self.scheduler.snapshot()['fenced_flows'], 'bench_scheduler_flow_fenced')
        dispatch = self.link.last_result
        if (self.sends and self.flow.queued_bytes() > 0
                and dispatch is not None and dispatch.outcome is CommitOutcome.REFUSED_SCHEDULER
                and dispatch.reason == 'dispatch_budget_refused' and not dispatch.invoked
                and not dispatch.flow_fenced):
            budget = self.ledger.snapshot(self.clock()[1])
            deadlines = self.owner.original_deadlines
            deadline_us = math.ceil(min(deadlines.application_deadline_monotonic,
                                        deadlines.authority_deadline_monotonic) * 1000000)
            # The frozen ledger deliberately adds observed counter progress to
            # persistent reservation debt. Neither ACKs nor quiet time refund
            # either term. Within this fixed window own usage cannot decrease;
            # if its end is beyond the original send deadline, unsent application
            # bytes cannot finish. With no unsent bytes a later ACK may still
            # complete an already handed-over DATA despite a refused retry.
            # Do not mint hundreds of known-unsent handles until the
            # compatibility retention bound hides the permanent refusal.
            if (budget['own_remaining_us'] < self.owner.cost.own_tx_us
                    and budget['window_end_us'] >= deadline_us):
                self.dispatch_budget_failure = dict(reason=dispatch.reason,
                    required_own_us=self.owner.cost.own_tx_us,
                    own_limit_us=self.ledger.policy.own_tx_limit_us,
                    original_send_deadline_us=deadline_us, ledger=budget)
                self.events.append(dict(event='dispatch_budget_exhausted',
                    mono_us=self.clock()[1], **self.dispatch_budget_failure))
                raise WireError('bench_own_airtime_budget_exhausted')
        require(len(self.owner.retained) < 1024, 'bench_handle_retention_limit')

    def snapshot(self):
        value = {'complete': self.flow.complete, 'received_bytes': len(self.received),
            'counts': dict(self.counts), 'retransmits': self.flow.retransmits,
            'fast_retry_offers': self.flow.fast_retry_offers,
            'fast_retransmits': self.flow.fast_retransmits,
            'acked_frames': self.flow.acked_frames(), 'duplicates': self.flow.duplicates,
            'startup_pending': self.flow.startup_pending, 'rto_seconds': self.flow.rto,
            'timing_policy': {'receive_gap_us': self.receive_gap_us,
                             'flow_window_frames': self.flow.limits.window_frames,
                             'selective_repair_fill': self._selective_repair_fill,
                             'selective_terminal_retry': self._selective_terminal_retry,
                             'selective_stall_retry': self._selective_stall_retry,
                             'burst_frames': self.burst_frames, 'burst_quiet_us': self.burst_quiet_us,
                             'receiver_ack_quiet_us': self.receiver_ack_quiet_us,
                             'burst_max_hold_us': self.burst_max_hold_us,
                             'sender_control_hold_us': self.sender_control_hold_us,
                             'ack_delay_seconds': self.flow.limits.ack_delay_s,
                             'persist_interval_seconds': self.flow.limits.persist_interval_s,
                             'sf5_ack_recovery': (self._sf5_ack_recovery_eligible
                                                  and self.receipt_confirmation_enabled),
                             'unchanged_ack_persistence_max_multiplier': 4,
                             'final_ack_reannouncement_limit': 1,
                             'control_rate_us_per_second': self.scheduler.policy.control_rate_us_per_second},
            'attempt_journal': self.journal.snapshot(), 'events': list(self.events),
            'scheduler': self.scheduler.snapshot()}
        receive_snapshot = getattr(self.worker, 'receive_reason_snapshot', None)
        if receive_snapshot is not None:
            reasons = receive_snapshot()
            if reasons is not None:
                value['hat_receive_reasons'] = reasons
        timeline_snapshot = getattr(self.worker, 'receive_timeline_snapshot', None)
        if timeline_snapshot is not None:
            timeline = timeline_snapshot()
            if timeline is not None:
                value['hat_receive_timeline'] = timeline
        if self._pace_selective_repeats:
            value['timing_policy']['pace_selective_repeats'] = True
        if self._scalar_fill_pair:
            value['timing_policy']['scalar_fill_pair'] = True
        if self._selective_repeat_retry:
            value['timing_policy']['selective_repeat_retry'] = True
            value['repeat_retry_epochs'] = self.flow.repeat_retry_epochs
        if self._selective_repair_train:
            value['timing_policy']['selective_repair_train'] = True
        if self.dispatch_budget_failure is not None:
            value['dispatch_budget_failure'] = self.dispatch_budget_failure
        if self.data_burst:
            value['physical_queue'] = dict(pending=len(self._physical_data),
                worker_pending=len(self.worker.pending_physical),
                events=self.worker.physical_snapshot())
        if self.receipt_confirmation_enabled:
            value['receipt_confirmation'] = self.receipt_confirmation_snapshot()
        if self.host_timing is not None:
            try:
                value['host_phase_timings'] = self.host_timing.snapshot()
            except Exception:
                value['host_phase_timings_unavailable'] = True
        return value

    def close(self):
        if not self.closed:
            self.closed = True
            self.owner.settle_sending()
            try:
                if self.data_burst:
                    if self.worker.pending_physical:
                        self.worker.cancel_pending()
                    self._collect_physical(self.worker.drain_physical())
                    require(not self._physical_data, 'unresolved_physical_data_on_close')
            finally:
                self.scheduler.close()
