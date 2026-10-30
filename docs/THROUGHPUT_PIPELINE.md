# Throughput pipeline work

The acceptance target is above 2,800 useful file bytes per second, including
sender process startup, on comparable finite workloads; 3,000 bytes/s is the
stretch. Neither has been established by the current physical recovery trial. The
[latest physical recovery case](../benchmarks/2026-10-30-sf5-recovery/README.md)
uses the earlier exact candidate and must not acquire the qualification of
subsequent source-only changes.

## Existing piece and recovery behavior

The [byte-bundle example](../examples/BUNDLE.md) already supports signed,
content-addressed parts accepted in any order, reverified missing-part inventory,
persistent verified-piece continuation, and publication only after all parts and
the signed whole-file length/hash verify. The generic [swarm planner](../src/meshticulum/resources/swarm.py)
also has rarest-first selection and bounded path/resource-aware parallel batches.
A single radio link still shares one half-duplex resource; scheduling more
sources does not create extra airtime on that resource.

The flow has bounded reassembly and selective ACKs. Its reviewed explicit fast
repair uses three later accepted receipts to justify one early retransmission.
The [MB1 binary envelope](../src/meshticulum/resources/bundle_wire.py) already
avoids Base64/padding on the radio. The signed response still carries metadata;
small parts pay a larger fraction of that cost than full-sized parts.

## Host readiness and refill

The [host pump](../src/meshticulum/bench/host_pump.py) preserves one reader and
one owner. Optional serial readiness wakes the next ordinary step when bytes
arrive, without consuming bytes in the wait. Actionable decoded RECEIVE or
terminal work also wakes it; unmatched command replies do not. Older channels
and HAT transports retain a bounded sleep fallback.

Each wait remains at most 5 ms and is clamped to the original file/application
deadline. This keeps the current external-owner/revocation watchdog. A successful
queue admission can trigger another ordinary step when local capacity and the
existing send-time guard permit, at most four consecutive steps. Readiness and
capacity hints grant no authority, physical completion or credit. A native
RF_DONE still needs durable host settlement and exact release before its slot
becomes reusable. Cleanup and exception ordering remain unchanged.

The six paired source cases retain the same charged attempts and complete
under the same injected losses. Timing changes are modest and mixed: the clean
nine-DATA case saves 3.765 modeled ms, while one 40/10 ms injected-host-cost case
regresses 1.900 modeled ms. Real crypto and journals execute, but modeled time
advances only through modeled events and explicit injected costs. These are
source controls, not RF measurements or evidence of 3 kB/s.

Readiness waits are accounted in `serial_read_wait`; actual fallback sleeps stay
in `host_loop_sleep`. A reduction in the latter alone does not measure CPU time
saved. Inclusive phase aggregates overlap and cannot be added into wall time.

## MCU delegation and the remaining margin

The [finite batch prototype](MCU_BATCH_PROTOTYPE.md) stages a complete, immutable
vector before one commit and one exact terminal-release vector. It is dormant:
current workers, firmware dispatch, wire capability and owner contract do not
consume it. It demonstrates bounded validation/accounting behavior in source.
Production integration needs the reviewed multi-emission owner contract and
atomic durable reservation described in that document.

Current queue depth and train length remain four. At SF5/BW500, a full-frame
steady-cycle estimate is four 193-byte transport payloads divided by four
34,640 us airtimes, three 5 ms receive gaps and one 100 ms reverse turn: about
3,044.6 transport bytes/s. In one actual signed-envelope fixture, a full 23,900
useful-byte part occupies 24,565 transport bytes. Applying that useful fraction
gives about 2,962.2 useful bytes/s before host work, initial startup or losses.
These are optimistic calculations, not measured capacity guarantees.

An eight-frame train is a separate proposed protocol experiment. Keeping the
same gap and reverse-turn durations would provide an ideal estimate of about
3,645 useful bytes/s with that fixture, leaving some margin for the target.
It requires an explicitly negotiated capability, consistent logical/reassembly
and native memory bounds, original finite delegation, and independent ACK-loss,
revocation, UNKNOWN/debt and legacy four-frame controls. Increasing only the
logical window cannot remove the current physical train boundary. An eight-entry
terminal-release vector would also exceed the current 320-byte body limit; its
framing and atomic-release protocol need explicit design, not a constant change.

The next physical comparison must use equal useful workloads, exact installed
source/firmware correspondence and original finite budgets. Retain failed runs
and complete allocations. No modeled result renews old authority, resets native
counters or qualifies a release artifact.

The combined host-pump and dormant batch-prototype source passed **3,276 tests,
37 skips and 49 subtests** locally. Separate independent edge controls supplement
that run. These source results supply no new hardware qualification.
