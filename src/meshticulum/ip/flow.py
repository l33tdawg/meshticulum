"""Frozen MT05 reliable flow over authenticated, byte-budgeted TCP dispatch.

No airtime ledger or RF completion token is reused for IP. The ordinary
synchronous flow handover occurs only after the entire bounded framed packet
is accepted by the local kernel; peer receipt still requires a valid ACK.
"""
from collections import Counter,deque
from types import SimpleNamespace

from ..flow.flow import ReliableFlow,FlowLimits,parse_frame,KIND_ACK,MIN_FRAME_BYTES
from ..flow.carrier import Delivery
from .authority import IpAuthority
from .state import SessionStore
from .wire import IpCipher,FrameKind,IpError,require
from .worker import TcpWorker


class _TcpLink:
    def __init__(self,owner):
        self.owner=owner;self.now=owner.clock()[1]/1e6
        # Unknown serialization rate; frozen flow's zero-rate branch adds no
        # made-up queue delay. RTO bounds are explicit source policy below.
        self.view=SimpleNamespace(capacity_bytes_per_second=0.0)
        self.pending=[]

    def incarnation(self,endpoint):return 1
    def profile(self,endpoint):return self.view
    def queued_bytes(self,endpoint):return 0
    def inbound(self,endpoint):
        pending,self.pending=self.pending,[];return pending

    def send(self,endpoint,payload,*,kind='data'):
        owner=self.owner
        require(endpoint==owner.endpoint and not owner.closed,'tcp_flow_sender_mismatch')
        parsed=parse_frame(payload);require(parsed is not None,'invalid_tcp_inner_frame')
        expected='ack' if parsed['kind']==KIND_ACK else 'data'
        require(kind==expected and parsed['flow_id']==1 and parsed['generation']==1,'tcp_inner_lane_mismatch')
        owner.authority.require_dispatch()
        frame_kind=FrameKind.ACK if kind=='ack' else FrameKind.DATA
        packet=owner.cipher.seal(payload,frame_kind,authorize=owner.authority.live)
        owner.worker.transmit(packet,frame_kind.name,owner.authority)
        owner.counts['tx_'+frame_kind.name]+=1
        return True


class IpFlow:
    def __init__(self,*,authority,worker,store):
        require(type(authority) is IpAuthority and type(worker) is TcpWorker and type(store) is SessionStore,
          'invalid_ip_flow_composition')
        require(worker.grant==authority.grant and worker.local_peer==authority.local_peer
          and store.grant==authority.grant and store.original_deadline_us==authority.application_deadline_us and worker.owned(),'ip_flow_binding_mismatch')
        require(getattr(authority, '_ip_flow', None) is None, 'ip_grant_flow_already_claimed')
        authority._ip_flow = self
        self.authority,self.worker,self.store=authority,worker,store
        self.clock=authority.clock;self.sends=authority.sends
        self.maximum_bytes=authority.grant.limits.max_object_bytes
        self.expected_bytes=authority.grant.object_bytes
        self.cipher=IpCipher(authority.grant,authority.local_peer,store)
        self.endpoint='a' if self.sends else 'b'
        self.received=bytearray();self.counts=Counter();self.events=deque(maxlen=64)
        self.closed=False;self.started=False
        self.link=_TcpLink(self);caps=authority.grant.limits
        self.flow=ReliableFlow(self.endpoint,self.link,FlowLimits(
          frame_payload_bytes=min(caps.max_plaintext-MIN_FRAME_BYTES,self.expected_bytes),window_frames=caps.window_frames,
          initial_credit_frames=caps.window_frames,max_reassembly_frames=caps.window_frames,
          max_delivered_bytes=self.expected_bytes,max_queued_bytes=self.expected_bytes,
          max_total_frames=min(65535,caps.max_frames),max_retries=caps.max_retries,
          initial_rto_s=.5,min_rto_s=.1,max_rto_s=5,ack_delay_s=.005,persist_interval_s=.5,max_persist_acks=8),
          flow_id=1,generation=1,sends=self.sends,receive=not self.sends,startup=None)

    def service_fresh(self):
        return not self.started and not self.closed and not self.events and not self.received and not self.flow.queued_bytes()

    def send(self,payload):
        require(self.sends and not self.started and not self.closed,'ip_flow_send_not_fresh')
        self.authority.require_dispatch();self.flow.send(payload);self.started=True

    def step(self):
        require(not self.closed,'ip_flow_closed')
        try:
            with self.authority.application_lease():
                require(self.worker.owned(),'ip_worker_not_owned')
                for _ in range(64):
                    packet=self.worker.receive(self.authority)
                    if packet is None:break
                    try:
                        verified=self.cipher.open(packet,authorize=self.authority.live)
                        parsed=parse_frame(verified.payload)
                        require(parsed is not None and parsed['flow_id']==1 and parsed['generation']==1,'invalid_ip_inner_frame')
                        expected=FrameKind.ACK if parsed['kind']==KIND_ACK else FrameKind.DATA
                        require(verified.kind is expected,'ip_authenticated_lane_mismatch')
                    except IpError:
                        self.counts['rx_refused']+=1;continue
                    at=self.clock()[1]/1e6;self.link.now=at
                    self.flow.absorb(parsed,at);self.counts['rx_'+verified.kind.name]+=1
                    self.events.append({'event':'rx','kind':verified.kind.name,'counter':verified.counter})
                if not self.sends and self.flow.delivered:
                    data=self.flow.consume(len(self.flow.delivered))
                    require(len(self.received)+len(data)<=self.expected_bytes,'ip_receive_limit')
                    self.received.extend(data)
                self.link.now=self.clock()[1]/1e6
                self.flow.pump(self.link.now,max_frames=self.authority.grant.limits.window_frames,drain=False)
                require(not self.flow.failure,'ip_reliable_flow_failed')
                self.authority.require_dispatch()
                return self.flow.complete
        except BaseException:self.close();raise

    def close(self):
        if not self.closed:self.closed=True;self.flow.cancel()
        # The dispatcher owns the worker, journal and session store lifecycle.

    def snapshot(self):
        return dict(transport='tcp',complete=self.flow.complete,received_bytes=len(self.received),
          counts=dict(self.counts),retransmits=self.flow.retransmits,acked_frames=self.flow.acked_frames(),
          accounting=self.worker.journal.snapshot(),application_acceptance=False)
