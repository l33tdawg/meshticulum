# Thirty-two logical frames over eight-frame radio trains

This explicit source policy lets a host retain more authenticated DATA behind a
missing frame while keeping the MCU queue and each radio train at eight frames.
It changes host credit and reassembly bounds. It does not enlarge native memory,
change the serial protocol or qualify the 3,000 useful file bytes/s target.

## Exact admission

Construct the original shared plan with
`meshticulum.bench.burst_protocol.policy_for_flow_window(32)`:

```json
{"schema":"meshticulum.data-burst-policy.v3","handover":"irrevocably-charged-at-queue-acceptance","max_depth":8,"max_hold_us":2000000,"inter_packet_rx_us":5000,"reverse_listen_us":100000,"train_frames":8,"flow_window_frames":32}
```

Only this exact new record is accepted. Both endpoints must bind it into their
original route/grant digest; a v2 peer cannot authenticate a v3 frame. Copies
cannot change an admitted plan. The adapter requires the original v3 contract
and matching worker policy. It refuses a smaller substituted contract instead
of silently reducing the requested window.

`policy_for_depth()` still selects v1/four. `policy_for_depth(8)` still selects
v2/eight, including its eight-frame logical window. V3 requires the same SF5 /
BW500 admission and MBQ8 firmware capability as v2. Capability, OPEN8, DATA,
event and release records keep their formats; their bound context digest
changes with the admitted policy. Old SDKs reject the unknown policy.

The existing contract admits a 6,656-byte owner credit ceiling (`208 × 32`) and
32 reassembly positions. Initial byte credit remains zero: the authenticated
receiver establishes the actual snapshot. Total object size, logical frame
count, retry ceiling, role attempt/airtime caps, deadlines, session identity and
single-use admission remain the original values. No extra RF allowance is
created by the larger logical window.

## Useful work behind a hole, within a fixed bound

With frame 0 missing and frames 1–7 buffered, an eight-frame receiver advertises
one available position. A 32-frame receiver advertises 25, allowing the sender
to retain the hole and admit up to 24 further frames. Those frames still travel
in groups of at most eight.

At 31 buffered frames the receiver again advertises one position. Offset 31 is
admissible; offset 32 is outside this reassembly window even though the SACK
field contains 32 bits. The exact bitmap is `fffffffe`. Filling the hole drains
the prefix in order. Later bytes are not delivered to the application ahead of
that hole, and an unrepaired hole still stops useful completion.

At the current 193-byte DATA quantum this can retain 4,632 additional payload
bytes compared with the seven-frame reorder case, plus host object overhead.
The conservative contract counts 208 bytes per logical owner position. No
32-slot MCU allocation is introduced.

## Radio and retry boundaries stay separate

The adapter's `burst_frames` remains eight. Full-train ACK coalescing stays
eight, partial-train quiet stays 250 ms, and its maximum hold stays two seconds.
The sender still yields a reverse turn after eight accepted frames. A selective
repair still requires checked physical drain and retains its existing reverse
turn hold. A larger logical grant does not shorten that hold or authorize a
second early repair.

The native queue retains its checked 5 ms receive dwell and 100 ms train/empty
tail floor, original two-second autonomous deadline and exact physical release
requirements. Slow refill can still produce an empty tail. Every retry receives
a fresh outer nonce and its own durable attempt reservation. UNKNOWN remains
sticky, and closure never refunds charged work.

The accepted [R11 flow and port](FLOW_R11_REVIEW.md) are unchanged. Existing SACK
and canonical credit handling support this window. The 256-counter durable
outer replay window is also unchanged; logical reordering is not permission to
replay old ciphertext. This policy does not rebase retry timers on RF_DONE or
reduce the current retry floor.

## Source verification and limits

[The matched source comparison](../benchmarks/2026-10-30-sf5-window32-source/README.md)
uses identical signed input, DATA geometry, injected losses and original role
caps for logical windows eight and 32. It keeps physical train depth eight.
It shows bounded overlap after a lost hole and repair, while clean transfers,
file-tail loss and injected host-cost cases remain unchanged. It is not a
paired hardware measurement and excludes actual process startup time.

`test_burst_window32.py` covers exact admission, old defaults, mismatched
contracts and peers, credit at offsets 31/32, ordered delivery, eight-frame
reverse turns, DATA/ACK loss, slow refill, fresh nonces, replay refusal,
revocation, expiry, original attempt caps and uncertain eighth writes.
The independent-host model keeps real flow, owner, cipher, journal and serial
code; radio clocks and RX setup are modeled. Physical qualification needs a
fresh finite scope, the same useful-file/startup/receipt boundary and complete
publisher, journal and physical closure evidence.
