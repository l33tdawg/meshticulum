# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Isolated integer-microsecond fixed-window reservation pacing ledger.

Costs are caller estimates, not physical measurements or regulatory compliance.
The shared-medium estimate includes forward, firmware ACK and reverse control
work; only own-radio TX has a cumulative counter here. A trusted adapter must
bind epoch/counter provenance and sample time to the actual carrier.

Pending reservations survive window changes and counter reads. ACCEPTED and
UNKNOWN mean carrier admission/uncertainty, not drained physical transmission;
their costs become persistent debt, added to each window's counter usage.
Neither newer samples, counter progress nor quiet elapsed time discharge that
debt. One quantization unit covers counter flooring. Counter reconciliation is
diagnostic only. This intentionally counts overlap twice and eventually stops
new work until a separately reviewed completion/epoch adapter exists.

There is no broker, send, clock read, persistence, cryptography or thread in this
module. The host must serialize calls and protect checkpoint provenance. A
restore retires all old handles, carries existing debt and open costs as debt,
and refuses new work for a full window from its new start, with a fresh sample
still needed. Recovery waiting does not itself prove drain or erase debt.
"""

from dataclasses import dataclass
import secrets

from meshticulum.contracts.errors import ContractError, integer, octets
from meshticulum.contracts.negotiation import CommitOutcome


MAX_WINDOW_US = 86_400_000_000
MAX_TIME_US = (1 << 63) - 1
MAX_USAGE_US = MAX_TIME_US + 4 * MAX_WINDOW_US
MAX_RESERVATIONS = 64
MAX_TOKEN = (1 << 64) - 1


def _epoch(value):
    octets(value, 16, "invalid_epoch")
    if not any(value):
        raise ContractError("invalid_epoch")


def _time(value):
    return integer(value, 0, MAX_TIME_US, "invalid_time")


@dataclass(frozen=True, slots=True)
class Cost:
    own_tx_us: int
    shared_medium_us: int

    def __post_init__(self):
        integer(self.own_tx_us, 1, MAX_WINDOW_US, "invalid_cost")
        integer(self.shared_medium_us, 1, MAX_WINDOW_US, "invalid_cost")
        if self.shared_medium_us < self.own_tx_us:
            raise ContractError("invalid_cost")


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    window_us: int
    own_tx_limit_us: int
    shared_limit_us: int
    sample_max_age_us: int
    quantization_us: int = 1_000_000
    max_reservations: int = 1

    def __post_init__(self):
        integer(self.window_us, 1, MAX_WINDOW_US, "invalid_budget_policy")
        for value in (self.own_tx_limit_us, self.shared_limit_us,
                      self.sample_max_age_us, self.quantization_us):
            integer(value, 1, self.window_us, "invalid_budget_policy")
        integer(self.max_reservations, 1, MAX_RESERVATIONS, "invalid_budget_policy")
        if self.shared_limit_us < self.own_tx_limit_us:
            raise ContractError("invalid_budget_policy")


@dataclass(frozen=True, slots=True)
class CounterSample:
    at_us: int
    tx_us: int
    epoch: bytes

    def __post_init__(self):
        _time(self.at_us)
        integer(self.tx_us, 0, MAX_TIME_US, "invalid_sample")
        _epoch(self.epoch)


@dataclass(frozen=True, slots=True, repr=False)
class Reservation:
    ledger_id: bytes
    epoch: bytes
    token: int
    cost: Cost
    reserved_at_us: int

    def __post_init__(self):
        octets(self.ledger_id, 16, "invalid_reservation")
        if not any(self.ledger_id) or type(self.cost) is not Cost:
            raise ContractError("invalid_reservation")
        _epoch(self.epoch)
        integer(self.token, 1, MAX_TOKEN, "invalid_reservation")
        _time(self.reserved_at_us)


@dataclass(frozen=True, slots=True, repr=False)
class BudgetCheckpoint:
    """Fixed-size inert recovery data; not authenticated persistent evidence.

    No tokens are serialized/restored. Aggregates include all open costs and
    may exceed a policy ceiling when independent firmware traffic was seen.
    """

    policy: BudgetPolicy
    epoch: bytes
    at_us: int
    window_start_us: int
    own_used_us: int
    shared_used_us: int
    pending_reservations: int
    pending_own_us: int
    pending_shared_us: int
    debt_own_us: int
    debt_shared_us: int
    fenced: bool

    def __post_init__(self):
        if type(self.policy) is not BudgetPolicy or type(self.fenced) is not bool:
            raise ContractError("invalid_checkpoint")
        _epoch(self.epoch)
        _time(self.at_us)
        _time(self.window_start_us)
        integer(self.own_used_us, 0, MAX_USAGE_US, "invalid_checkpoint")
        integer(self.shared_used_us, 0, MAX_USAGE_US, "invalid_checkpoint")
        integer(self.pending_reservations, 0, self.policy.max_reservations, "invalid_checkpoint")
        integer(self.pending_own_us, 0, self.policy.own_tx_limit_us, "invalid_checkpoint")
        integer(self.pending_shared_us, 0, self.policy.shared_limit_us, "invalid_checkpoint")
        integer(self.debt_own_us, 0, self.policy.own_tx_limit_us, "invalid_checkpoint")
        integer(self.debt_shared_us, 0, self.policy.shared_limit_us, "invalid_checkpoint")
        if (self.window_start_us > self.at_us
                or self.shared_used_us < self.own_used_us
                or self.pending_own_us > self.own_used_us
                or self.pending_shared_us > self.shared_used_us
                or self.pending_shared_us < self.pending_own_us
                or self.debt_shared_us < self.debt_own_us
                or self.debt_own_us + self.pending_own_us > self.policy.own_tx_limit_us
                or self.debt_shared_us + self.pending_shared_us > self.policy.shared_limit_us
                or self.debt_own_us + self.pending_own_us > self.own_used_us
                or self.debt_shared_us + self.pending_shared_us > self.shared_used_us
                or (self.pending_reservations == 0) != (self.pending_own_us == 0)
                or (self.pending_reservations == 0) != (self.pending_shared_us == 0)
                or self.pending_own_us < self.pending_reservations):
            raise ContractError("invalid_checkpoint")


class Ledger:
    """Single-owner fixed-window gate with exactly-once live reservations.

    snapshot() and checkpoint() process elapsed windows. Invalid reservation
    or outcome settlement is checked before changing clocks or ledger state.
    settle() can reconcile an existing handle while fenced, but cannot grant
    new work or repair a counter/clock fence. Unknown outcomes settle as spent.
    """

    def __init__(self, policy, epoch, start_us):
        if type(policy) is not BudgetPolicy:
            raise ContractError("invalid_budget_policy")
        _epoch(epoch)
        _time(start_us)
        if start_us + policy.window_us > MAX_TIME_US:
            raise ContractError("invalid_time")
        self._policy, self._epoch = policy, epoch
        self._id = secrets.token_bytes(16)
        if not any(self._id):
            raise ContractError("invalid_reservation")
        self._window_start = self._last_us = start_us
        self._sample = None
        self._baseline = None
        self._estimated_own = self._estimated_shared = 0
        self._unsampled_own = self._unsampled_shared = 0
        self._last_charge_us = None
        self._pending = {}
        self._next_token = 1
        self._fence = None
        self._recovery_until = 0
        self._recovery_own = self._recovery_shared = 0
        self._recovered_unresolved = 0

    @property
    def policy(self):
        return self._policy

    @property
    def epoch(self):
        return self._epoch

    def _tick(self, now_us):
        _time(now_us)
        if now_us < self._last_us:
            self._fence = self._fence or "clock_regressed"
            raise ContractError("clock_regressed")
        elapsed = now_us - self._window_start
        if elapsed >= self.policy.window_us:
            next_start = self._window_start + (elapsed // self.policy.window_us) * self.policy.window_us
            if next_start + self.policy.window_us > MAX_TIME_US:
                self._fence = self._fence or "clock_exhausted"
                raise ContractError("clock_exhausted")
            # No sample or elapsed window proves that carrier-admitted work
            # physically drained. Settled uncertainty debt and _pending remain.
            self._window_start = next_start
            self._baseline = self._sample.tx_us if self._sample is not None else None
            if now_us >= self._recovery_until:
                self._recovery_own = self._recovery_shared = 0
        self._last_us = now_us

    def _fresh(self, now_us):
        return (self._sample is not None and self._sample.at_us <= now_us
                and now_us - self._sample.at_us < self.policy.sample_max_age_us)

    def _usage(self):
        measured = 0
        if self._sample is not None:
            measured = (self._sample.tx_us - self._baseline
                        + self.policy.quantization_us)
        own = measured + self._estimated_own + self._recovery_own
        # Carrier-admitted work may still execute later. Without a completion
        # proof, no overlap with the cumulative counter may be subtracted.
        shared = measured + self._estimated_shared + self._recovery_shared
        # Saturation is always above every policy limit, so it can only refuse
        # work and keeps repeated conservative checkpoint recovery bounded.
        return (min(MAX_USAGE_US, own + sum(item.cost.own_tx_us for item in self._pending.values())),
                min(MAX_USAGE_US, shared + sum(item.cost.shared_medium_us for item in self._pending.values())))

    def observe(self, sample, now_us):
        if type(sample) is not CounterSample:
            raise ContractError("invalid_sample")
        _time(now_us)
        if sample.at_us > now_us:
            raise ContractError("future_sample")
        if self._fence is not None:
            raise ContractError("ledger_fenced")
        if sample.epoch != self.epoch:
            self._tick(now_us)
            self._fence = "counter_epoch_changed"
            raise ContractError("counter_epoch_changed")
        previous = self._sample
        if previous is not None and sample.at_us == previous.at_us:
            if sample.tx_us != previous.tx_us:
                self._tick(now_us)
                self._fence = "sample_conflict"
                raise ContractError("sample_conflict")
            # Cached same-time reading cannot contain subsequent attempts.
            self._tick(now_us)
            return
        if previous is not None and sample.at_us < previous.at_us:
            raise ContractError("sample_reordered")
        if now_us - sample.at_us >= self.policy.sample_max_age_us:
            raise ContractError("stale_sample")
        self._tick(now_us)
        if previous is not None and sample.tx_us < previous.tx_us:
            self._fence = "counter_decreased"
            raise ContractError("counter_decreased")
        if self._baseline is None:
            # If the initial counter was captured after this window opened,
            # conservatively include its entire cumulative value. A sample at
            # or before the start is a safe (possibly earlier) baseline.
            self._baseline = sample.tx_us if sample.at_us <= self._window_start else 0
        self._sample = sample
        if self._last_charge_us is None or sample.at_us > self._last_charge_us:
            self._unsampled_own = self._unsampled_shared = 0
        # This only updates diagnostic unsampled totals. Persistent uncertainty
        # debt and open reservations never change on any counter read.

    def reserve(self, cost, now_us):
        if type(cost) is not Cost:
            raise ContractError("invalid_cost")
        self._tick(now_us)
        if self._fence is not None:
            raise ContractError("ledger_fenced")
        if now_us < self._recovery_until:
            raise ContractError("recovery_gate")
        if self._sample is None:
            raise ContractError("no_sample")
        if not self._fresh(now_us):
            raise ContractError("stale_sample")
        if len(self._pending) >= self.policy.max_reservations:
            raise ContractError("reservation_capacity")
        own, shared = self._usage()
        if own + cost.own_tx_us > self.policy.own_tx_limit_us:
            raise ContractError("own_budget")
        if shared + cost.shared_medium_us > self.policy.shared_limit_us:
            raise ContractError("shared_budget")
        if self._next_token > MAX_TOKEN:
            raise ContractError("token_exhausted")
        handle = Reservation(self._id, self.epoch, self._next_token, cost, now_us)
        self._next_token += 1
        self._pending[handle.token] = handle
        return handle

    def _live(self, reservation):
        if type(reservation) is not Reservation:
            raise ContractError("invalid_reservation")
        if reservation.ledger_id != self._id or reservation.epoch != self.epoch:
            raise ContractError("reservation_scope_mismatch")
        if self._pending.get(reservation.token) is not reservation:
            raise ContractError("reservation_not_live")

    def validate(self, reservation, now_us):
        """Recheck the last pre-send boundary without settling its booked cost."""
        self._live(reservation)
        self._tick(now_us)
        if self._fence is not None:
            raise ContractError("ledger_fenced")
        if now_us < self._recovery_until:
            raise ContractError("recovery_gate")
        if self._sample is None:
            raise ContractError("no_sample")
        if not self._fresh(now_us):
            raise ContractError("stale_sample")
        own, shared = self._usage()
        if own > self.policy.own_tx_limit_us:
            raise ContractError("own_budget")
        if shared > self.policy.shared_limit_us:
            raise ContractError("shared_budget")
        return True

    def settle(self, reservation, outcome, now_us):
        self._live(reservation)
        if type(outcome) is not CommitOutcome:
            raise ContractError("invalid_outcome")
        charged = outcome in (CommitOutcome.ACCEPTED, CommitOutcome.UNKNOWN)
        try:
            self._tick(now_us)
        except ContractError as error:
            if error.code not in ("invalid_time", "clock_regressed", "clock_exhausted"):
                raise
            self._fence = self._fence or error.code
            if not charged:
                raise
            # A failed clock after physical send must not erase an attempted
            # cost. Use the last valid ledger time and fence further sends.
            now_us = self._last_us
        del self._pending[reservation.token]
        if charged:
            self._estimated_own += reservation.cost.own_tx_us
            self._estimated_shared += reservation.cost.shared_medium_us
            self._unsampled_own += reservation.cost.own_tx_us
            self._unsampled_shared += reservation.cost.shared_medium_us
            self._last_charge_us = now_us

    def snapshot(self, now_us):
        self._tick(now_us)
        own, shared = self._usage()
        return dict(window_start_us=self._window_start,
                    window_end_us=self._window_start + self.policy.window_us,
                    own_used_us=own, shared_used_us=shared,
                    own_remaining_us=max(0, self.policy.own_tx_limit_us - own),
                    shared_remaining_us=max(0, self.policy.shared_limit_us - shared),
                    estimated_own_us=self._estimated_own,
                    estimated_shared_us=self._estimated_shared,
                    debt_own_us=self._estimated_own,
                    debt_shared_us=self._estimated_shared,
                    unsampled_own_us=self._unsampled_own,
                    unsampled_shared_us=self._unsampled_shared,
                    pending_reservations=len(self._pending),
                    sample_fresh=self._fresh(now_us), fenced=self._fence is not None,
                    fence_reason=self._fence, recovery_until_us=self._recovery_until,
                    recovered_unresolved_reservations=self._recovered_unresolved)

    def checkpoint(self, now_us):
        state = self.snapshot(now_us)
        return BudgetCheckpoint(self.policy, self.epoch, now_us, self._window_start,
            state["own_used_us"], state["shared_used_us"], len(self._pending),
            sum(item.cost.own_tx_us for item in self._pending.values()),
            sum(item.cost.shared_medium_us for item in self._pending.values()),
            self._estimated_own, self._estimated_shared,
            self._fence is not None)

    @classmethod
    def restore(cls, checkpoint, new_epoch, start_us):
        if type(checkpoint) is not BudgetCheckpoint:
            raise ContractError("invalid_checkpoint")
        # Revalidate ordinary record fields; this is not authenticity checking.
        BudgetCheckpoint(**{name: getattr(checkpoint, name)
                            for name in BudgetCheckpoint.__dataclass_fields__})
        ledger = cls(checkpoint.policy, new_epoch, start_us)
        ledger._estimated_own = ledger._unsampled_own = checkpoint.debt_own_us + checkpoint.pending_own_us
        ledger._estimated_shared = ledger._unsampled_shared = checkpoint.debt_shared_us + checkpoint.pending_shared_us
        ledger._recovery_own = checkpoint.own_used_us - ledger._estimated_own
        ledger._recovery_shared = checkpoint.shared_used_us - ledger._estimated_shared
        ledger._last_charge_us = start_us
        ledger._recovered_unresolved = checkpoint.pending_reservations
        ledger._recovery_until = start_us + checkpoint.policy.window_us
        return ledger
