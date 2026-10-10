# SPDX-License-Identifier: Apache-2.0
"""Finite public service loop over an already admitted, caller-owned flow.

Native application policy and private capture belong to the consuming project.
This module opens no backend, device, socket or grant. Its public entry point
refuses protected messages before dispatch; a receive is still not publisher
verification. Worker ownership, disarm and outstanding debt stay with the owner.
"""
from ..contracts.identity import ServiceKind
from .services import ServiceTransfer
from .wire import require


class _ServiceLoop:
    """Shared loop mechanics for explicit application adapters.

    An application subclass must supply its admission and settlement hooks.
    This internal base is not a private-service provider or an enable switch.
    """
    def __init__(self, transfer, *, payload=None):
        require(type(transfer) is ServiceTransfer, 'invalid_service_runner')
        require((transfer.flow.sends and type(payload) is bytes)
                or (not transfer.flow.sends and payload is None), 'invalid_service_runner_direction')
        self.transfer, self.payload = transfer, payload
        self.state, self.wire_complete, self.application_result = 'created', False, None
        self.public_payload = None

    def _prepare_receive(self):
        pass

    def _send(self):
        self.transfer.send(self.payload)

    def _dispatch(self):
        return self.transfer.flow.step()

    def _receive(self):
        self.public_payload = self.transfer.received()

    def _close_application(self):
        return True

    def start(self):
        require(self.state == 'created', 'service_runner_not_fresh')
        try:
            self.transfer._check()
            self._prepare_receive()
            if self.transfer.flow.sends:
                self._send()
                self.payload = None
            self.state = 'running'
        except BaseException:
            self.state = 'failed'
            self.transfer.cancel()
            raise

    def step(self):
        require(self.state in ('running', 'wire_complete'), 'service_runner_not_running')
        try:
            self.transfer._check()
            done = self._dispatch()
            if done and not self.wire_complete:
                self.wire_complete = True
                if not self.transfer.flow.sends:
                    self._receive()
                self.state = 'wire_complete'
            return self.wire_complete
        except BaseException:
            self.state = 'failed'
            self.transfer.cancel()
            raise

    def close(self):
        self.transfer.cancel()
        self.payload = self.public_payload = None
        if self.state not in ('application_settled', 'wire_complete', 'failed'):
            self.state = 'cancelled'
        return self._close_application()

    def snapshot(self):
        return dict(state=self.state, wire_complete=self.wire_complete,
            application_status=self.application_result.status if self.application_result else None,
            service_kind=self.transfer.kind.value)


class ServiceRunner(_ServiceLoop):
    """Public resources only; private application integration is external."""
    def __init__(self, transfer, *, payload=None):
        require(type(transfer) is ServiceTransfer and transfer.kind is ServiceKind.KNOWLEDGE,
                'private_runner_requires_external_application_adapter')
        super().__init__(transfer, payload=payload)
