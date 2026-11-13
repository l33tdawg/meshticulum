# Optional host pacing of repeated selective repair

`radio_file transfer --confirm-receipt --pace-selective-repeats` enables an
experimental host restraint. Both options default off. Receipt confirmation
still uses the shared original file context and durable receiver acceptance.
The pacing flag may be supplied on both peers; only sender repair scheduling
uses it. It creates no additional authority or deadline.

The first early repair of each logical flight is unchanged. After an accepted
early repair, the host remembers that flight's existing `sent_at + rto` boundary,
including the existing carrier airtime adjustment. Another early repair of that
flight waits until this fixed boundary. Later ACKs do not renew the interval.
Refused and cancelled offers start no interval; deferred offers start one only
after confirmed handover. Retirement removes the record; a reused sequence
number cannot inherit another flight's interval.

Both the main selective offer and grouped terminal/stall repairs use the same
read-only check. It follows the frozen flow's exact candidate ordering. If the
next candidate is waiting, the host conservatively waits instead of skipping
it: the unmodified core would still choose that candidate. Other repairs may
therefore wait too. A grouped train ends through its existing reverse hold;
the ordinary pump remains active and may perform its existing timeout recovery.
Once that timeout occurs, its original backoff and finite attempt ceiling remain
in force. No evidence, RTO, credit, debt, attempt limit, or scope is edited.

The deterministic fresh-SACK loss model in `test_selective_repeat_pacing.py`
spends all six attempts with unpaced repeated early repairs. With pacing, the
same proof sequence spends one initial attempt and one early repair, then
reaches ordinary timeout recovery with attempts still available. This is a
model of scheduling, not physical delivery or throughput. It does not establish
that pacing fixes the observed RF omissions or achieves 4,000 useful bytes/s.
