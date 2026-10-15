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
import math
from pathlib import Path
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
from .gfsk_profile import radio_airtime_us,radio_is_fast,radio_is_sf5
from .public_block import object_limit


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
                 profile, ledger, scheduler_epoch, broker_epoch, sends, clock):
        require(type(flow_key) is FlowKey and type(contract) is NegotiatedContract
                and type(profile) is CarrierProfile and type(ledger) is Ledger,
                'invalid_flow_composition')
        require(cipher.grant.session == flow_key.session and contract.route.session == flow_key.session,
                'flow_session_mismatch')
        require(profile.payload_capacity == cipher.grant.max_plaintext == 208,
                'unsupported_flow_frame_capacity')
        require(type(sends) is bool and callable(clock), 'invalid_flow_direction')
        self.authority, self.worker, self.cipher = authority, worker, cipher
        self.journal, self.clock, self.ledger = journal, clock, ledger
        self.sends = sends
        self.events = deque(maxlen=512)
        self.counts = Counter()
        self.received = bytearray()
        self.last_activity_us = self.clock()[1]
        flow, port = _flow_modules()
        self.next_send_us = 0
        self.receive_gap_us = math.ceil(radio_airtime_us(authority.profile,
            flow.ACK_BYTES + 47) * 1.10) + 60000
        limits = contract.limits
        self.maximum_bytes = min(limits.max_queued_bytes, limits.max_unconsumed_bytes)
        require(self.maximum_bytes <= object_limit(authority.plan.get('service',{})), 'bench_transfer_too_large')
        # Owner credit uses the admitted frame bound (208). Derive the DATA
        # overhead from the actual MT3 wire encoder, not older adapter prose:
        # the reviewed encoding uses fifteen bytes, leaving 193 useful bytes.
        window = min(4, limits.window_bytes // limits.max_payload_bytes,
                     limits.max_reassembly_frames)
        require(window >= 1, 'bench_window_too_small')
        self.burst_frames = window if radio_is_fast(authority.profile) else 1
        fast_sf5 = radio_is_sf5(authority.profile)
        self.burst_quiet_us = math.ceil(profile.forward_seconds * 1000000) + 60000
        if self.burst_frames > 1:
            # USB/driver completion can span more than one PHY packet. Keep
            # fast SF5 quiet at250 ms when its retry floor grows: retry and
            # inter-frame silence are different clocks. Other profiles retain
            # their negotiated control window.
            quiet_rto = min(profile.initial_rto_seconds, .5) if fast_sf5 else profile.initial_rto_seconds
            self.burst_quiet_us = max(self.burst_quiet_us, math.ceil(quiet_rto * 500000))
        self.burst_max_hold_us = math.ceil(profile.initial_rto_seconds * 1000000)
        if fast_sf5:
            # A bounded four-frame train can exceed the old 500ms hold under
            # native host work. Never let its total-time cap preempt a train
            # whose individual gaps remain inside the quiet window. A full
            # train still earns its ACK immediately in step().
            self.burst_max_hold_us = self.burst_frames * self.burst_quiet_us
        self.burst_sent = 0
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
        self.link = port.FlowPortLink(scheduler=self.scheduler, owner=self.owner,
            flow=flow_key, authorize=authority.service_lease, broker=authority.broker_lease,
            carrier=self._send, monotonic_us=lambda: self.clock()[1],
            epoch_clock=lambda: self.clock()[0] / 1000000)
        self.flow = flow.ReliableFlow('a' if sends else 'b', self.link,
            flow.FlowLimits(frame_payload_bytes=quantum, window_frames=window,
                initial_credit_frames=window, max_reassembly_frames=window,
                max_delivered_bytes=self.maximum_bytes, max_queued_bytes=self.maximum_bytes,
                max_total_frames=min(256 if authority.public32 else 128, limits.max_total_frames),
                max_retries=limits.max_retry_attempts,
                initial_rto_s=profile.initial_rto_seconds,
                min_rto_s=profile.min_rto_seconds, max_rto_s=profile.max_rto_seconds,
                ack_delay_s=0.01, persist_interval_s=max(.5,
                    2 * profile.forward_seconds + profile.reverse_control_seconds + .15),
                max_persist_acks=8),
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
        self.parse_frame, self.ack_kind = flow.parse_frame, flow.KIND_ACK
        self.closed = False
        self.update_counter()

    def update_counter(self):
        state = self.journal.snapshot()
        require(not state['fenced'], 'worker_run_fenced')
        now = self.clock()[1]
        self.ledger.observe(CounterSample(now, state['reserved_estimated_airtime_us'],
                                         self.ledger.epoch), now)

    def _send(self, frame):
        # The authority checks exact object identity of both currently held
        # leases and the immutable request, as well as the OS radio owner.
        self.authority.require_dispatch(frame)
        parsed = self.parse_frame(frame.payload)
        require(parsed is not None, 'invalid_inner_transmit')
        kind = FrameKind.ACK if parsed['kind'] == self.ack_kind else FrameKind.DATA
        require((frame.request.role is FrameRole.CONTROL) == (kind is FrameKind.ACK),
                'inner_lane_mismatch')
        packet = self.cipher.seal(frame.payload, kind, authorize=self.authority.live)
        worker_started_us = self.clock()[1]
        result = self.worker.transmit(packet)
        worker_finished_us = self.clock()[1]
        require(type(worker_started_us) is int and type(worker_finished_us) is int
                and worker_finished_us >= worker_started_us, 'invalid_worker_timing')
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
            'driver_error': driver.driver_error if driver is not None else None})
        self.last_activity_us = self.clock()[1]
        # A single half-duplex radio needs time to hear the reverse ACK before
        # another DATA start. This is an explicit initial bench pacing policy,
        # not a measured optimum or a permanent protocol stop-and-wait rule.
        self.next_send_us = (0 if self.burst_frames > 1
                             else self.last_activity_us + self.receive_gap_us)
        if result.outcome == 'RF_DONE':
            if kind is FrameKind.ACK:
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
            return CommitOutcome.REFUSED_CARRIER
        self.owner.settle_sending()
        return CommitOutcome.UNKNOWN

    def send(self, data):
        require(self.sends and type(data) is bytes and 0 < len(data) <= self.maximum_bytes,
                'invalid_bench_object')
        self.flow.send(data)

    def step(self):
        require(not self.closed and self.authority.live(), 'bench_authority_not_live')
        self.update_counter()
        # Bound unauthenticated ingress CPU and the accepted adapter's inbound
        # list even in a noisy environment. Invalid frames never reach MT05.
        for _ in range(8):
            item = self.worker.receive(timeout=0)
            if item is None:
                break
            at, raw = item
            try:
                verified = self.cipher.open(raw.packet, authorize=self.authority.live)
                parsed = self.parse_frame(verified.payload)
                require(parsed is not None, 'invalid_authenticated_inner_frame')
                expected = FrameKind.ACK if parsed['kind'] == self.ack_kind else FrameKind.DATA
                require(verified.kind is expected, 'authenticated_inner_lane_mismatch')
            except WireError as error:
                self.counts['rx_refused'] += 1
                self.events.append({'event': 'rx_refused', 'reason': str(error),
                                    'mono_us': self.clock()[1]})
                continue
            self.counts['rx_' + verified.kind.name] += 1
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
            if (not self.sends and self.burst_frames > 1 and verified.kind is FrameKind.DATA
                    and parsed['flow_id'] == self.flow.flow_id
                    and parsed['generation'] == self.flow.generation
                    and self.flow._ack_work_gen > receive_work_before):
                if not self.burst_received:
                    self.burst_started_us = self.last_activity_us
                self.burst_received += 1
                self.ack_due_us = (self.last_activity_us if self.burst_received >= self.burst_frames
                    or self.flow.complete else min(self.last_activity_us + self.burst_quiet_us,
                        self.burst_started_us + self.burst_max_hold_us))
            # A received authenticated ACK has already finished on air. Once
            # its flow state is applied there is no reason to spend the rest
            # of a conservative reverse-airtime wait before the next DATA.
            if (self.sends and verified.kind is FrameKind.ACK
                    and self.flow.acked_frames() > acknowledged_before):
                self.next_send_us = min(self.next_send_us, self.last_activity_us)
                self.burst_sent = 0
            self.events.append({'event': 'rx', 'mono_us': self.last_activity_us,
                'outer_counter': verified.counter, 'outer_kind': verified.kind.name,
                'packet_sha256': hashlib.sha256(raw.packet).hexdigest(),
                'bytes': len(raw.packet), 'rssi_dbm': raw.rssi_dbm, 'snr_db': raw.snr_db})
            # Reach the guarded DATA pump when a genuine receipt permits work.
            # Repeated ACKs stay queued instead of consuming the peer's bounded
            # startup/control window while each native receive is checked.
            accepted_receipt = (self.flow._peer_grant_rev, self.flow.repeat_grants) != previous_receipt
            if (self.sends and verified.kind is FrameKind.ACK and accepted_receipt
                    and self.flow.queued_bytes() > 0
                    and self.flow.in_flight() < min(self.flow.limits.window_frames, self.flow._peer_credit)):
                break
        if not self.sends and self.flow.delivered:
            data = self.flow.consume(len(self.flow.delivered))
            require(len(self.received) + len(data) <= self.maximum_bytes, 'bench_receive_limit')
            self.received.extend(data)
        self.link.now = self.clock()[1] / 1000000
        # Coalesce the ACK before asking the frozen flow to encode it. Merely
        # pausing an already queued ACK would preserve a stale receipt snapshot
        # and cause another ACK in the next DATA slot. A short/lost burst is
        # answered after one maximum packet plus bounded host-turnaround time.
        if (not self.sends and self.burst_received and self.clock()[1] < self.ack_due_us):
            self.owner.settle_sending()
            self._check_flow()
            return self.flow.complete
        if self.clock()[1] < self.next_send_us:
            self.owner.settle_sending()
        else:
            self.owner.resume_sending()
            self.link.dispatch_queued()
            if self.clock()[1] < self.next_send_us:
                self.owner.settle_sending()
        self.flow.pump(self.link.now, max_frames=1, drain=False)
        # Keep the reviewed adapter's diagnostic history bounded too; no
        # application data is exported from it in the run receipt.
        del self.link.sent[:-64]
        self._check_flow()
        return self.flow.complete

    def _check_flow(self):
        if self.flow.failure:
            raise WireError('reliable_flow_' + self.flow.failure)
        if self.worker.fenced:
            raise WireError('worker_run_fenced')
        require(not self.scheduler.snapshot()['fenced_flows'], 'bench_scheduler_flow_fenced')
        require(len(self.owner.retained) < 1024, 'bench_handle_retention_limit')

    def snapshot(self):
        return {'complete': self.flow.complete, 'received_bytes': len(self.received),
            'counts': dict(self.counts), 'retransmits': self.flow.retransmits,
            'acked_frames': self.flow.acked_frames(), 'duplicates': self.flow.duplicates,
            'startup_pending': self.flow.startup_pending, 'rto_seconds': self.flow.rto,
            'timing_policy': {'receive_gap_us': self.receive_gap_us,
                             'burst_frames': self.burst_frames, 'burst_quiet_us': self.burst_quiet_us,
                             'burst_max_hold_us': self.burst_max_hold_us,
                             'sender_control_hold_us': self.sender_control_hold_us,
                             'ack_delay_seconds': self.flow.limits.ack_delay_s,
                             'persist_interval_seconds': self.flow.limits.persist_interval_s,
                             'control_rate_us_per_second': self.scheduler.policy.control_rate_us_per_second},
            'attempt_journal': self.journal.snapshot(), 'events': list(self.events),
            'scheduler': self.scheduler.snapshot()}

    def close(self):
        if not self.closed:
            self.closed = True
            self.owner.settle_sending()
            self.scheduler.close()
