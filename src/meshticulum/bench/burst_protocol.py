"""Exact local serial records for opt-in, bounded LoRa DATA handover.

Parsing proves shape and binding, not peer authentication or RF authority.
The exclusive owner, immutable grant and durable physical journal remain
separate requirements. Clock conversion assumes the declared minimum rate.
"""
from dataclasses import dataclass
import struct

from .serial_protocol import Frame, Message
from .tx_burst import (Binding, BurstContractError, MAX_AUTONOMOUS_HOLD_US,
                       MAX_DEPTH, PacketDescriptor, PacketEvidence, Stage,
                       START_GUARD_US, UINT64_MAX, _bytes, _integer, _need)

INTER_PACKET_RX_US = 5_000
REVERSE_LISTEN_US = 100_000
MAX_CLOCK_RTT_US = 50_000
CLOCK_MARGIN_US = 20_000
MIN_MCU_RATE_PPM = 990_000
CAPABILITY_STRUCT = struct.Struct('!4sBBHIIII')
CAPABILITY = CAPABILITY_STRUCT.pack(b'MBQ1', MAX_DEPTH, 7, 255,
    MAX_AUTONOMOUS_HOLD_US, START_GUARD_US, INTER_PACKET_RX_US, REVERSE_LISTEN_US)
CAPABILITY8 = CAPABILITY_STRUCT.pack(b'MBQ8', 8, 7, 255,
    MAX_AUTONOMOUS_HOLD_US, START_GUARD_US, INTER_PACKET_RX_US, REVERSE_LISTEN_US)
CAPABILITY_TRAIN_STRUCT = struct.Struct('!4sBBBHIIII')
CAPABILITY12 = CAPABILITY_TRAIN_STRUCT.pack(b'MBT1', 8, 12, 7, 255,
    MAX_AUTONOMOUS_HOLD_US, START_GUARD_US, INTER_PACKET_RX_US, REVERSE_LISTEN_US)
CLOCK = struct.Struct('!QQQ')
OPEN = struct.Struct('!32sQQ')
DATA = struct.Struct('!QQQBH32s')
EVENT = struct.Struct('!QBQ32sQHQQIh')
ACK = struct.Struct('!QQBQ32s')
CANCEL = struct.Struct('!QBB')
STATUS = struct.Struct('!Q32sQQBBBBBQQIQ')
TOKEN = struct.Struct('!Q')

POLICY = dict(schema='meshticulum.data-burst-policy.v1',
    handover='irrevocably-charged-at-queue-acceptance', max_depth=MAX_DEPTH,
    max_hold_us=MAX_AUTONOMOUS_HOLD_US, inter_packet_rx_us=INTER_PACKET_RX_US,
    reverse_listen_us=REVERSE_LISTEN_US)
_POLICY_ITEMS = tuple(POLICY.items())
POLICY8 = dict(POLICY, schema='meshticulum.data-burst-policy.v2',
               max_depth=8, train_frames=8)
_POLICY8_ITEMS = tuple(POLICY8.items())
POLICY32 = dict(POLICY8, schema='meshticulum.data-burst-policy.v3',
                flow_window_frames=32)
_POLICY32_ITEMS = tuple(POLICY32.items())
POLICY_REPAIR_FILL = dict(POLICY32, schema='meshticulum.data-burst-policy.v4',
                         selective_repair_fill=True)
_POLICY_REPAIR_FILL_ITEMS = tuple(POLICY_REPAIR_FILL.items())
POLICY_TERMINAL_RETRY = dict(POLICY_REPAIR_FILL, schema='meshticulum.data-burst-policy.v5',
                            selective_terminal_retry=True)
_POLICY_TERMINAL_RETRY_ITEMS = tuple(POLICY_TERMINAL_RETRY.items())
POLICY_SCALAR_FILL_PAIR = dict(POLICY_TERMINAL_RETRY, schema='meshticulum.data-burst-policy.v6',
                              scalar_fill_pair=True)
_POLICY_SCALAR_FILL_PAIR_ITEMS = tuple(POLICY_SCALAR_FILL_PAIR.items())
POLICY_REPEAT_RETRY = dict(POLICY_SCALAR_FILL_PAIR, schema='meshticulum.data-burst-policy.v7',
                           selective_repeat_retry=True)
_POLICY_REPEAT_RETRY_ITEMS = tuple(POLICY_REPEAT_RETRY.items())
POLICY_REPAIR_TRAIN = dict(POLICY_REPEAT_RETRY, schema='meshticulum.data-burst-policy.v8',
                           selective_repair_train=True)
_POLICY_REPAIR_TRAIN_ITEMS = tuple(POLICY_REPAIR_TRAIN.items())
POLICY_TRAIN12 = dict(POLICY_REPAIR_TRAIN, schema='meshticulum.data-burst-policy.v9',
                      train_frames=12)
_POLICY_TRAIN12_ITEMS = tuple(POLICY_TRAIN12.items())


def validate_policy(value):
    _need(type(value) is dict and any(
          set(value) == {key for key, _ in items}
          and all(type(value[key]) is type(expected) and value[key] == expected
                  for key, expected in items)
          for items in (_POLICY_ITEMS, _POLICY8_ITEMS, _POLICY32_ITEMS,
                        _POLICY_REPAIR_FILL_ITEMS, _POLICY_TERMINAL_RETRY_ITEMS,
                        _POLICY_SCALAR_FILL_PAIR_ITEMS, _POLICY_REPEAT_RETRY_ITEMS,
                        _POLICY_REPAIR_TRAIN_ITEMS, _POLICY_TRAIN12_ITEMS)), 'invalid_data_burst_policy')
    return dict(value)


def policy_for_depth(depth=4):
    """Select an explicit bounded scalar policy; this creates no authority."""
    _need(type(depth) is int and depth in (4, 8), 'invalid_data_burst_depth')
    return dict(_POLICY_ITEMS if depth == 4 else _POLICY8_ITEMS)


def policy_depth(policy):
    return validate_policy(policy)['max_depth']


def policy_train_frames(policy):
    value = validate_policy(policy)
    return value.get('train_frames', value['max_depth'])


def policy_for_train12():
    """Explicit twelve-frame train; retained native storage remains eight slots."""
    return dict(_POLICY_TRAIN12_ITEMS)


def policy_for_flow_window(flow_window_frames):
    """Explicit logical-32/physical-eight policy; existing depth defaults stay unchanged."""
    _need(type(flow_window_frames) is int and flow_window_frames == 32,
          'invalid_data_burst_flow_window')
    return dict(_POLICY32_ITEMS)


def policy_flow_window(policy):
    value = validate_policy(policy)
    return value.get('flow_window_frames', value['max_depth'])


def policy_for_repair_fill():
    """Explicit logical-32/eight-train policy allowing fresh DATA after one repair."""
    return dict(_POLICY_REPAIR_FILL_ITEMS)


def policy_repair_fill(policy):
    return validate_policy(policy).get('selective_repair_fill', False)


def policy_for_terminal_retry():
    """Explicit terminal recovery after local FINAL handover and peer receipt."""
    return dict(_POLICY_TERMINAL_RETRY_ITEMS)


def policy_terminal_retry(policy):
    return validate_policy(policy).get('selective_terminal_retry', False)


def policy_for_scalar_fill_pair():
    """Explicit logical-32/eight-train admission for a bounded fresh scalar pair."""
    return dict(_POLICY_SCALAR_FILL_PAIR_ITEMS)


def policy_scalar_fill_pair(policy):
    return validate_policy(policy).get('scalar_fill_pair', False)


def policy_for_repeat_retry():
    """Explicit v7 admission for fresh proof after a confirmed retry handover."""
    return dict(_POLICY_REPEAT_RETRY_ITEMS)


def policy_repeat_retry(policy):
    return validate_policy(policy).get('selective_repeat_retry', False)


def policy_for_repair_train():
    """Explicit v8 admission for distinct proven holes after peer FINAL receipt."""
    return dict(_POLICY_REPAIR_TRAIN_ITEMS)


def policy_repair_train(policy):
    return validate_policy(policy).get('selective_repair_train', False)


def _train_shape(depth, train_frames):
    policy_for_depth(depth)
    train_frames = depth if train_frames is None else train_frames
    _need(type(train_frames) is int and (depth, train_frames) in ((4, 4), (8, 8), (8, 12)),
          'invalid_data_burst_train')
    return train_frames


def capability_for_depth(depth=4, *, train_frames=None):
    train_frames = _train_shape(depth, train_frames)
    if train_frames == 12:
        return CAPABILITY12
    return CAPABILITY if depth == 4 else CAPABILITY8


def capability_request_body(depth=4, *, train_frames=None):
    train_frames = _train_shape(depth, train_frames)
    if train_frames == 12:
        return b'\x08\x0c'
    return b'' if depth == 4 else b'\x08'


def _reply(frame, kind, boot, request, size):
    _bytes(boot, 16, 'invalid_burst_boot')
    _need(any(boot), 'empty_burst_boot')
    _need(type(frame) is Frame and frame.type is kind and frame.boot == boot
          and frame.request > 0 and (request is None or frame.request == request)
          and len(frame.body) == size, 'unbound_burst_reply')


def check_capability(frame, *, boot, request, depth=4, train_frames=None):
    expected = capability_for_depth(depth, train_frames=train_frames)
    _reply(frame, Message.BURST_CAPABILITIES_REPLY, boot, request, len(expected))
    _need(frame.body == expected, 'unsupported_burst_capability')


@dataclass(frozen=True, slots=True)
class ClockSnapshot:
    now_us: int
    arm_request: int
    arm_end_us: int

    def __post_init__(self):
        _integer(self.now_us, 1, UINT64_MAX, 'invalid_burst_clock_snapshot')
        for value in (self.arm_request, self.arm_end_us):
            _integer(value, 0, UINT64_MAX, 'invalid_burst_clock_snapshot')
        _need((self.arm_request == 0) == (self.arm_end_us == 0),
              'invalid_burst_clock_snapshot')

    @classmethod
    def from_frame(cls, frame, *, boot, request):
        _reply(frame, Message.BURST_CLOCK_REPLY, boot, request, CLOCK.size)
        value = cls(*CLOCK.unpack(frame.body))
        _need(value.now_us > 0 and ((value.arm_request == 0) == (value.arm_end_us == 0)),
              'invalid_burst_clock_snapshot')
        return value

    def map_deadline(self, *, host_deadline_us, sent_us, received_us, arm_request,
                     original_cap_us=None, burst=False):
        """Narrow under the explicit >=0.99 MCU/host clock-rate assumption."""
        for value in (host_deadline_us, sent_us, received_us, arm_request):
            _integer(value, 0, UINT64_MAX, 'invalid_burst_clock_mapping')
        _need(type(burst) is bool and sent_us <= received_us
              and received_us - sent_us <= MAX_CLOCK_RTT_US,
              'burst_clock_sampling_envelope')
        _need(arm_request > 0 and self.arm_request == arm_request
              and self.now_us < self.arm_end_us, 'burst_arm_clock_changed')
        remaining = host_deadline_us - received_us
        _need(remaining > 0, 'burst_authority_expired')
        mapped = self.now_us + remaining * MIN_MCU_RATE_PPM // 1_000_000 - CLOCK_MARGIN_US
        end = min(mapped, self.arm_end_us)
        if original_cap_us is not None:
            _integer(original_cap_us, 1, UINT64_MAX, 'invalid_original_burst_cap')
            end = min(end, original_cap_us)
        if burst:
            end = min(end, self.now_us + MAX_AUTONOMOUS_HOLD_US - CLOCK_MARGIN_US)
        _need(self.now_us < end <= UINT64_MAX, 'burst_authority_expired')
        return end


@dataclass(frozen=True, slots=True)
class OpenBinding:
    token: int
    context_sha256: bytes
    arm_request: int
    deadline_us: int
    depth: int = 4
    train_frames: int | None = None

    def __post_init__(self):
        _train_shape(self.depth, self.train_frames)
        for value in (self.token, self.arm_request, self.deadline_us):
            _integer(value, 1, UINT64_MAX, 'invalid_burst_open')
        _bytes(self.context_sha256, 32, 'invalid_burst_context')
        _need(any(self.context_sha256), 'empty_burst_context')

    @classmethod
    def from_frame(cls, frame, *, boot, request, expected_body=None, depth=4, train_frames=None):
        prefix = capability_request_body(depth, train_frames=train_frames)
        _reply(frame, Message.BURST_OPEN_REPLY, boot, request, OPEN.size + len(prefix))
        _need(expected_body is None or frame.body == expected_body, 'burst_open_changed')
        _need(frame.body[:len(prefix)] == prefix, 'burst_open_changed')
        return cls(frame.request, *OPEN.unpack(frame.body[len(prefix):]), depth, train_frames)


def open_frame(boot, token, context_sha256, arm_request, deadline_us, *, depth=4, train_frames=None):
    value = OpenBinding(token, context_sha256, arm_request, deadline_us, depth, train_frames)
    return Frame(Message.BURST_OPEN, boot, token,
                 capability_request_body(depth, train_frames=train_frames)
                 + OPEN.pack(value.context_sha256, value.arm_request, value.deadline_us))


def data_frame(boot, token, packet, ciphertext):
    _integer(token, 1, UINT64_MAX, 'invalid_burst_open_token')
    _need(type(packet) is PacketDescriptor, 'invalid_burst_packet_descriptor')
    packet.check_ciphertext(ciphertext)
    return Frame(Message.BURST_DATA, boot, packet.request,
                 DATA.pack(token, packet.deadline_us, packet.estimated_airtime_us,
                           packet.kind, packet.size, packet.digest) + ciphertext)


def query_frame(boot, token, request):
    _integer(token, 1, UINT64_MAX, 'invalid_burst_open_token')
    _integer(request, 1, UINT64_MAX, 'invalid_burst_request')
    return Frame(Message.BURST_QUERY, boot, request, TOKEN.pack(token))


@dataclass(frozen=True, slots=True)
class BurstEvent:
    request: int
    token: int
    stage: Stage
    at_us: int
    digest: bytes
    estimated_airtime_us: int
    size: int
    deadline_us: int
    driver_elapsed_us: int
    irq_flags: int
    driver_error: int

    @classmethod
    def from_frame(cls, frame, *, boot, request=None):
        _reply(frame, Message.BURST_EVENT, boot, request, EVENT.size)
        values = list(EVENT.unpack(frame.body))
        try:
            values[1] = Stage(values[1])
        except ValueError as error:
            raise BurstContractError('invalid_burst_event_stage') from error
        value = cls(frame.request, *values)
        _integer(value.token, 1, UINT64_MAX, 'invalid_burst_open_token')
        value.evidence(Binding(boot, b'\x01' * 32))  # Shape only; caller binds real context.
        return value

    @property
    def packet(self):
        return PacketDescriptor(self.request, self.size, self.digest,
                                self.estimated_airtime_us, self.deadline_us)

    def check_packet(self, packet, *, token):
        _need(self.token == token and self.packet == packet, 'burst_event_packet_changed')

    def evidence(self, binding):
        _integer(self.driver_elapsed_us, 0, UINT64_MAX, 'invalid_burst_driver_elapsed')
        _integer(self.irq_flags, 0, (1 << 32) - 1, 'invalid_burst_driver_irq')
        _integer(self.driver_error, -(1 << 15), (1 << 15) - 1, 'invalid_burst_driver_error')
        if self.stage in (Stage.STARTED, Stage.RF_DONE):
            driver = self.driver_elapsed_us, self.irq_flags, self.driver_error
        else:
            _need(self.stage is Stage.UNKNOWN
                  or (self.driver_elapsed_us, self.irq_flags, self.driver_error) == (0, 0, 0),
                  'unexpected_burst_driver_evidence')
            driver = None, None, None
        return PacketEvidence(binding, self.packet, self.stage, self.at_us, *driver)


def ack_frame(boot, command_request, event):
    _need(type(event) is BurstEvent and event.stage in
          (Stage.RF_DONE, Stage.NOT_SENT, Stage.UNKNOWN), 'burst_ack_not_terminal')
    return Frame(Message.BURST_ACK, boot, command_request,
                 ACK.pack(event.token, event.request, event.stage, event.at_us, event.digest))


@dataclass(frozen=True, slots=True)
class BurstStatus:
    token: int
    context_sha256: bytes
    arm_request: int
    open_deadline_us: int
    pending: int
    retained: int
    transmitting: bool
    fenced: bool
    containment_required: bool
    now_us: int
    next_start_us: int
    attempts: int
    airtime_us: int

    @classmethod
    def from_frame(cls, frame, *, boot, request, depth=4):
        policy_for_depth(depth)
        _reply(frame, Message.BURST_STATUS_REPLY, boot, request, STATUS.size)
        values = list(STATUS.unpack(frame.body))
        _need(all(value in (0, 1) for value in values[6:9]), 'invalid_burst_status_flags')
        values[6:9] = map(bool, values[6:9])
        value = cls(*values)
        _need(0 <= value.pending <= value.retained <= depth
              and value.attempts <= 100_000 and value.airtime_us <= 300_000_000,
              'invalid_burst_status_counters')
        if value.token:
            OpenBinding(value.token, value.context_sha256, value.arm_request, value.open_deadline_us)
        else:
            _need(not any(value.context_sha256) and value.open_deadline_us == 0
                  and value.retained == 0, 'invalid_closed_burst_status')
        return value
