"""Inert, versioned same-boot continuation proposal. This creates no authority.

Only an initial extension from a <=1024 baseline is modeled; records cannot
chain automatically. The legacy RadioAdmission and RunAuthority deliberately do not accept this
record yet. An approved integration must additionally query current STATUS and
BURST_STATUS through the exclusively held native channel before ARM.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import struct
from .serial_protocol import Frame, FirmwareIdentity, Message, RadioProfile
from .wire import require

SCHEMA = 'meshticulum.same-boot-continuation.v1'
CONTEXT_FIELDS = ('schema', 'initiator', 'responder', 'session_id', 'generation', 'profile',
                  'object', 'sender_unit', 'country', 'antenna_confirmed',
                  'max_frames', 'operation_limits', 'data_burst',
                  'issued_epoch_us', 'expires_epoch_us')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')

def context_digest(plan):
    require(type(plan) is dict and plan.get('schema') == 'meshticulum.bench-run.v1',
            'invalid_continuation_plan')
    optional = {'file_capability', 'turn_end_capability'}
    endpoint_fields = {'unit', 'host_boot_id', 'local_peer', 'boot_continuation'}
    require(set(plan) <= set(CONTEXT_FIELDS) | optional | endpoint_fields,
            'unrecognized_continuation_plan_field')
    value = {name: plan[name] for name in CONTEXT_FIELDS}
    for name in sorted(optional & set(plan)):
        value[name] = plan[name]
    return hashlib.sha256(b'meshticulum.same-boot-context.v1\0' + canonical(value)).hexdigest()

def _uint(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum

def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None

@dataclass(frozen=True, slots=True)
class ContinuationRecord:
    """Immutable checked bytes, not a native permission or approval signature."""
    raw: bytes

    def __post_init__(self):
        require(type(self.raw) is bytes and 0 < len(self.raw) <= 4096, 'invalid_continuation_bytes')
        value = json.loads(self.raw)
        require(type(value) is dict and canonical(value) == self.raw, 'noncanonical_continuation')
        require(set(value) == {'schema', 'original_ceiling', 'absolute_ceiling',
            'cumulative_airtime_us', 'baseline', 'operation_limits', 'history_sha256',
            'approval_sha256', 'context_sha256', 'prior_profile_sha256'}, 'invalid_continuation_fields')
        require(value['schema'] == SCHEMA and type(value['original_ceiling']) is int
                and value['original_ceiling'] == 1024, 'invalid_continuation_origin')
        baseline = value['baseline']
        require(type(baseline) is dict and set(baseline) == {'mcu_boot', 'firmware', 'usb_serial',
            'boot_attempts', 'boot_reserved_estimated_airtime_us', 'dropped_receive'}, 'invalid_continuation_baseline')
        require(type(baseline['mcu_boot']) is str and re.fullmatch('[0-9a-f]{32}', baseline['mcu_boot'])
                and int(baseline['mcu_boot'][:16], 16) > 0 and int(baseline['mcu_boot'][16:], 16) > 0,
                'invalid_continuation_boot')
        require(type(baseline['firmware']) is str and re.fullmatch('mtb1-[0-9a-f]{10}', baseline['firmware'])
                and type(baseline['usb_serial']) is str and re.fullmatch('(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', baseline['usb_serial']),
                'invalid_continuation_identity')
        require(_uint(baseline['boot_attempts'], 1, 1024)
                and _uint(baseline['boot_reserved_estimated_airtime_us'], 1, 300000000)
                and type(baseline['dropped_receive']) is int and baseline['dropped_receive'] == 0,
                'invalid_continuation_debt')
        limits = value['operation_limits']
        require(type(limits) is dict and set(limits) == {'sender', 'receiver'}, 'invalid_continuation_limits')
        for row in limits.values():
            require(type(row) is dict and set(row) == {'attempts', 'estimated_airtime_us'}
                    and _uint(row['attempts'], 1, 1024) and _uint(row['estimated_airtime_us'], 1, 300000000),
                    'invalid_continuation_limits')
        require(_uint(value['absolute_ceiling'], 1025, 100000)
                and value['absolute_ceiling'] == baseline['boot_attempts'] + limits['sender']['attempts'],
                'continuation_ceiling_not_exact')
        require(type(value['cumulative_airtime_us']) is int and value['cumulative_airtime_us'] == 300000000
                and baseline['boot_reserved_estimated_airtime_us'] + limits['sender']['estimated_airtime_us'] <= 300000000,
                'continuation_airtime_exceeded')
        require(all(_sha(value[name]) for name in ('history_sha256', 'approval_sha256', 'context_sha256', 'prior_profile_sha256')),
                'invalid_continuation_evidence_pin')

    def value(self):
        return json.loads(self.raw)

    def check_plan(self, plan):
        """Bind both endpoints' shared context; never modify the grant or profile."""
        value = self.value()
        if type(plan) is dict and 'boot_continuation' in plan:
            require(canonical(plan['boot_continuation']) == self.raw, 'continuation_record_changed')
        require(type(plan) is dict and plan.get('radio_modem') is None
                and context_digest(plan) == value['context_sha256'], 'continuation_context_changed')
        require(plan['operation_limits'] == value['operation_limits']
                and plan['profile']['max_boot_attempts'] == value['absolute_ceiling']
                and plan['profile']['max_boot_estimated_airtime_us'] == value['cumulative_airtime_us'],
                'continuation_plan_limits_changed')
        RadioProfile(**plan['profile']).encode(board_id=1)

    def check_status(self, frame, *, request, identity, held_usb_serial):
        """Check bound read-only STATUS data. Does not prove channel freshness.

        Caller must supply a new request from the held channel, then independently
        prove empty BURST_STATUS and live owner/deadline immediately before ARM.
        This result deliberately is not an admission token.
        """
        value = self.value(); baseline = value['baseline']
        require(type(identity) is FirmwareIdentity and identity.board_id == 1
                and identity.boot.hex() == baseline['mcu_boot'] and identity.version == baseline['firmware']
                and held_usb_serial == baseline['usb_serial'], 'continuation_native_identity_changed')
        require(type(frame) is Frame and frame.type is Message.STATUS_REPLY
                and _uint(request, 1, 2**64-1) and frame.request == request and frame.boot == identity.boot
                and len(frame.body) == 49, 'unbound_continuation_status')
        require(hashlib.sha256(frame.body[:30]).hexdigest() == value['prior_profile_sha256'],
                'continuation_prior_profile_changed')
        attempts, airtime, drops = struct.unpack('!IQI', frame.body[30:46])
        require(frame.body[46:] == b'\0\0\0' and attempts == baseline['boot_attempts']
                and airtime == baseline['boot_reserved_estimated_airtime_us'] and drops == baseline['dropped_receive'],
                'continuation_native_debt_or_state_changed')
