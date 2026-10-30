# Throughput pipeline work

The active goal is at least 3,000 useful file bytes per second, including
sender process startup through authenticated receipt, on comparable finite
workloads with publisher and whole-file verification. Strictly above 2,800
bytes/s remains the net-win checkpoint. Neither has been established. The
[first eight-frame hardware sample](../benchmarks/2026-10-30-sf5-eight-profile/README.md)
verified 23,900 useful bytes in 29.821904 seconds including startup, or
801.424349 useful B/s. It retained 14 DATA retries and all attempt debt. A
[matched R11 sample](../benchmarks/2026-10-30-sf5-r11/README.md) completed the same
useful bytes at 1,053.284286 B/s in 22.690930 seconds, with 10 DATA retries.
The different loss patterns prevent an isolated causal gain estimate. A
[subsequent logical-window32 sample](../benchmarks/2026-10-30-sf5-window32-profile/README.md)
verified the same useful bytes at 1,276.917174 B/s in 18.716954 seconds, with
the physical train still eight. The [selective-repair fill sample](../benchmarks/2026-10-30-sf5-repair-fill-profile/README.md)
completed in 19.537686 seconds including startup, or 1,223.276902 useful B/s,
with 13 DATA retries and nine repair-fill turns. Its loss pattern differed from
the earlier samples; this is not an isolated causal performance comparison.
The subsequent [terminal-policy/native-RX diagnostic sample](../benchmarks/2026-10-30-sf5-terminal-retry-profile/README.md)
verified the same useful bytes in 11.793771 seconds including startup, or
2,026.493477 B/s, with six DATA retries. All six used the existing bulk
selective path; the enabled terminal threshold was not exercised. Firmware
and loss patterns differed. The subsequent [8 MHz SPI comparison](../benchmarks/2026-10-30-sf5-spi8-profile/README.md)
kept the same host code and v5 policy, and verified the file in 23.765471
seconds including startup, or 1,005.660692 B/s. DATA retries rose to 26;
the differing loss pattern prevents an isolated estimate of the SPI effect.
The separate [v6 scalar-fill pair sample](../benchmarks/2026-10-30-sf5-scalar-fill-pair-profile/README.md)
retained the SPI8 firmware and both MCU boots, and verified the file in
19.551794 seconds including startup, or 1,222.394221 B/s. It recorded 17 extra
offers and 26 DATA retries. Different loss and recovery patterns prevent
causal attribution of its higher rate to v6. All seven samples miss the target.

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

Production wiring is not yet justified by a demonstrated timing benefit. DATA
and STAGE each have a 59-byte body header, so the staged ciphertext retains the
same USB pacing cost. The current prototype adds BEGIN/COMMIT, waits for all
members before first RF, and retains one job until complete release. A proposed
overlap design needs its own whole-path comparison. The next measurement should
isolate native setup and host sealing costs before selecting a larger owner
revision on the assumption that batching is faster.

The default queue depth and train length remain four. At SF5/BW500/CR4/5 with a
12-symbol preamble, a full-frame
steady-cycle estimate is four 193-byte transport payloads divided by four
34,640 us airtimes, three 5 ms receive gaps and one 100 ms reverse turn: about
3,044.6 transport bytes/s. In one actual signed-envelope fixture, a full 23,900
useful-byte part occupies 24,565 transport bytes. Applying that useful fraction
gives about 2,962.2 useful bytes/s before host work, initial startup or losses.
These are optimistic calculations, not measured capacity guarantees.

The [offline native timing analysis](../benchmarks/2026-10-30-sf5-recovery/README.md#offline-native-timing-analysis)
identifies another cost in the retained trials. In 18 transitions where the next
packet was already queued, local RF_DONE to next STARTED averaged 10.630 ms:
6.780 ms before ISSUED, then 3.850 ms in checked transmit setup. The pre-issue
bucket includes RX rearm and the full listening floor; neither bucket is proven
wholly removable. [Bounded native phase profiling](MCU_NATIVE_PROFILING.md)
now separates those calls in an opt-in, separately identified firmware build.
It has no reset command and can be queried only after disarm and complete
physical reconciliation. Host `cipher_seal` and `cipher_open` timings include
their durable nonce/replay transactions and authorization, alongside crypto;
they do not claim to isolate CPU encryption time.

An [explicit eight-frame scalar train](MCU_EIGHT_TRAIN.md) now has one finite
verified-file sample. Keeping the
same gap and reverse-turn durations would provide an ideal estimate of about
3,645 useful bytes/s with that fixture, leaving some margin for the target.
It binds a distinct policy and local capability, with consistent flow,
reassembly and native bounds. Each ciphertext still takes an individual durable
reservation, queue acceptance and terminal release. The frozen flow and owner
APIs already support this window; they remain unchanged. No aggregate serial
frame or atomic release vector is introduced. The 2-second hold and 5/100 ms
receive floors remain unchanged, including when an expired queued suffix turns
an earlier completion into the actual tail. Source controls cover ACK loss,
every DATA position, underfilled trains, UNKNOWN, expiry and legacy four-frame
behavior. Both targets built; the two Heltec units passed installation and
idle diagnostic checks. Sustained physical performance remains unqualified.

The [22-case scalar four/eight source comparison](../benchmarks/2026-10-30-sf5-eight-source/README.md)
uses the same full signed part and fault boundaries. Its clean idealized native
schedule rises from 2,924.5 to 3,592.8 modeled useful B/s; with 40/10 ms injected
sender/receiver seal work, it reaches 2,873.7 B/s. Real process startup and CPU/
fsync time are excluded, so these are not physical acceptance results.

The first measured eight-frame run spent 20.952743 seconds without physical
DATA outstanding. Native ISSUED→STARTED averaged about 4.038 ms per DATA frame;
TX_DONE→RX-return averaged about 1.349 ms. These software-boundary measurements
are inclusive where documented and cannot be added as disjoint costs. Three
lost selective repairs fell back to roughly 4.7-second waits. A focused retry
review produced [flow revision 11](FLOW_R11_REVIEW.md): selective repair keeps
its existing RTO while actual timeout retries retain backoff. Its two independent
reviews passed. Its matched hardware sample spent 14.125908 seconds without
physical DATA outstanding; native setup still averaged about 4.060 ms. The
remaining recovery stalls require further work.

The subsequent window32 sample reduced the interval without host-observed DATA
outstanding to 9.707643 seconds. The repair-fill sample recorded 10.379994 seconds
on that boundary, including two late gaps of 2.244117 and 4.366965 seconds
(6.611082 seconds total). Its nine selective repairs each shared a group with
seven new DATA frames, but remaining tail holes still waited for recovery.
The late waits are consistent with the source timeout policy; the trace did
not log the exact internal retry due times. Receive-drop counts do not identify
the cause of a missing packet. Host terminal delivery follows the worker's
normal release flush, so these envelope measurements are not RF airtime.

The terminal-policy sample spent 3.417723 seconds without host-observed DATA
outstanding and 7.626313 seconds inside DATA envelopes. Its retained native
trace includes six empty-queue transitions within eight-frame groups, each
followed by the existing roughly 100 ms listening floor. Both receiver and
sender drops were exclusively header-only IRQ restarts (20 and three);
these categories do not identify missing packets or their RF cause. Keeping
the native queue fed and investigating receive handling are the next measured
leads. The [terminal recovery](TERMINAL_RETRY.md) source controls separately
exercise late holes absent from this sample.

The SPI8 sample's native transmit-start call averaged 2.237 ms versus the
prior 3.367 ms, while its interval without DATA outstanding grew to
14.017466 seconds. Faster setup did not yield higher file goodput in this
sample. The [explicit scalar-fill pair](SCALAR_FILL_PAIR.md) then recorded
17 extra offers in its separate physical sample. Its interval without DATA
outstanding was 10.346933 seconds. Neither that interval nor the observed
rate increase isolates the benefit of an extra offer; the matched source
model remains separate evidence.

Further work examines queue refill, receive handling and release/host costs while keeping
canonical ACK evidence, one selective offer per flight, paid attempts and
original finite deadlines. A source model or a faster isolated number cannot
qualify the 3,000 useful B/s goal.

Further physical comparisons must use equal useful workloads, exact installed
source/firmware correspondence and original finite budgets. Retain failed runs
and complete allocations. No modeled result renews old authority, resets native
counters or qualifies a release artifact.

The eight-frame and native-profile candidate passed **3,453 tests, 37 skips
and 49 subtests** locally, with green Python 3.11–3.13 and crypto-prototype CI.
Source tests and physical samples retain their separate scopes.
The subsequent reviewed retry revision passed 3,462 tests, 37 skips and 49
subtests; its independent controls distinguish the previous backoff behavior.
