"""Offline companion USB client for MeshCore d92964352441e53b93e8667b802e04f6e072b39e.

Exclusive fresh serial ownership is required; commands have no transaction IDs.
The injected stream implements read(size, timeout) and write(bytes, timeout),
both returning within timeout. Empty reads mean EOF/timeout, never retry forever.
Uncertain IO poisons this client. Reopening is not permission to retry a send.
No discovery, contact modification, automatic receiver draining, logs or SAGE binding.
"""

import collections
from dataclasses import dataclass
import hashlib
import math
import os
import select
import struct
import threading
import time

from .airtime_budget import AirtimeSample


BAUD = 115200
MAX_FRAME = 176
MAX_TEXT = 128
# A per-packet RF log measurement stays reportable for five minutes, matching
# the advertisement/discovery freshness window used for radio observations.
PACKET_SIGNAL_WINDOW = 300
FIRMWARE = "v1.17.1-d929643"
# r3 is the pinned base plus the contacts-response atomicity fix (upstream PR
# #3403, the interleaved-push defect that poisoned contact reads) and the
# packet-RSSI capture. A radio admits itself by this exact string, so the host
# release that admits r3 ships before the flash.
# r4 is r3 plus the client-repeat frequency allowance for the commissioned
# 917.375 MHz channel (ALLOWED_REPEAT_FREQ_RANGE). It changes nothing on air
# until the radio's repeat bit is set through CMD_SET_RADIO_PARAMS; the host
# release that admits r4 still ships before the flash.
# r5 is r4 plus the forward-path airtime budget (upstream PR #3450): relayed
# airtime is accounted in a rolling window and the relay role is capped by the
# radio's own duty-cycle setting. The Lantern build defaults that cap to 10% of
# the window; like r4 it changes nothing on air until the repeat bit is set.
# r6 is r5 plus the companion bridge and its transport selection (upstream PRs
# #3410 and #3454): a packet whose single-hop destination is known to be on the
# 2.4 GHz lane goes over ESP-NOW only - the LoRa radio is not keyed for it - and
# everything else keeps the radio and is mirrored before the transmit. It also
# reports the bridge's own counters as stats sub-type 3, which this module reads
# for the firmware that serves it.
SUPPORTED_FIRMWARE = (FIRMWARE, "v1.17.1-d929643-r1", "v1.17.1-d929643-r2",
                      "v1.17.1-d929643-r3", "v1.17.1-d929643-r4",
                      "v1.17.1-d929643-r5", "v1.17.1-d929643-r6",
                      "v1.17.1-d929643-r7")
# Bridge counters are a sub-type of their own (STATS_TYPE_BRIDGE in the
# companion), not more fields on the frames above: a host that does not know it
# never asks, and a firmware without a bridge answers with an error frame. Only
# these builds serve it, so the read is gated on the exact version the way the
# rest of the transport gates capability.
# r7 retains r6's bridge protocol and backports the USB HWCDC transmit fix.
BRIDGE_STATS_FIRMWARE = ("v1.17.1-d929643-r6", "v1.17.1-d929643-r7")
STATS_TYPE_BRIDGE = 3
BRIDGE_STATS_LAYOUT = "<22I4B"
BRIDGE_STATS_NAMES = (
    "espnow_tx", "espnow_tx_announce", "espnow_tx_unicast", "espnow_tx_failed",
    "espnow_tx_done", "espnow_tx_error", "espnow_rx", "espnow_rx_announce",
    "espnow_rx_bad", "fastlane_tx", "mirror_tx", "fastlane_miss", "fastlane_fallback",
    "peer_unicast_failed", "fastlane_ack_tx", "rx_to_queue_last_ms", "rx_to_queue_max_ms",
    "inject_to_process_last_ms", "inject_to_process_max_ms", "dropped_dup",
    "dropped_other", "injected", "bridge_delay_ms", "peers_known", "running", "self_hash",
)
# Every response code >= 0x80 is an asynchronous push (PUSH_CODE_* in
# examples/companion_radio/MyMesh.cpp) and can arrive at any point, including in
# the middle of another command's response. The host owns no push callback, so
# before this map existed a push that landed inside an exchange was read and
# silently discarded: a dropped MSG_WAITING delayed a fetch, a dropped
# CONTACT_DELETED/CONTACTS_FULL left a stale directory until the next poll, and a
# dropped SEND_CONFIRMED destroyed the only in-band radio-ACK observation.
PUSH_CODES = {
    0x80: "advert",
    0x81: "path_updated",
    0x82: "send_confirmed",
    0x83: "messages_waiting",
    0x84: "raw_data",
    0x85: "login_success",
    0x86: "login_fail",
    0x87: "status_response",
    0x88: "log_rx_data",
    0x89: "trace_data",
    0x8A: "new_advert",
    0x8B: "telemetry_response",
    0x8C: "binary_response",
    0x8D: "path_discovery_response",
    0x8E: "control_data",
    0x8F: "contact_deleted",
    0x90: "contacts_full",
}
# Bounded, payload-free observation record. Pushes carry other people's traffic
# (contact messages, raw data, telemetry), so only code, size and clock are kept.
PUSH_EVENT_LIMIT = 64

# Read-only failures that leave the port mid-stream: the frame body was cut short
# (unit A's stock firmware stalls part-way through a 148-byte contact record) or
# the header itself was unparsable. Only these need a bounded resynchronisation;
# a refusal raised by the caller leaves the port on a frame boundary.
WIRE_RESYNC_CODES = ("serial_timeout", "serial_incomplete", "invalid_frame")
PROTOCOL_VERSION = 13


class MeshCoreError(RuntimeError):
    def __init__(self, code, *, outcome_unknown=False):
        self.code = code
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


class SerialFDStream:
    """Borrow an exclusively owned, already configured 115200 8N1 raw TTY FD.

    Does not open/configure/close devices or toggle DTR/RTS. Caller sets
    O_NONBLOCK and raw termios; this adapter verifies those prerequisites.
    """

    def __init__(self, descriptor):
        import fcntl
        import termios
        if type(descriptor) is not int or not os.isatty(descriptor):
            raise MeshCoreError("invalid_serial_descriptor")
        settings = termios.tcgetattr(descriptor)
        if (not fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_NONBLOCK
                or settings[4:6] != [termios.B115200, termios.B115200]
                or settings[0] != 0 or settings[1] != 0 or settings[3] != 0
                or settings[2] & (termios.PARENB | termios.CSTOPB)
                or settings[2] & termios.CSIZE != termios.CS8):
            raise MeshCoreError("invalid_serial_configuration")
        self.descriptor = descriptor

    def read(self, size, timeout):
        readable, _, _ = select.select([self.descriptor], [], [], timeout)
        return os.read(self.descriptor, size) if readable else b""

    def write(self, data, timeout):
        _, writable, _ = select.select([], [self.descriptor], [], timeout)
        return os.write(self.descriptor, data) if writable else 0


@dataclass(frozen=True)
class DeviceIdentity:
    public_key: bytes
    firmware: str
    protocol_version: int
    # Firmware info flags (protocol v9+): the companion's own repeat bit and
    # the path-hash mode. Repeat means this radio forwards flood traffic for
    # other nodes; it says nothing about duty-cycle policy.
    repeat: bool = False
    path_hash_mode: int = 0


@dataclass(frozen=True)
class SendReceipt:
    expected_ack: bytes
    suggested_timeout_ms: int
    route: str
    serial_accepted: bool = True
    delivered: bool = False
    read: bool = False


@dataclass(frozen=True)
class RadioAckObservation:
    expected_ack: bytes
    trip_time_ms: int
    radio_ack_observed: bool = True
    delivered: bool = False
    read: bool = False


@dataclass(frozen=True, repr=False)
class IncomingMessage:
    """Opaque, untrusted USB bytes; payload may include subtype-specific prefixes.

    Neither a radio contact prefix nor a channel index authenticates a SAGE
    sender. No text decoding, application-envelope verification or decryption.
    """

    raw_frame: bytes
    payload: bytes
    kind: str
    sender_prefix: bytes | None
    channel_index: int | None
    text_type: int | None
    timestamp: int | None
    path_descriptor: int
    snr_quarter_db: int | None
    data_type: int | None = None
    trust: str = "untrusted_radio_payload"
    application_authenticated: bool = False
    delivered: bool = False
    read: bool = False


def decode_incoming(frame):
    if type(frame) is not bytes or not 1 <= len(frame) <= MAX_FRAME or frame[0] not in (7, 8, 16, 17, 27):
        raise MeshCoreError("invalid_incoming_message")
    if frame[0] == 27:
        if len(frame) < 9 or frame[2:4] != bytes(2) or frame[8] != len(frame) - 9:
            raise MeshCoreError("invalid_incoming_message")
        return IncomingMessage(raw_frame=frame, payload=frame[9:], kind="channel_binary",
                               sender_prefix=None, channel_index=frame[4], text_type=None,
                               timestamp=None, path_descriptor=frame[5],
                               snr_quarter_db=struct.unpack("b", frame[1:2])[0],
                               data_type=int.from_bytes(frame[6:8], "little"))
    modern = frame[0] in (16, 17)
    contact = frame[0] in (7, 16)
    offset = 4 if modern else 1
    address_size = 6 if contact else 1
    payload_offset = offset + address_size + 6
    if len(frame) < payload_offset or (modern and frame[2:4] != bytes(2)):
        raise MeshCoreError("invalid_incoming_message")
    address = frame[offset:offset + address_size]
    offset += address_size
    return IncomingMessage(frame, frame[payload_offset:], "contact" if contact else "channel",
                           address if contact else None, None if contact else address[0],
                           frame[offset + 1], int.from_bytes(frame[offset + 2:offset + 6], "little"),
                           frame[offset], struct.unpack("b", frame[1:2])[0] if modern else None)


def encode_frame(payload):
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_FRAME:
        raise MeshCoreError("invalid_frame")
    return b"<" + struct.pack("<H", len(payload)) + payload


class MeshCoreClient:
    def __init__(self, stream, *, expected_public_key, timeout=3.0, clock=time.monotonic):
        if (type(expected_public_key) is not bytes or len(expected_public_key) != 32
                or type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 30):
            raise MeshCoreError("invalid_configuration")
        self._stream = stream
        self._key = expected_public_key
        self._timeout = timeout
        self._clock = clock
        self._last_clock = None
        self._mutex = threading.Lock()
        self._poisoned = False
        self._poisoned_by = None
        self._read_failure = None
        self._last_command = None
        self._last_discard = 0
        self._identity = None
        self._last_receipt = None
        self._receive_observer = None
        self._discovery_events = {}
        self._next_advertisement = 0
        self._last_received_signal = None
        self._last_signal_at = None
        self._push_events = collections.deque(maxlen=PUSH_EVENT_LIMIT)
        self._messages_waiting = False
        self._contact_table_dirty = False
        self._last_send_confirmation = None
        self._airtime_sample = None
        self._written = 0

    @property
    def poisoned(self):
        """True once a failure after the first write left the radio unreconciled."""
        return self._poisoned

    @property
    def poisoned_by(self):
        """Why the transport was poisoned (diagnostic only)."""
        return self._poisoned_by

    @property
    def read_failure(self):
        """The last read-only exchange that stalled without poisoning (diagnostic)."""
        return self._read_failure

    @property
    def discarded_bytes(self):
        """Bytes discarded by the last bounded resynchronisation (diagnostic)."""
        return self._last_discard

    @property
    def wire_frames(self):
        """Count of command frames whose write loop has been entered.

        Diagnostic only: the caller uses the count to tell a provably pre-wire
        failure (nothing was written, so firmware state is untouched) from an
        unknown mid-exchange outcome. It is never authorization or delivery
        evidence, and it is incremented before the first byte is written so a
        partial write still counts.
        """
        return self._written

    @property
    def push_events(self):
        """Bounded, payload-free record of asynchronous push frames observed.

        A snapshot, newest last. Entries carry the PUSH_CODES name, size and
        clock; only the send confirmation parses further (ACK CRC, trip time).
        """
        return tuple(dict(event) for event in self._push_events)

    def take_messages_waiting(self):
        """Consume the firmware's "messages are queued" notification, if seen."""
        value, self._messages_waiting = self._messages_waiting, False
        return value

    def take_contact_table_dirty(self):
        """Consume a deleted-contact / contacts-full notification, if seen."""
        value, self._contact_table_dirty = self._contact_table_dirty, False
        return value

    @property
    def send_confirmation(self):
        """Last in-band send confirmation (0x82), or None.

        This is a radio-level observation only: the firmware reports that a
        matching ACK arrived. It is not delivery or read evidence, and the same
        ACK can be reported more than once.
        """
        return None if self._last_send_confirmation is None else dict(self._last_send_confirmation)

    @property
    def airtime_sample(self):
        """Latest cumulative TX/RX airtime reading from a health() stats read."""
        return self._airtime_sample

    def set_receive_observer(self, observer):
        if not callable(observer) or not self._mutex.acquire(blocking=False):
            raise MeshCoreError('invalid_observer')
        try:
            if self._receive_observer is not None or self._identity is None or self._poisoned:
                raise MeshCoreError('invalid_observer')
            self._receive_observer = observer
        finally:
            self._mutex.release()

    def _now(self):
        current = self._clock()
        if (type(current) not in (int, float) or not math.isfinite(current)
                or (self._last_clock is not None and current < self._last_clock)):
            raise MeshCoreError("invalid_clock")
        self._last_clock = current
        return current

    def _remaining(self, deadline):
        remaining = deadline - self._now()
        if remaining <= 0:
            raise MeshCoreError("serial_timeout")
        return remaining

    def _read(self, size, deadline):
        data = bytearray()
        while len(data) < size:
            chunk = self._stream.read(size - len(data), self._remaining(deadline))
            self._remaining(deadline)
            if type(chunk) is not bytes or not 1 <= len(chunk) <= size - len(data):
                raise MeshCoreError("serial_incomplete")
            data.extend(chunk)
        return bytes(data)

    def _frame(self, deadline):
        header = self._read(3, deadline)
        size = int.from_bytes(header[1:], "little")
        if header[:1] != b">" or not 1 <= size <= MAX_FRAME:
            raise MeshCoreError("invalid_frame")
        frame = self._read(size, deadline)
        if frame[0] >= 0x80:
            self._observe_push(frame)
        return frame

    def _observe_push(self, frame):
        """Record an asynchronous push frame instead of silently dropping it.

        Pushes land inside command exchanges by design, so "_exchange skipped a
        frame it was not waiting for" must not mean "the event never existed".
        Only bounded, payload-free summaries are retained; discovery pushes keep
        their existing parsing (including the malformed-length contract).
        """
        code = frame[0]
        if code == 0x82 and len(frame) == 9:
            self._last_send_confirmation = dict(ack_crc=frame[1:5].hex(),
                trip_time_ms=int.from_bytes(frame[5:9], "little"), observed_monotonic=self._now())
        elif code == 0x83:
            self._messages_waiting = True
        elif code in (0x8F, 0x90):
            self._contact_table_dirty = True
        self._push_events.append(dict(code=code, name=PUSH_CODES.get(code, "unknown"),
                                      size=len(frame), observed_monotonic=self._now()))
        if code in (0x80, 0x88, 0x8A):
            self._observe_discovery(frame)

    def _discard_pending(self, *, limit=4096, window=0.25):
        """Bounded resynchronisation after a read-only exchange stalled.

        A read-only command cannot have sent or dequeued user traffic, so the
        only damage a stall leaves behind is part of a response still sitting in
        the port. Read and discard a bounded amount within a bounded window so the
        next command starts on a frame boundary; never writes a command, never
        retries, never blocks past the window. Returns the discarded byte count.
        """
        dropped = 0
        try:
            deadline = self._now() + window
            while dropped < limit:
                remaining = deadline - self._now()
                if remaining <= 0:
                    break
                chunk = self._stream.read(min(256, limit - dropped), remaining)
                if type(chunk) is not bytes or not chunk:
                    break
                dropped += len(chunk)
        except Exception:
            pass
        self._last_discard = dropped
        return dropped

    def _exchange(self, payload, expected_code, deadline):
        wire = encode_frame(payload)
        self._last_command = payload[:1].hex() if payload else None
        self._written += 1
        offset = 0
        while offset < len(wire):
            sent = self._stream.write(wire[offset:], self._remaining(deadline))
            self._remaining(deadline)
            if type(sent) is not int or not 1 <= sent <= len(wire) - offset:
                raise MeshCoreError("serial_incomplete")
            offset += sent
        for _ in range(33):
            frame = self._frame(deadline)
            if frame[0] in (expected_code if type(expected_code) is tuple else (expected_code,)):
                return frame
            if frame[0] == 1 and len(frame) == 2:
                raise MeshCoreError("device_rejected")
            if frame[0] < 0x80:
                raise MeshCoreError("unexpected_response")
        raise MeshCoreError("unsolicited_frame_limit")

    def _observe_discovery(self, frame):
        if frame[0] == 0x88:
            if len(frame) < 4:
                raise MeshCoreError("invalid_receive_signal_event")
            snr_quarter, rssi = struct.unpack("bb", frame[1:3])
            # PUSH_CODE_LOG_RX_DATA: the firmware emits this for every received
            # packet with the SNR/RSSI it sampled immediately after reception,
            # before it restarts listening. The stats snapshot is read later and
            # is known to report zero RSSI on this stock firmware, so keep the
            # measured value and its observation time for health reporting.
            self._last_received_signal = (rssi, snr_quarter / 4)
            self._last_signal_at = self._now()
            return
        if frame[0] not in (0x80, 0x8A):
            return
        if len(frame) != (33 if frame[0] == 0x80 else 148):
            raise MeshCoreError("invalid_discovery_event")
        key = frame[1:33].hex()
        previous = self._discovery_events.pop(key, {})
        name = previous.get("name")
        if frame[0] == 0x8A:
            name = frame[100:132].split(b"\0", 1)[0].decode("utf-8", errors="replace")
            name = "".join(character for character in name if character.isprintable())
        self._discovery_events[key] = dict(public_key=key, name=name,
            observed_monotonic=self._now(), source="firmware_advertisement",
            direct_neighbor_verified=False)
        while len(self._discovery_events) > 256:
            del self._discovery_events[next(iter(self._discovery_events))]

    def health(self, *, authorize, announce=False):
        def operation(deadline):
            if self._identity is None:
                raise MeshCoreError("handshake_required")
            if announce and self._now() >= self._next_advertisement:
                if authorize() is not True:
                    raise MeshCoreError("access_revoked")
                identity = self._exchange(b"\x01" + bytes(7) + b"Lantern", 5, deadline)
                if not 58 <= len(identity) <= 90 or identity[4:36] != self._key:
                    raise MeshCoreError("identity_mismatch")
                self._next_advertisement = self._now() + 60 + self._key[0] % 17
                if identity[45] == 0:
                    if authorize() is not True:
                        raise MeshCoreError("access_revoked")
                    if self._exchange(b"\x07\x00", 0, deadline) != b"\x00":
                        raise MeshCoreError("invalid_advertisement_response")
            result = {}
            layouts = (
                (0, "<HIHB", ("battery_mv", "uptime_seconds", "error_flags", "tx_queue")),
                (1, "<hbbII", ("noise_floor_dbm", "last_rssi_dbm", "last_snr_quarter_db", "tx_airtime_seconds", "rx_airtime_seconds")),
                (2, "<IIIIIII", ("packets_received", "packets_sent", "sent_flood", "sent_direct", "received_flood", "received_direct", "receive_errors")),
            )
            for subtype, layout, names in layouts:
                if authorize() is not True:
                    raise MeshCoreError("access_revoked")
                response = self._exchange(bytes((56, subtype)), 24, deadline)
                if len(response) != 2 + struct.calcsize(layout) or response[1] != subtype:
                    raise MeshCoreError("invalid_stats_response")
                result.update(zip(names, struct.unpack(layout, response[2:])))
            # The bridge's own view of the lane, on the firmware that serves it:
            # how many frames crossed it, how many packets skipped the radio
            # entirely, and how much of an injected packet's trip was the
            # bridge's. Without this a lane result can only be inferred from
            # timing; with it the counters are the measurement.
            if self._identity.firmware in BRIDGE_STATS_FIRMWARE:
                if authorize() is not True:
                    raise MeshCoreError("access_revoked")
                response = self._exchange(bytes((56, STATS_TYPE_BRIDGE)), 24, deadline)
                if (len(response) != 2 + struct.calcsize(BRIDGE_STATS_LAYOUT)
                        or response[1] != STATS_TYPE_BRIDGE):
                    raise MeshCoreError("invalid_stats_response")
                bridge = dict(zip(BRIDGE_STATS_NAMES,
                                  struct.unpack(BRIDGE_STATS_LAYOUT, response[2:])))
                bridge["running"] = bool(bridge["running"])
                result["bridge"] = bridge
            # The firmware's own cumulative airtime counters are the only honest
            # duty-cycle source (they include ACKs the radio sends by itself).
            # Cache the reading so a dispatch gate never pays a radio exchange.
            self._airtime_sample = AirtimeSample(monotonic=self._now(),
                tx_airtime_seconds=float(result["tx_airtime_seconds"]),
                rx_airtime_seconds=float(result["rx_airtime_seconds"]))
            if authorize() is not True:
                raise MeshCoreError("access_revoked")
            now = self._now()
            self._discovery_events = {key: entry for key, entry in self._discovery_events.items()
                if 0 <= now - entry["observed_monotonic"] < 300}
            result["discovered_nodes"] = [dict(public_key=entry["public_key"], name=entry["name"],
                age_seconds=now-entry["observed_monotonic"], source=entry["source"],
                direct_neighbor_verified=False) for entry in self._discovery_events.values()]
            result["last_snr_db"] = result.pop("last_snr_quarter_db") / 4
            if self._last_received_signal is not None and 0 <= now - self._last_signal_at < PACKET_SIGNAL_WINDOW:
                # Prefer the most recent per-packet measurement over the radio
                # stats snapshot, whose last_rssi_dbm reads zero on this firmware.
                result["last_rssi_dbm"], result["last_snr_db"] = self._last_received_signal
            return result
        return self._run(operation)

    def _run(self, operation, *, sending=False, receiving=False):
        if not self._mutex.acquire(blocking=False):
            raise MeshCoreError("busy")
        try:
            if self._poisoned:
                raise MeshCoreError("reconciliation_required")
            written = self._written
            try:
                return operation(self._now() + self._timeout)
            except BaseException as error:
                # No command byte reached the radio in this operation, so nothing
                # can have been sent or dequeued: a refusal raised before the
                # first write (denied authorization, missing handshake, identity
                # gate) is reported under the existing error contract without
                # poisoning the transport, so it never costs a cold start.
                pre_wire = self._written == written
                if not isinstance(error, Exception):
                    # A non-Exception (process shutdown and friends) is not
                    # classified: keep the fail-closed rule exactly as before.
                    if not pre_wire:
                        self._poisoned = True
                        self._identity = None
                    raise
                if not pre_wire:
                    code_name = str(error.args[0]) if error.args else type(error).__name__
                    cause = dict(command=self._last_command, sending=bool(sending),
                                 receiving=bool(receiving),
                                 error=code_name,
                                 frames=self._written - written)
                    if sending or receiving:
                        # A command that could have sent or dequeued a user message
                        # failed after the wire: firmware state is unknown, so keep
                        # the existing fail-closed rule and require a reviewed
                        # recovery before this radio is used again.
                        self._poisoned = True
                        self._poisoned_by = cause
                        self._identity = None
                    else:
                        # A read-only command (contact list, health) stalled. It
                        # cannot have sent or dequeued user traffic - the dequeue
                        # path is the receive command, which runs receiving=True -
                        # and the firmware re-sends a whole record for the next
                        # read. Drop the abandoned tail and keep the radio usable
                        # instead of latching it until a process restart.
                        self._read_failure = cause
                        if code_name in WIRE_RESYNC_CODES:
                            # Only a partially read or unparsable frame leaves the
                            # port mid-stream. A refusal raised by the caller (or a
                            # complete frame the caller rejected) leaves the port on
                            # a frame boundary, and those bytes may belong to the
                            # next response, so they must not be thrown away.
                            self._discard_pending()
                code = "send_unconfirmed" if sending else "receive_unconfirmed" if receiving else "serial_unavailable"
                raise MeshCoreError(code, outcome_unknown=not pre_wire and (sending or receiving)) from None
        finally:
            self._mutex.release()

    def handshake(self):
        def operation(deadline):
            info = self._exchange(bytes((22, PROTOCOL_VERSION)), 13, deadline)
            if (len(info) != 82 or info[1] != PROTOCOL_VERSION
                    or info[60:80].split(b"\0", 1)[0] not in tuple(version.encode("ascii") for version in SUPPORTED_FIRMWARE)):
                raise MeshCoreError("firmware_mismatch")
            if info[80] not in (0, 1) or info[81] not in (0, 1, 2):
                raise MeshCoreError("firmware_flags_invalid")
            identity = self._exchange(b"\x01" + bytes(7) + b"Lantern", 5, deadline)
            if not 58 <= len(identity) <= 90 or identity[4:36] != self._key:
                raise MeshCoreError("identity_mismatch")
            self._identity = DeviceIdentity(self._key, info[60:80].split(b"\0", 1)[0].decode("ascii"),
                                            PROTOCOL_VERSION, bool(info[80]), info[81])
            return self._identity
        return self._run(operation)

    def send_text(self, *, recipient_prefix, text, timestamp, authorize):
        """Explicit send to a pre-established six-byte contact prefix; no retries.

        Caller proves prefix uniqueness/recipient consent and journals dispatch
        before calling. Text crosses USB in plaintext. Firmware chooses direct
        routing or flooding from its contact path, not from the method name.
        """
        if (type(recipient_prefix) is not bytes or len(recipient_prefix) != 6
                or type(text) is not str or type(timestamp) is not int
                or not 1 <= timestamp <= 0xffffffff or not callable(authorize)):
            raise MeshCoreError("invalid_message")
        try:
            content = text.encode("utf-8")
        except UnicodeError:
            raise MeshCoreError("invalid_message") from None
        if not 1 <= len(content) <= MAX_TEXT or b"\0" in content:
            raise MeshCoreError("invalid_message")

        def operation(deadline):
            if self._identity is None or authorize() is not True:
                raise MeshCoreError("send_not_authorized")
            stamp = struct.pack("<I", timestamp)
            expected = hashlib.sha256(stamp + b"\0" + content + self._key).digest()[:4]
            frame = self._exchange(b"\x02\x00\x00" + stamp + recipient_prefix + content, 6, deadline)
            if len(frame) != 10 or frame[1] not in (0, 1) or frame[2:6] != expected:
                raise MeshCoreError("send_response_mismatch")
            receipt = SendReceipt(expected, int.from_bytes(frame[6:10], "little"),
                                  "flood" if frame[1] else "direct")
            self._last_receipt = receipt
            return receipt
        return self._run(operation, sending=True)

    def get_contacts(self, *, authorize):
        """Read an observed inventory, not a future routing or consent guarantee."""
        from .meshcore_contacts import get_contacts

        def operation(deadline):
            if self._identity is None:
                raise MeshCoreError("handshake_required")
            return get_contacts(exchange=self._exchange, read_frame=self._frame,
                                deadline=deadline, authorize=authorize)
        return self._run(operation)

    def observe_ack(self, receipt):
        """One bounded passive wait; short radio ACK is NOT delivery/read proof."""
        if type(receipt) is not SendReceipt or receipt is not self._last_receipt:
            raise MeshCoreError("invalid_receipt")

        def operation(deadline):
            for _ in range(33):
                frame = self._frame(deadline)
                if frame[0] == 0x82:
                    if len(frame) != 9:
                        raise MeshCoreError("invalid_ack")
                    if receipt.expected_ack != bytes(4) and frame[1:5] == receipt.expected_ack:
                        self._last_receipt = None
                        return RadioAckObservation(frame[1:5], int.from_bytes(frame[5:9], "little"))
                elif frame[0] < 0x80:
                    raise MeshCoreError("unexpected_response")
            raise MeshCoreError("unsolicited_frame_limit")
        return self._run(operation)

    def receive_next(self, *, authorize, persist):
        """Pop at most one firmware message; None means explicit queue-empty reply.

        persist(IncomingMessage)->True must durably protect the raw bytes before
        returning, even if authorization was revoked after command admission.
        Both callbacks are trusted and bounded. They must not send read receipts.
        Firmware dequeue is destructive and cannot be made crash-atomic with host
        storage: unknown results require reconciliation, never automatic retry.
        """
        if not callable(authorize) or not callable(persist):
            raise MeshCoreError("invalid_receive_configuration")

        def operation(deadline):
            if self._identity is None or authorize() is not True:
                raise MeshCoreError("receive_not_authorized")
            frame = self._exchange(b"\x0a", (7, 8, 10, 16, 17, 27), deadline)
            if frame[0] == 10:
                if len(frame) != 1:
                    raise MeshCoreError("invalid_empty_response")
                message = None
            else:
                message = decode_incoming(frame)
                if persist(message) is not True:
                    raise MeshCoreError("receive_storage_unconfirmed")
            self._remaining(deadline)
            if authorize() is not True:
                raise MeshCoreError("receive_access_revoked")
            if message is not None and self._receive_observer is not None:
                try:
                    self._receive_observer(dict(kind=message.kind, sender_prefix=message.sender_prefix,
                        snr_quarter_db=message.snr_quarter_db))
                except Exception:
                    self._receive_observer = None
            return message
        return self._run(operation, receiving=True)
