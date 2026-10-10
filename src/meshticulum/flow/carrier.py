"""Deterministic simulated carrier for the Meshticulum flow candidate.

This is a test carrier, not a production Lantern lane. Nothing here opens a
socket, a serial port or a radio, and no runtime module is imported. A host UDP
carrier is a later optional feature (MT05 treats UDP as a test/optional host
carrier); the radio lane itself belongs to the broker work.

Everything is virtual and seeded. There is no thread, no sleep and no
wall-clock assertion, so the same seed and the same call order produce the same
deliveries on every run.

Two costs are modelled deliberately apart, because conflating them is how a
host scheduler gets credited with removing firmware serialization:

* ``firmware_ack_bytes`` is charged per *data frame* handed to the radio. It
  stands for the radio's own per-frame acknowledgement, which no overlay ACK
  can remove.
* ``ack_bytes_per_second`` is a reverse-link allowance for *overlay* ACK frames.
  When it is exhausted the carrier refuses the ACK, and the sender has to learn
  from a retry deadline instead. That refusal is counted separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import heapq
import random

ENDPOINTS = ("a", "b")


def other(endpoint: str) -> str:
    if endpoint not in ENDPOINTS:
        raise ValueError("unknown endpoint")
    return ENDPOINTS[1] if endpoint == ENDPOINTS[0] else ENDPOINTS[0]


@dataclass(frozen=True)
class LinkProfile:
    """What one direction of the carrier does to frames handed to it.

    A profile describes the direction a named endpoint *sends on*.
    """

    name: str
    capacity_bytes_per_second: float
    one_way_delay_s: float = 0.05
    jitter_s: float = 0.0
    loss_p: float = 0.0
    duplicate_p: float = 0.0
    corrupt_p: float = 0.0
    reorder_p: float = 0.0
    queue_bytes: int = 8192
    firmware_ack_bytes: int = 0
    ack_bytes_per_second: float | None = None

    def __post_init__(self):
        probabilities = (("loss_p", self.loss_p), ("duplicate_p", self.duplicate_p),
                         ("corrupt_p", self.corrupt_p), ("reorder_p", self.reorder_p))
        for name, value in probabilities:
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a probability")
        if self.capacity_bytes_per_second <= 0:
            raise ValueError("capacity must be positive")
        if self.queue_bytes < 0 or self.one_way_delay_s < 0 or self.jitter_s < 0:
            raise ValueError("negative carrier budget")
        if self.firmware_ack_bytes < 0:
            raise ValueError("negative firmware cost")
        if self.ack_bytes_per_second is not None and self.ack_bytes_per_second <= 0:
            raise ValueError("ack allowance must be positive when set")


@dataclass(frozen=True)
class Delivery:
    payload: bytes
    corrupted: bool
    kind: str
    sent_at: float
    delivered_at: float


@dataclass
class CarrierStats:
    sent_frames: int = 0
    sent_bytes: int = 0
    ack_frames: int = 0
    refused_queue: int = 0
    refused_ack_budget: int = 0
    dropped: int = 0
    duplicated: int = 0
    corrupted: int = 0
    firmware_ack_bytes: int = 0
    restarts: int = 0
    sent_by_endpoint: dict = field(default_factory=lambda: {"a": 0, "b": 0})

    def as_dict(self) -> dict:
        return dict(vars(self))


def _corrupt(payload: bytes, rng: random.Random) -> bytes:
    if not payload:
        return payload
    index = rng.randrange(len(payload))
    bit = 1 << rng.randrange(8)
    mutated = bytearray(payload)
    mutated[index] ^= bit
    return bytes(mutated)


class SimLink:
    """One bidirectional link between endpoints ``a`` and ``b`` on a virtual clock."""

    def __init__(self, forward: LinkProfile, reverse: LinkProfile, *, seed: int = 0):
        self._profiles = {ENDPOINTS[0]: forward, ENDPOINTS[1]: reverse}
        self._rng = random.Random(seed)
        self.stats = CarrierStats()
        self.now = 0.0
        self._events: list[tuple[float, int, str, Delivery]] = []
        self._order = 0
        self._inbound: dict[str, list[Delivery]] = {"a": [], "b": []}
        self._queued = {"a": 0, "b": 0}
        self._busy_until = {endpoint: 0.0 for endpoint in ENDPOINTS}
        self._incarnation = {"a": 0, "b": 0}
        self._ack_tokens = {name: (profile.ack_bytes_per_second or 0.0) for name, profile in self._profiles.items()}
        self._ack_refilled_at = {endpoint: 0.0 for endpoint in ENDPOINTS}

    # -- observation -----------------------------------------------------

    def profile(self, sender: str) -> LinkProfile:
        return self._profiles[sender]

    def incarnation(self, endpoint: str) -> int:
        """Bumped when that endpoint is restarted; a flow treats a change as peer loss."""
        return self._incarnation[endpoint]

    def queued_bytes(self, sender: str) -> int:
        return self._queued[sender]

    def in_flight(self) -> int:
        return len(self._events)

    def inbound(self, endpoint: str) -> list[Delivery]:
        """Take everything that has arrived for that endpoint."""
        arrived = self._inbound[endpoint]
        self._inbound[endpoint] = []
        return arrived

    # -- events ----------------------------------------------------------

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("time only moves forward")
        self.now += seconds
        self._deliver_due()

    def advance_to_next(self) -> None:
        """Jump straight to the next scheduled delivery, if any."""
        if self._events and self._events[0][0] > self.now:
            self.now = self._events[0][0]
        self._deliver_due()

    def _deliver_due(self) -> None:
        while self._events and self._events[0][0] <= self.now:
            _, _, recipient, delivery = heapq.heappop(self._events)
            self._queued[other(recipient)] = max(0, self._queued[other(recipient)] - len(delivery.payload))
            self._inbound[recipient].append(delivery)

    # -- transmission ----------------------------------------------------

    def send(self, sender: str, payload: bytes, *, kind: str = "data") -> bool:
        """Hand a frame to the radio. False means refused: backpressure, not loss."""
        profile = self._profiles[sender]
        if kind == "ack" and profile.ack_bytes_per_second is not None:
            if not self._spend_ack_tokens(sender, len(payload)):
                self.stats.refused_ack_budget += 1
                return False

        # Decide duplication first: the queue has to hold every copy this frame
        # will actually produce, or the declared ceiling is not the real one.
        copies = 2 if self._rng.random() < profile.duplicate_p else 1
        if self._queued[sender] + copies * len(payload) > profile.queue_bytes:
            self.stats.refused_queue += 1
            return False

        self.stats.sent_frames += 1
        self.stats.sent_bytes += len(payload)
        self.stats.sent_by_endpoint[sender] = self.stats.sent_by_endpoint.get(sender, 0) + 1
        if kind == "data":
            self.stats.firmware_ack_bytes += profile.firmware_ack_bytes
        else:
            self.stats.ack_frames += 1
        if copies == 2:
            self.stats.duplicated += 1

        # The radio is a serial resource: a frame occupies it for its own
        # transmission time, and the next frame waits. A frame lost in the air
        # has still spent that airtime.
        transmit_s = len(payload) / profile.capacity_bytes_per_second
        start_at = max(self.now, self._busy_until[sender])
        self._busy_until[sender] = start_at + transmit_s

        if self._rng.random() < profile.loss_p:
            self.stats.dropped += 1
            return True

        arrive_at = start_at + transmit_s + profile.one_way_delay_s
        if profile.jitter_s:
            arrive_at += self._rng.random() * profile.jitter_s
        if self._rng.random() < profile.reorder_p:
            arrive_at += max(transmit_s, 1e-6)

        corrupted = self._rng.random() < profile.corrupt_p
        body = payload
        if corrupted:
            self.stats.corrupted += 1
            body = _corrupt(payload, self._rng)

        for _ in range(copies):
            self._schedule(sender, body, corrupted, arrive_at)
        return True

    def _schedule(self, sender: str, payload: bytes, corrupted: bool, arrive_at: float) -> None:
        recipient = other(sender)
        delivery = Delivery(payload=payload, corrupted=corrupted, kind="frame",
                            sent_at=self.now, delivered_at=arrive_at)
        self._order += 1
        heapq.heappush(self._events, (arrive_at, self._order, recipient, delivery))
        self._queued[sender] += len(payload)

    def _spend_ack_tokens(self, sender: str, size: int) -> bool:
        profile = self._profiles[sender]
        rate = profile.ack_bytes_per_second or 0.0
        elapsed = self.now - self._ack_refilled_at[sender]
        if elapsed > 0:
            self._ack_tokens[sender] = min(float(profile.queue_bytes), self._ack_tokens[sender] + elapsed * rate)
            self._ack_refilled_at[sender] = self.now
        if self._ack_tokens[sender] < size:
            return False
        self._ack_tokens[sender] -= size
        return True

    # -- topology changes ------------------------------------------------

    def switch_profile(self, sender: str, profile: LinkProfile) -> None:
        """Change the carrier under a live flow: a route or lane change, never silent."""
        self._profiles[sender] = profile
        self._ack_tokens[sender] = min(float(profile.queue_bytes), profile.ack_bytes_per_second or 0.0)
        self._ack_refilled_at[sender] = self.now

    def restart(self, endpoint: str) -> None:
        """Restart a peer: frames still in the air to it are lost with its state."""
        kept = []
        for event in self._events:
            _, _, recipient, delivery = event
            if recipient == endpoint:
                sender = other(endpoint)
                self._queued[sender] = max(0, self._queued[sender] - len(delivery.payload))
            else:
                kept.append(event)
        self._events = kept
        heapq.heapify(self._events)
        self._inbound[endpoint] = []
        self._incarnation[endpoint] += 1
        self.stats.restarts += 1
