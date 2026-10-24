# Host-backed DATA burst integration contract

This is the implementation contract for the opt-in LoRa DATA path. It does not
authorize hardware installation or RF. Legacy mode remains the default. Linux
retains session keys, authenticated ACKs, credit and retries.

## Admission and ownership

A private immutable grant opts in with exactly this `data_burst` record:

```json
{"schema":"meshticulum.data-burst-policy.v1","handover":"irrevocably-charged-at-queue-acceptance","max_depth":4,"max_hold_us":2000000,"inter_packet_rx_us":5000,"reverse_listen_us":200000}
```

The record participates in the original shared route/grant digest. It permits
only locally classified DATA ciphertext. Queue acceptance is an irrevocably
charged in-flight carrier handover: accepted ciphertext may start before its
fixed deadline even after host revocation or disconnection. A delivered CANCEL
stops future claims and contains an uncertain active operation. Disconnected USB
cannot promise instantaneous cancellation. This deployment policy needs explicit
owner acceptance in the later finite hardware scope; source code does not grant
it. Grants without this record retain live host checks on the synchronous path.

The adapter independently computes ciphertext SHA-256 and airtime from the
current admitted LoRa profile. One shared MCU boot state owns all legacy and
queued attempt/airtime debt, mutation highwater, monotonic watermark, fence and
containment. Never reset/refund these through OPEN, CANCEL, ACK or restart.

## Serial records

Use the existing boot/request-bound MB1 frame and CRC. Integers are big-endian.
Bodies never exceed 320 bytes. These records establish local serial evidence;
they do not authenticate a remote peer.

| Command | Value | Reply |
| --- | ---: | --- |
| BURST_CAPABILITIES | 11 | 139 (`0x8b`) |
| BURST_CLOCK | 12 | 140 (`0x8c`) |
| BURST_OPEN | 13 | 141 (`0x8d`) |
| BURST_DATA | 14 | BURST_EVENT, or existing REFUSED |
| BURST_QUERY | 15 | retained BURST_EVENT, or REFUSED |
| BURST_ACK | 16 | 142 (`0x8e`) |
| BURST_CANCEL | 17 | 143 (`0x8f`) |
| BURST_STATUS | 18 | 148 (`0x94`) |
| BURST_EVENT | — | 147 (`0x93`) |

- CAPABILITIES: empty request; exact 24-byte reply `!4sBBHIIII` = magic
  `MBQ1`, depth 4, flags 7 (LoRa DATA, stable event timestamps, exact ACK replay),
  packet bytes 255, hold 2,000,000 us, start guard 100,000 us, inter-packet RX
  dwell 5,000 us, post-burst reverse listen 200,000 us. Same discovered firmware
  and boot must match; unsupported capability cannot reach OPEN or DATA.
- CLOCK: empty request; reply `!QQQ` = current MCU monotonic microseconds,
  original successful ARM mutation request, immutable ARM end. Read-only;
  returns zero ARM/end when unarmed. It never extends a lease or mutation mark.
- OPEN: request `!32sQQ` = immutable grant/profile context digest, exact ARM
  request, mapped original deadline. Header request is the new OPEN token.
  Reply echoes all 48 body bytes. The deadline must be current and no later
  than the original ARM end. No reopen while retained/active work exists.
- DATA: header request is the prepaid physical attempt ID. Body
  `!QQQBH32s` followed by exact ciphertext = OPEN token, shared burst deadline,
  independent estimate, local DATA kind 1, size, SHA-256. Body is 60–314 bytes.
  Firmware compares its own hash/estimate. First item fixes the common deadline,
  at most two seconds from admission; later items cannot extend it. Admission
  accounts pending airtime/guards and native RX cooldown before the deadline.
- EVENT: header request is the physical attempt ID. Body `!QBQ32sQHQQIh` =
  OPEN token, stage, stable MCU event timestamp, digest, estimate, size,
  original packet deadline, driver elapsed, IRQ flags, driver error (81 bytes).
  Stages match the source queue: QUEUED=1, ISSUED=2, STARTED=3, RF_DONE=4,
  NOT_SENT=5, UNKNOWN=6. QUEUED/ISSUED/NOT_SENT have zero driver fields;
  UNKNOWN may retain diagnostic driver fields without claiming completion.
  State transitions assign timestamps once; QUERY/replay cannot restamp them.
  Retain the latest exact state until terminal ACK. Missing intermediate reports
  do not invalidate exact terminal evidence. UNKNOWN is sticky.
- QUERY: header request is the referenced physical attempt; body `!Q` OPEN
  token. Read-only; emits the retained exact event and never changes highwater.
- ACK: fresh command request, body `!QQBQ32s` = OPEN token, physical attempt,
  terminal stage, stable event timestamp, digest (57 bytes). Reply echoes body.
  Release only the exact terminal record after checked containment. Keep four
  bounded exact ACK receipts, sufficient for the one serialized outstanding ACK
  transaction. Exact replay is idempotent, including after slot release. A fresh
  command may repeat the same exact release identity; changed identity conflicts.
- CANCEL: fresh command request, body `!Q` OPEN token. Stop claims immediately;
  queued becomes NOT_SENT, issued/started becomes UNKNOWN; debt persists. Try
  checked radio containment and retry failures from the loop. Reply `!QBB` =
  token, sticky fenced flag, containment-required flag at the checked attempt.
  Retain the exact command reply for replay; STATUS observes later containment.
- STATUS: body `!Q` token (zero permits read-only discovery of current context).
  Reply `!Q32sQQBBBBBQQIQ` = token, context, original ARM request, OPEN deadline,
  pending count, retained count, transmitting, fenced, containment-required,
  MCU now, next-start-not-before, shared attempts, shared airtime (89 bytes).
  An empty closed queue has token/context/deadline zero and still reports debt.

Mutation ordering remains global across ARM, OPEN, DATA, ACK, CANCEL, DISARM and
legacy TX. Exact retained replays precede stale-request refusal and never repeat
RF. Reserve/write one DATA mutation at a time; allocate ACK mutations only after
older reserved DATA requests have been written or settled unsent. Do not
pre-reserve four IDs then interleave a newer ACK ahead of those writes.

## Clock, RX and closure

Capture host send/receive times around each CLOCK response. Reject a reversed
clock or an RTT over 50 ms. The explicit rate assumption is at least 0.99 MCU
microseconds per host microsecond. Map the original host authority end as MCU
snapshot plus floor(0.99 times remaining host microseconds at reply), less 20 ms
margin; also cap by ARM end. This rate conversion covers the whole OPEN horizon.
Every new burst uses a fresh CLOCK observation and tightens
against the original OPEN cap and current host authority end. Its common
deadline is at most snapshot + 1,980,000 us. No existing deadline is recomputed
upward. The reply-time mapping is already conservative for sampling latency;
the RTT bound is an envelope check. A fresh preflight must retain boot-bound
bracketed clock observations about ten seconds apart and check their rate
interval against the stated bound. Such observations are evidence of the
current setup, not a guarantee of future oscillator behavior.

Do not issue while RX_DONE evidence is pending or output is blocked. Drain RX
again at standby before touching the TX FIFO. Retain at least 5 ms receive dwell
between DATA operations, and let a detected valid header finish or time out
before the next claim. After four admissions or an empty queue at RF completion,
retain at least 200 ms reverse listen. The existing host
coalesces ACKs for a finite four-frame train; do not move ACK snapshots into the
queue. Neither receive dwell nor reverse listen extends the original authority.

Host routing is keyed by physical request and accepts events while other serial
commands wait. Durable reservation includes immutable queue metadata before any
write. Queue acceptance leaves the attempt RESERVED. Commit physical terminal
settlement before sending terminal ACK. Keep physical records after peer ACK or
transport retirement; suppress duplicate pending DATA by flow/generation/sequence
domain and verify exact plaintext digest before sealing another ciphertext.

Build queue-aware retry/profile bounds before constructing the authority and
frozen scheduler. No second handover callback occurs at RF_DONE. Successful
closure requires every physical result settled, every release confirmed, and
STATUS showing no pending/retained/active/containment work. Checked DISARM still
closes the radio in cleanup. Reads, settlement, exact release and containment can
finish after RF authority expires, but cannot start new RF.

Restart retains the existing RESERVED→UNKNOWN and sticky host fence rule. Do not
resume queued ciphertext or upgrade recovered UNKNOWN from a later report.
Evidence and containment may still be reconciled through the held exact owner.
Firmware defaults to queue-closed. Hardware comparison must preserve preflash
debt and use fresh finite deployment/RF authority; compare legacy and opt-in
transfers with the same rebuilt firmware before inferring mode gains.
