# Train24 source experiment

This opt-in source candidate admits up to 24 DATA attempts in one physical
train while retaining eight native queue slots. It is an experiment toward
higher useful throughput. No device execution or 4,000 useful B/s result is
established by these source tests.

Select `meshticulum.bench.burst_protocol.policy_for_train24()` when preparing
an explicitly authorized grant. The exact policy has schema
`meshticulum.data-burst-policy.v10`, `train_frames=24`, and
`dynamic_train_admission=true`. The existing public transfer example reads the
policy from the bound grant; there is no CLI override that upgrades an old
grant. Both peers authenticate the entire policy through their route binding.
Existing v1–v9 policies and their capability bytes retain their behavior.

The native capability is `MBT2`, queue depth eight, train limit 24. Its request
and OPEN prefix is `08 18`; train12 remains `MBT1` / `08 0c`. Capability mismatch
fails before ARM. Source preparation must compute a new firmware identity from
the changed application inputs. Do not label the candidate as the installed
Alpha4 firmware `mtb1-e2eaa9f94c`, or infer installed bytes from source identity.

## Dynamic admission and unchanged bounds

All previous policies retain their conservative whole-train fit check. V10
explicitly uses a different admission contract: the worst-case pending queue
of eight must fit, and the native adapter separately checks each refill against
the remaining **same original train deadline**. A complete 24-attempt train is
not guaranteed. Slow host work, reverse traffic or driver delay may shorten it.

The maximum autonomous hold remains two seconds, with the existing clock
margin and rate mapping. Queue depth remains eight; inter-packet receive and
reverse-listen floors remain 5ms and 100ms. Original OPEN, ARM, application and
grant deadlines are unchanged. MCU boot caps, per-operation caps, irreversible
attempt/airtime debt and uncertainty fencing are unchanged. Native admission
and dispatch remain authoritative even when host calculations predict a fit.

A native `BUDGET` refusal (`06`) is a charged host attempt, even when no native
RF attempt was admitted. For v10, the worker latches that train closed after the
first such refusal. Further DATA offers cannot spend retries while retained
slots remain. The runtime drains and collects exact physical outcomes, obtains
confirmed releases and STATUS, then retires the latch and gives the receiver
the entire existing sender-control hold. Authenticated ACK progress can update
the logical flow during this hold but cannot shorten it. No new CLOCK mapping
is sampled until a later permitted DATA offer, after physical release. Pending
logical flights stay with the unchanged frozen flow; their original retry
limits and deadlines remain binding. Unknown writes take the existing fenced
cleanup path, never the known-refusal recovery path.

Existing repair-fill, terminal repair and saturated-window stall recovery are
explicitly enabled for v10. The frozen `flow.py` and `flow_port.py` remain
unchanged. The receiver still acknowledges short trains after its 250ms quiet
period, with a two-second maximum coalescing hold and unchanged final-ACK
recovery.

## Validation and measurement

`tests/test_burst_train24.py` exercises exact policy/capability binding,
unchanged old policy, partial-write UNKNOWN behavior, refused-turn debt and
drain, logical-flight preservation, full reverse hold, DATA loss, final-ACK
loss and slow-host operation. `tests/test_burst_train24_native.py` compiles the
actual native adapter with sanitizers and profile modes on/off, checking slot
reuse through 24 admissions, original deadlines, late refill refusal, expiry,
cancellation and unchanged reverse floors. All radio and clock inputs in these
tests are modeled.

Source validation must include the standalone suite and
`tools/verify_frozen_flow.py`. A later physical campaign needs a newly reviewed
firmware/source identity, exact current ownership/counters, finite fresh scope,
nonces and claims, retained failures, and independent whole-file verification.
Measure useful application bytes from the actual sender process-start boundary
through authenticated final receipt. Report physical drain, endpoint closure
and reader verification separately. Per-part invocation times and modeled
throughput cannot substitute for that metric. Longer trains alone are not a
promise that the 4,000 useful B/s target will be reached.
