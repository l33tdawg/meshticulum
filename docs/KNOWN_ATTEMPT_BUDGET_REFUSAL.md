# Known host attempt-budget refusal

A host journal exhaustion discovered before reservation now terminates a burst
flow with `bench_host_attempt_budget_exhausted`. It is a known carrier refusal,
not an uncertain handover of that candidate. It does not complete the file.

The journal emits a distinct `AttemptBudgetExhausted` only at its checked budget
gate, before allocating a request or inserting debt. The signal escapes only
when transaction rollback succeeds. The burst worker records the exact error,
journal and immutable candidate bytes at that reservation boundary. The adapter
requires that matching proof before settling the carrier as refused and latching
a terminal diagnostic. Exception names or error messages alone are insufficient.
A later step cannot turn the exhausted operation into more retries.

The result proves no DATA submission for the refused candidate. Existing setup,
CLOCK, release or query commands may already have occurred. Previously admitted
packets remain charged, even when still pending or subsequently cancelled. On this known-refusal exit only, cleanup first drains already-admitted work for
at most2.5seconds, bounded further by the remaining original worker deadline.
No further DATA is submitted and native deadlines are unchanged. Drain failure
retains its error, marks ambiguous outcomes through the normal worker path and
attempts cancellation; a cancellation failure is retained alongside the drain
error. Other failure cleanup is unchanged. A
fenced DISARM response can still produce a cleanup error; this change does not
reinterpret that response as successful closure.

Other journal faults, rollback failure, partial writes and post-admission errors
retain existing UNKNOWN behavior. Frozen flow and flow-port code, RF attempt and
airtime ceilings, original deadlines, native guards and authority are unchanged.
The explicit known-refusal classification is restricted to burst DATA; scalar
transmission behavior is unchanged.

Tests use a real encrypted paired flow and native protocol model: three admitted
attempts, one RF_DONE and two pending, followed by a refused fourth reservation.
They cover marker off/on, a sealed marked tail, terminal retry refusal, unchanged
paid debt and cancellation, rollback failure, post-admission error, and both
attempt-count and estimated-airtime limits. These are source regressions, not
hardware throughput or physical outcome evidence.
