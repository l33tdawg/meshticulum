"""Duty-cycle pacing fed by the radio firmware's own TX airtime counter.

The companion firmware counts every millisecond it transmits (`total_air_time`
in `Dispatcher`), and STATS_TYPE_RADIO exposes it as whole seconds. That
counter is the only honest source for a duty-cycle gate: it also includes
frames the host never schedules, such as the ACKs the radio sends for received
packets. Its resolution is one second and it only moves when a health() read
fetches it, so the ledger combines the two available views of the same window:
the counter's advance since the window opened, plus the estimates of attempts
made after the most recent counter reading (which that reading cannot contain),
and never less than the window's total estimates. Over-counting only throttles
our own dispatch, which is the safe direction for a regulatory limit.

This ledger is a *pacing gate*, not a certification. It uses fixed windows (a
burst at a window boundary can exceed a strict sliding-window reading of a
regulatory limit), it under-counts a window by less than one frame because the
firmware counter is floor-quantised to seconds, and it only governs the
background dispatch paths wired into MeshRuntime: one-shot dispatch routes and
firmware-generated traffic are visible to it only through the counter.
"""

from dataclasses import dataclass
import math


DEFAULT_WINDOW_SECONDS = 3600.0
DEFAULT_DUTY_CYCLE = 0.01
DEFAULT_FRAME_SECONDS = 1.0
DEFAULT_SAMPLE_MAX_AGE_SECONDS = 600.0
POLICY_KEYS = ("window_seconds", "duty_cycle", "frame_seconds", "sample_max_age_seconds")


def _require(condition, message="invalid airtime policy"):
    if condition is not True:
        raise ValueError(message)


def _number(value, *, low, high, message):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high, message)
    return float(value)


@dataclass(frozen=True)
class AirtimePolicy:
    """Operator-configured duty-cycle budget for background dispatch."""

    window_seconds: float = DEFAULT_WINDOW_SECONDS
    duty_cycle: float = DEFAULT_DUTY_CYCLE
    frame_seconds: float = DEFAULT_FRAME_SECONDS
    sample_max_age_seconds: float = DEFAULT_SAMPLE_MAX_AGE_SECONDS

    def __post_init__(self):
        _number(self.window_seconds, low=60, high=86400, message="invalid airtime window")
        _number(self.duty_cycle, low=1e-6, high=1, message="invalid duty cycle")
        _number(self.frame_seconds, low=1e-3, high=60, message="invalid frame estimate")
        _number(self.sample_max_age_seconds, low=10, high=3600, message="invalid sample age limit")


@dataclass(frozen=True)
class AirtimeSample:
    """One reading of the firmware's cumulative TX/RX airtime counters."""

    monotonic: float
    tx_airtime_seconds: float
    rx_airtime_seconds: float | None = None


def policy_from_settings(value):
    """Build a policy from the optional mesh-runtime.json ``airtime`` block."""
    _require(type(value) is dict and not set(value) - set(POLICY_KEYS), "invalid airtime settings")
    settings = {key: value.get(key, default) for key, default in zip(POLICY_KEYS, (
        DEFAULT_WINDOW_SECONDS, DEFAULT_DUTY_CYCLE, DEFAULT_FRAME_SECONDS, DEFAULT_SAMPLE_MAX_AGE_SECONDS))}
    return AirtimePolicy(**settings)


class AirtimeLedger:
    """Fixed-window duty-cycle ledger with two-phase allow/spend semantics.

    ``allow`` checks a candidate transmission against the budget without
    consuming it; ``spend`` records the estimate once the attempt has happened
    (including a failed attempt that may still have reached the wire). Keeping
    the two apart means a permitted-but-empty attempt - a probe with nothing to
    send, for example - costs no budget, while a fragment that is attempted
    always costs one estimate until the next counter sample accounts for it.
    """

    def __init__(self, policy):
        _require(type(policy) is AirtimePolicy)
        self.policy = policy
        self._sample = None
        self._window_start = None
        self._window_baseline = None
        self._estimated = 0.0
        self._spent_since_sample = 0.0
        self._allowed = 0
        self._denied = 0
        self._denied_stale = 0
        self._last = None

    def observe(self, sample):
        """Keep the newest counter reading; out-of-order samples are ignored."""
        _require(type(sample) is AirtimeSample
                 and math.isfinite(sample.monotonic) and math.isfinite(sample.tx_airtime_seconds)
                 and sample.tx_airtime_seconds >= 0
                 and (sample.rx_airtime_seconds is None
                      or (math.isfinite(sample.rx_airtime_seconds) and sample.rx_airtime_seconds >= 0)),
                 "invalid airtime sample")
        if self._sample is None or sample.monotonic >= self._sample.monotonic:
            self._sample = sample
            self._spent_since_sample = 0.0

    def allow(self, *, now, frames=1):
        """True when ``frames`` more estimated frames fit inside this window."""
        _require(math.isfinite(now))
        _require(type(frames) is int and 1 <= frames <= 64)
        policy = self.policy
        sample = self._sample
        if sample is None:
            self._denied += 1
            self._last = dict(allowed=False, reason="no_sample", used_seconds=None, required_seconds=frames * policy.frame_seconds)
            return False
        if now - sample.monotonic > policy.sample_max_age_seconds or now < sample.monotonic:
            self._denied += 1
            self._denied_stale += 1
            self._last = dict(allowed=False, reason="stale_sample", used_seconds=None, required_seconds=frames * policy.frame_seconds)
            return False
        if (self._window_start is None or now < self._window_start
                or now - self._window_start >= policy.window_seconds):
            self._window_start = now
            self._window_baseline = sample.tx_airtime_seconds
            self._estimated = 0.0
        counter_used = max(0.0, sample.tx_airtime_seconds - self._window_baseline)
        used = max(counter_used + self._spent_since_sample, self._estimated)
        required = frames * policy.frame_seconds
        budget = policy.duty_cycle * policy.window_seconds
        allowed = used + required <= budget + 1e-9
        if allowed:
            self._allowed += 1
        else:
            self._denied += 1
        self._last = dict(allowed=allowed, reason="budget" if not allowed else "ok",
                          used_seconds=used, required_seconds=required)
        return allowed

    def spend(self, *, frames=1):
        """Record estimated airtime for an attempt that has now happened."""
        _require(type(frames) is int and 1 <= frames <= 64)
        if self._window_start is not None:
            estimate = frames * self.policy.frame_seconds
            self._estimated += estimate
            self._spent_since_sample += estimate

    def snapshot(self):
        """Bounded diagnostics: no payloads, keys or peer identities."""
        policy = self.policy
        used = None
        if self._sample is not None and self._window_start is not None:
            used = max(max(0.0, self._sample.tx_airtime_seconds - self._window_baseline)
                       + self._spent_since_sample, self._estimated)
        return dict(event="mesh_airtime_ledger", window_seconds=policy.window_seconds,
                    duty_cycle=policy.duty_cycle, frame_seconds=policy.frame_seconds,
                    budget_seconds=policy.duty_cycle * policy.window_seconds,
                    used_seconds=used, estimated_seconds=self._estimated,
                    unsampled_seconds=self._spent_since_sample,
                    allowed=self._allowed, denied=self._denied, denied_stale=self._denied_stale,
                    last=self._last)
