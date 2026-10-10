"""Live signed SQLite storage observations from an operator-pinned local SAGE.

This does not enroll identities, load keys, grant recipient consent or enable
messaging. The configured endpoint-to-chain binding is operator responsibility.
Snapshots describe current new-write encryption, not historical migration or
future availability. Compare the epoch before and after an operation; a changed
epoch makes a dispatched outcome uncertain. Never cache a positive observation.
"""

from dataclasses import dataclass, replace
import math
import re

from .community_messaging import MessagingUnavailable, Prerequisites
from .message_contacts import RecipientID


@dataclass(frozen=True)
class MessageStorageSnapshot:
    instance_id: str
    vault_generation: str

    @property
    def epoch(self):
        return self.instance_id + ":" + self.vault_generation


class MessageStorageProbe:
    def __init__(self, client, *, actor, endpoint):
        if (type(actor) is not RecipientID or type(endpoint) is not str
                or re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", endpoint) is None
                or not 1 <= int(endpoint.rsplit(':', 1)[1]) <= 65535):
            raise MessagingUnavailable()
        self.client = client
        self.actor = actor
        self.endpoint = endpoint
        self._transport = client._client
        self._identity = client._identity
        self._binding()

    def _binding(self):
        try:
            transport = self.client._client
            timeouts = transport.timeout.as_dict()
            if (transport is not self._transport or self.client._identity is not self._identity
                    or self._identity.agent_id != self.actor.agent_id
                    or self.client._base_url != self.endpoint
                    or str(transport.base_url).rstrip('/') != self.endpoint
                    or transport.trust_env is not False or transport.follow_redirects is not False
                    or type(timeouts) is not dict or set(timeouts) != {'connect', 'read', 'write', 'pool'}
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           or not 0 < value <= 5 for value in timeouts.values())):
                raise MessagingUnavailable()
        except Exception:
            raise MessagingUnavailable() from None

    def snapshot(self):
        try:
            self._binding()
            value = self.client.message_storage_status()
            self._binding()
            if hasattr(value, 'model_dump'):
                value = value.model_dump(mode='json')
            fields = {'schema', 'instance_id', 'agent_id', 'encryption_expected',
                      'vault_active', 'vault_generation', 'stable', 'canonical_send_idempotency', 'encrypted_send_admission'}
            if (type(value) is not dict or set(value) != fields
                    or value['schema'] != 'sage.message-storage.v1'
                    or value['agent_id'] != self.actor.agent_id
                    or type(value['instance_id']) is not str
                    or re.fullmatch(r'[0-9a-f]{64}', value['instance_id']) is None
                    or type(value['vault_generation']) is not str
                    or re.fullmatch(r'[1-9][0-9]{0,19}', value['vault_generation']) is None
                    or int(value['vault_generation']) > (1 << 64) - 1
                    or any(value[field] is not True for field in
                           ('encryption_expected', 'vault_active', 'stable', 'canonical_send_idempotency', 'encrypted_send_admission'))):
                raise MessagingUnavailable()
            return MessageStorageSnapshot(value['instance_id'], value['vault_generation'])
        except Exception:
            raise MessagingUnavailable() from None

    def prerequisites(self, base):
        if type(base) is not Prerequisites or base.actor != self.actor:
            raise MessagingUnavailable()
        snapshot = self.snapshot()
        return replace(base, encrypted_storage_verified=True, storage_epoch=snapshot.epoch)
