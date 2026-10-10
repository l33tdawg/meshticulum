# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Opt-in finite service adapters over the existing authenticated worker flow.

The provisioner admits exact service, format, object and original expiry in a
service-run grant. These adapters create no listener, grant, worker or device
window. RF completion/accounting stays with BenchFlow and its exclusive worker;
application admission is a separate result. Production private policy, durable
capture reconciliation, routing and automatic capability discovery are outside
this source increment. Private payloads never go to VerifiedChunks or BlockServer.
"""
import hashlib

from meshticulum.resources import resource

from ..contracts.identity import ServiceKind
from .authority import RunAuthority
from .flow_runtime import BenchFlow
from .wire import require


def _shape(payload, format):
    if format == 'LME.binary':
        from meshticulum.privacy.mesh_binary_envelope import to_text
        to_text(payload)  # Shape only. Recipient signature/expiry checks follow.
    elif format == 'MR1.request':
        resource.decode_request(payload)
    else:
        require(format == 'MR1.response' and len(payload) >= resource.RESPONSE.size,
                'invalid_service_format')
        magic, _, status, total, offset = resource.RESPONSE.unpack(payload[:resource.RESPONSE.size])
        data = payload[resource.RESPONSE.size:]
        require(magic == resource.MAGIC and 0 <= offset < resource.MAX_RESOURCE,
                'invalid_service_response')
        require((status == 1 and total == 0 and not data) or
                (status == 0 and 0 < total <= resource.MAX_RESOURCE and offset < total
                 and 0 < len(data) <= min(resource.MAX_BLOCK, total - offset)),
                'invalid_service_response')


class ServiceTransfer:
    """One finite object with explicit private or public application use.

    The caller retains a stable capture UUID and the protected inbox until any
    uncertain admission is reconciled. close/cancel retires host queues; it is
    not an RF drain or a refund of durable attempts and lifetime airtime debt.
    """
    def __init__(self, flow):
        if type(flow) is BenchFlow:
            authority = flow.authority
            require(type(authority) is RunAuthority and authority.service_format is not None
                    and not flow.owner.retained and not flow.received and not flow.events
                    and not flow.flow.queued_bytes(), 'service_requires_fresh_flow')
            require(authority.contract.route.service.kind is authority.service_kind,
                    'service_flow_binding_mismatch')
        else:
            # Optional source carrier. The frozen radio runtime stays independent
            # of IP packaging and never enables a socket through this import.
            try:
                from ..ip.authority import IpAuthority
                from ..ip.flow import IpFlow
            except ImportError:
                require(False, 'ip_carrier_not_in_runtime')
            require(type(flow) is IpFlow, 'invalid_service_ip_flow')
            authority = flow.authority
            require(type(flow) is IpFlow and type(authority) is IpAuthority and flow.service_fresh()
                    and authority.service_kind is authority.grant.service_kind,
                    'invalid_service_ip_flow')
        require(getattr(flow, '_service_adapter', None) is None
                and flow.cipher.grant is authority.grant, 'service_flow_binding_mismatch')
        self.flow, self.authority = flow, authority
        self.kind, self.format = authority.service_kind, authority.service_format
        self.descriptor = (authority.plan['object']['bytes'], authority.plan['object']['sha256'])
        self.grant_digest = authority.grant.digest
        self.started, self.cancelled = False, False
        flow._service_adapter = self

    def live(self):
        return (not self.cancelled and not self.flow.closed
                and self.flow.authority is self.authority
                and self.authority.service_kind is self.kind
                and self.authority.service_format == self.format
                and self.authority.grant.digest == self.grant_digest
                and self.authority.application_live())

    def _check(self):
        require(self.live(), 'service_transfer_not_live')

    def _payload(self, payload):
        require(type(payload) is bytes and len(payload) == self.descriptor[0]
                and hashlib.sha256(payload).hexdigest() == self.descriptor[1],
                'service_object_mismatch')
        _shape(payload, self.format)
        return payload

    def send(self, payload):
        require(self.flow.sends and not self.started, 'service_send_not_fresh')
        with self.authority.application_lease():
            self._check()
            self._payload(payload)
            self.started = True
            self.flow.send(payload)

    def _received(self):
        self._check()
        require(not self.flow.sends and self.flow.flow.complete and not self.flow.flow.failure,
                'service_receive_incomplete')
        return self._payload(bytes(self.flow.received))

    def received(self):
        with self.authority.application_lease():
            return self._received()

    def serve_public(self, server):
        """Use only a caller-owned verified public-resource allowlist."""
        require(self.kind is ServiceKind.KNOWLEDGE and self.format == 'MR1.request'
                and type(server) is resource.BlockServer, 'public_service_binding_refused')
        with self.authority.application_lease():
            request = self._received()
            result = server.handle(request)
            _shape(result, 'MR1.response')
            self._check()
            return result

    def cancel(self):
        self.cancelled = True
        self.flow.close()


class PreparedBlockExchange:
    """A pre-admitted request/reply pair usable by the existing BlockReader.

    pump() advances the caller's existing bounded flow/worker loop on both
    endpoints. The reply sender and verified public store are separately owned.
    Both directions need fresh independent grants and preserve the original
    resource deadline. No arbitrary request can renew or change an admitted
    object. One pair serves exactly one bounded block request.
    """
    def __init__(self, request, response, *, pump):
        require(type(request) is ServiceTransfer and type(response) is ServiceTransfer
                and request.format == 'MR1.request' and response.format == 'MR1.response'
                and request.flow.sends and not response.flow.sends and callable(pump),
                'invalid_block_flow_pair')
        a, b = request.authority.grant.session, response.authority.grant.session
        require(a.initiator == b.responder and a.responder == b.initiator
                and a.session_id != b.session_id and a.session_generation != b.session_generation
                and request.authority.application_expires_epoch_us ==
                    response.authority.application_expires_epoch_us,
                'block_flow_binding_mismatch')
        self.request, self.response, self.pump = request, response, pump
        self.used = False

    def __call__(self, payload, limit, deadline):
        require(not self.used and type(limit) is int
                and resource.RESPONSE.size <= self.response.descriptor[0] <= limit <= self.response.flow.maximum_bytes
                and type(deadline) in (int, float) and 0 < deadline < float('inf'),
                'invalid_block_exchange')
        _, _, requested_size = resource.decode_request(payload)
        require(self.response.descriptor[0] <= resource.RESPONSE.size + requested_size,
                'block_exchange_response_limit')
        # Polling alone cannot shorten the physical dispatch lease. Refuse a
        # tighter caller deadline until both directions have fresh admission
        # with bounds no later than that original resource deadline.
        require(all(t.authority.deadlines.application_deadline_monotonic <= deadline
                    for t in (self.request, self.response)), 'block_requires_deadline_bound_admission')
        bound = deadline
        self.used = True
        try:
            self._live(bound)
            self.request.send(payload)
            while not (self.request.flow.flow.complete and self.response.flow.flow.complete):
                self._live(bound)
                self.pump()
                self._live(bound)
            reply = self.response.received()
            require(len(reply) <= limit, 'block_exchange_response_limit')
            _, offset, size = resource.decode_request(payload)
            magic, binding, status, total, at = resource.RESPONSE.unpack(reply[:resource.RESPONSE.size])
            require(magic == resource.MAGIC and binding == hashlib.sha256(payload).digest()[:16]
                    and at == offset and len(reply) <= resource.RESPONSE.size + size,
                    'block_exchange_response_mismatch')
            require(status == 1 or len(reply) - resource.RESPONSE.size == min(size, total - offset),
                    'block_exchange_response_mismatch')
            return reply
        finally:
            # Successful receive and cancellation both retire queued work;
            # the worker/journal lifetime remains the caller's responsibility.
            try:
                self.request.cancel()
            finally:
                self.response.cancel()

    def _live(self, deadline):
        self.request._check()
        self.response._check()
        require(all(t.flow.clock()[1] / 1000000 < deadline
                    for t in (self.request, self.response)), 'block_exchange_deadline')
