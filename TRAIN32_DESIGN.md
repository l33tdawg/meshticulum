# Train32 prototype: static host pending ceiling

This worktree starts at560898e8bedcd96d99dee70cf3e6451740fa7faa. The source-only prototype now implements explicitMBT3/v11 capability/policy, static host pending3 and turn retirement, with compiled native/model tests. No firmware image build, install, device state, guard, authority, over-air flow or wire format has changed.

Use a new explicit local serial train32 capability and new exact data-burst policy. Retain the native queue maximum8, original2s autonomous hold,5ms RX-ready guard,100ms reverse-listen floor, frozen over-air flow/window32 and existing boot/run accounting. The new policy restricts host retained physical slots to3 throughout the train. This is a scheduling restriction, not a reduction in the native queue's maximum or an enlargement of authority. Train24 and earlier policies retain exact behavior.

The native check remains final: at actual admission time N, needed equals each nonterminal packet's full estimated airtime plus100ms start guard plus5ms gap, plus the candidate's same cost, plus max(next_start-N,0). needed must fit originalD-N. No active-packet progress credit is taken. The host does not project native time using an invented clock-rate upper bound: the existing clock protocol only states a minimum MCU rate0.99, which is insufficient for a conservative upper-now projection.

A static3 ceiling can preserve queued successors while avoiding the late8-deep conservative backlog. A sufficient illustrative admission bound near the tail is startup+29*Tcycle+refill+3*(E+105ms)+cooldown <=1980ms. With E35.920ms, cycle44.152ms and100ms conservative cooldown, the fixed terms total1803.168ms, leaving176.832ms for startup/refill/jitter. These are model premises, not guaranteed physical latencies. The native check must still REFUSE on excessive delay, without repeated paid offers or deadline renewal.

No-empty-tail proof must inspect QUEUED successors at each nonfinal RF_DONE. Having a nonempty host retained map is insufficient: a retained active/terminal record is not a queued successor. The pure simulator separates RF_DONE from the8.4ms modeled RX/setup gap, requires each next arrival strictly before preceding RF_DONE, and rejects the initial-second-packet case when dispatch is40ms versus36.4ms RF duration. Steady service tests use44.2/44.8/47.027ms cycles and6/12/24/30ms dispatch premises. All32 packets fit with at most3 retained slots, originalD1.98s and no empty terminal boundary. Arbitrarily slow dispatch or70ms service does not qualify as a successful train.

Current BurstSerialWorker._forget resets its burst deadline/count when the physical map becomes empty. Train32 must instead latch D/count until an explicit logical turn closure with the existing reverse-hold path. An unexpectedly empty turn is retired; it cannot be promoted to a fresh2s turn because the next caller happens to see an empty map. Native burst_adapter already imposes100ms whenever RF_DONE finds no queued successor, and this behavior must remain intact. Early drain/refusal evidence and all old paid costs remain visible.

Required runtime proof before activation:

- Exact capability/query/open binding rejects train24 firmware before ARM; old policies/capabilities remain byte-exact.
- New policy binds host pending limit3 and train32; no caller override to4/8 or deadline/guard reduction.
- Durable reservation occurs only after capacity/identity checks; native BUDGET still costs one known refusal and latches until exact release/reverse closure.
- Empty-map transitions cannot reset D/count in-place, including rejected writes, cancellations, delayed terminal acknowledgments and unknown writes.
- Native compiled tests verify32 packets only under original deadline, queue≤8, exact5ms/100ms guards, no shortened final RX/reverse tail and no changes to attempt/debt/error semantics.
- The frozen flow module and wire format remain unchanged. The local serial capability/policy is a new source identity and would require a separately reviewed firmware build/install later.

Pure model command: `python3 -m unittest discover -s tools/train32_model -v`. Six pure-model tests include12 normal service/refill combinations, exact native budget equality/one-us refusal, depth refusal, delayed initial replenishment, slow-service headroom refusal and immutable drained-turn closure. They are not hardware qualification or a4000B/s prediction. The observed clean16KiB baseline remains85DATA,81short gaps,3long gaps; train32 can remove some turn overhead but cannot claim unmeasured throughput.

## Implemented closure ordering

For v11 only, `_forget` preserves originalD and burstcount when the physical map empties. The runtime establishes the existing reverse-hold path, preserving any later prior hold, before explicit retirement clears those fields. Retirement samples no CLOCK and creates no authority. A later new turn samples its own deadline under the unchanged original OPEN/lease bounds. Unknown/fenced/cancelled states cannot retire.

An authenticated progressACK can arrive before delayed final terminal release. It may acknowledge an older logical flight: the marker preserves existing progress permission, not proof of coverage for the current local turn. Its marker binds OPEN token, monotonically increasing local turn generation, originalD and admission count. New queued DATA or a refusal invalidates it. If final drain matches the same marker, the runtime first establishes hold and retires, then restores the already-authorized prior hold while retaining the refusal floor. Marker consumption and turn generation prevent reuse on a later turn even when a source-model CLOCK happens to return the same deadline. Native100ms remains final regardless of logical ACK release.

Actual encrypted model pairs cover clean delivery, one dropped DATA, native budget refusal and delayed terminal release after authenticated ACK. Local-native sanitizer fixtures exercise32 transmissions with a genuinely queued successor at each nonfinalRF_DONE and exact unchanged5ms/100ms boundaries. The prototype does not promise performance under unbounded host latency.
