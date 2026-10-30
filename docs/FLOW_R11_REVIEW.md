# R11 review: selective repair and timeout backoff

The owner accepted this source revision after two independent reviews on
30 October 2026. The accepted `flow.py` SHA-256 is
`e3aedfa63f844dd237b13f8a592d691de65eafeca8ed802e889d2bfe4659eae4`;
the port retains SHA-256
`57596421d916f0c49d61599653b5ab75154d64e5bc806ba1444fa5905ad025ca`.
This is source acceptance, not R11 hardware qualification. The port, wire
format, host worker and firmware are unchanged by this revision.

R10 routes both selective fast repairs and ordinary timeout retries through
the same handover settlement. Each successful handover increments the attempt
count, reanchors the timer after conservatively estimated queued carrier work,
and doubles the flight's recovery interval. A selective repair can therefore
double an interval even though that timer has never expired.

The accepted change preserves the flight's existing RTO when the handed-over
retry is selective. An ordinary timeout retry still doubles it within the same
configured bounds. Both retry kinds consume an attempt, retain all debt and
reanchor their timer after queued work. The change does not create another
early opportunity, reset to a newer adaptive global RTO, reduce the configured
minimum or increase retry limits.

The motivating profiled eight-frame SF5 candidate transferred 23,900 useful
bytes in 29.821904 seconds including batch startup, below the 3,000 B/s goal.
Two lost fast-repair attempts were followed by ordinary retry submissions
4.684173 and 4.690475 seconds later. R10's 2.22-second queued minimum had doubled
to 4.44 seconds, with the conservative queued-work estimate and host dispatch
time added before the next submission. A third lost fast repair showed the
same interval while two additional DATA frames were sent. These observations
motivate source controls; they do not prove that R11 will save a fixed amount
of time on radio or meet the throughput goal.

The explicit adapter gate still requires physical drain and checked STATUS
before a selective offer. The native original deadlines, 2-second queued hold,
5ms receive gap, 100ms reverse floor, uncertainty fencing and finite authority
remain unchanged. Ordinary timeout trains retain their existing admission and
collision-avoidance behavior. The queued 2.22-second minimum itself is unchanged.

Review controls cover a lost original and lost fast repair, conservative queued
backlog, repeated and late SACKs, preserving a flight interval distinct from the
adaptive global RTO, default/off behavior, ordinary timeout doubling, exact
owner/scheduler retry debt and the retry ceiling. Existing deferred, refused,
cancelled, UNKNOWN, terminal and full-size final-frame controls remain required.

`meshticulum_checkpoint` independently approved the exact source with 65
focused controls passing. `profile_design` passed 21 independent controls;
the identical suite produced seven expected failures and 14 passes against
R10, proving that it distinguishes the changed recovery behavior. The author
suite passed 3,462 tests with 37 skips and 49 passing subtests. The existing
[real-port controls](../tests/test_selective_retry_adapter.py) and new
[backoff controls](../tests/test_selective_retry_backoff.py) are retained with
the source. Independent loss/receiver/port controls are retained in the review
evidence. The [revision record](../src/meshticulum/flow/README.md#revision-11-selective-repair-backoff-30-october-2026)
records the explicit acceptance. Separately authorized physical evidence is
required for any R11 speed claim.
