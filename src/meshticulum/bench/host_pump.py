"""Single-owner host driving with bounded wakeups and useful immediate work.

Hints decide when to call the normal step again; they never authorize dispatch,
extend deadlines, or stand in for physical settlement. No background reader or
timer callback mutates worker state.
"""
from .linux_hat import require


MAX_WAIT_US = 5_000
MAX_IMMEDIATE_STEPS = 4


def run_host_pump(step, *, check, wait, clock_us, deadline_us,
                  progress=None, can_repump=None):
    """Run until step completes, preserving checks and original finite time.

    ``progress`` is a monotonic count of successful queue admissions. Only a
    strict increase and a positive read-only capacity hint allow another step
    without waiting, for at most four consecutive steps. A refused offer or
    ordinary timer tick cannot spin through this path. ``wait`` must block up
    to its bounded timeout or return on actionable input; it must not consume
    that input. The next normal step rechecks all live authority.
    """
    require(type(deadline_us) is int and deadline_us > 0, 'invalid_host_pump_deadline')
    require(all(callable(item) for item in (step, check, wait, clock_us))
            and ((progress is None and can_repump is None)
                 or callable(progress) and callable(can_repump)), 'invalid_host_pump_callbacks')
    consecutive = 0
    while True:
        now_us = clock_us()
        require(type(now_us) is int and 0 <= now_us < deadline_us, 'host_pump_deadline')
        before = None if progress is None else progress()
        if step():
            return
        check()
        now_us = clock_us()
        require(type(now_us) is int and 0 <= now_us < deadline_us, 'host_pump_deadline')
        consecutive += 1
        after = None if progress is None else progress()
        if (consecutive < MAX_IMMEDIATE_STEPS and type(before) is int
                and type(after) is int and after > before >= 0 and can_repump() is True):
            continue
        wait(min(MAX_WAIT_US, deadline_us - now_us) / 1_000_000)
        consecutive = 0
