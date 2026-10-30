# Terminal repair trains: explicit v8 source candidate

This opt-in host policy groups distinct, already proven missing DATA frames at
end of a transfer. It is source-only and has no physical qualification result.
The current R13 flow and port, firmware, RF profile, USB pacing and native
5/100 ms receive floors remain unchanged.

The canonical selector is `policy_for_repair_train()`. Its exact v8 policy adds
`selective_repair_train: true` to all v7 prerequisites: eight physical slots,
logical window 32, repair-fill, terminal selective retry, scalar-fill pairs and
repeat retry. Earlier policy bytes and default behavior remain unchanged.
Both peers must bind the same original policy in their grants. A policy cannot
be changed inside a running session.

## Admission and stop conditions

A train can begin only after the original FINAL was handed over and its peer
receipt was accepted through the canonical ACK path, with no outbound bytes
remaining. The first repair retains the existing exact local physical drain,
release and STATUS check. It is independently selected by the unchanged flow
API and paid through the owner, cipher, journal and native queue.

Each subsequent normal host step may offer one further proven hole. A bounded
set tracks the distinct logical domains in this turn, including the first
repair. A domain cannot be offered again in the same turn even after its prior
physical record is released. Existing pending-domain exclusion also remains.
At most eight accepted packets belong to the turn. Each call rechecks the
current canonical evidence, attempt ceiling, incarnation and timeout; accepted
R13 retry handover clears the previous evidence and opens its normal fresh
proof epoch. This policy neither fabricates proof nor modifies a retry timer.

Any received input ends an active repair train, including input that later
fails authentication. A held offer, exhausted capacity, mismatched host/worker
physical inventory, closed authority, due ordinary retry, refusal or UNKNOWN
also stops further offers. Clock and authority checks run for each step; a
carrier-boundary check rejects a duplicate, non-DATA or newly due offer before
cipher sealing or physical reservation. Normal framework reservations and
known-unsent handles remain accounted.

When the turn ends, the original reverse-control hold applies. A genuine
progress ACK may release that hold as before. A later train must pass a new
exact physical drain gate. ACK/CONTROL behavior and bulk repair plus fresh-DATA
fill retain v7 behavior. Missing original FINAL receipt cannot enable this
terminal-only feature.

## Evidence and limits

The motivating retained run08 completed six successful single-packet repairs
after original FINAL had physically settled. Its FINAL-terminal to authenticated
receipt interval was 2,305,242 µs, including 400,764 µs in host queue-to-terminal
envelopes and 1,904,478 µs outside them. These are observed sender-clock intervals,
not RF airtime or a promise that the entire remainder can be removed.

The source comparison uses the same signed 23,900-byte useful input and 128-DATA
response geometry in both arms, with fresh fixtures and unchanged original
attempt/airtime/deadline bounds. The case08-tail scenario injects only the six
terminal logical holes 114, 115, 116, 119, 124 and 125; it does not replay all measured
losses or timing. Compare with:

```sh
python tools/benchmarks/compare_sf5_repair_train.py --repo . --output /tmp/repair-train.json
```

The model preserves real owner/flow/crypto/journal paths and modeled scalar USB
writes. Native setup and receive priority are idealized; only explicit host
cost injections advance its clock. Its discovery-to-cleanup interval excludes
real process startup and full application publication. A smaller model time
cannot establish the physical useful-file throughput goal.

`tests/test_burst_repair_train.py` covers exact policy binding, unchanged limits,
initial physical release/boot checks, per-hole nonce and debt, bounded distinct
trains, ingress, deferred/refused/UNKNOWN outcomes, timeout crossings and lost
DATA/ACK controls. Previous-policy suites remain required. A long injected loss
run has a pre-existing model collision before repair in both arms; the comparison
retains that result instead of assuming every modeled case is collision-free.
