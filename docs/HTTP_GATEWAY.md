# HTTP over radio: 30-second browser path

On 18 October 2026, the lab carried bounded HTTP/1.x requests from a
radio-only endpoint to a second endpoint with ordinary internet access, fetched
the pages, and carried the responses back over the same direct GFSK250 radio
path. A `links` text browser rendered each page with its normal **30-second**
receive timeout.

This is an application gateway experiment, not transparent IP routing. It uses
two fresh authenticated radio grants per request: one for the request and one
for the response. The response is bounded to 16 KiB, the internet fetch has a
20-second timeout, and the gateway accepts only public `http://` targets. It
refuses credentials, private or loopback addresses, and HTTPS `CONNECT`.

## Measured result

| Item | Result |
| --- | --- |
| URLs | `http://example.com/` and `http://example.org/` |
| HTTP status | `200` on all runs |
| Body | `577` bytes, SHA-256 `25ddf2c883e0d1958ea971d279a7e4f0fd446724ee3db7db19dadabd4a62e484` |
| Request carried over radio | `83` bytes |
| Response carried over radio | `676` bytes |
| Request direction | Receiver verified; sender accepted the authenticated peer receipt |
| Response direction | Receiver verified; sender accepted the authenticated peer receipt |
| One-shot control, 3-second startup gap | **17.559 s** to the final authenticated response receipt |
| Text browser, 30-second timeout, run 1 | **Passed** in `17.99` s wall time (gateway `17.08` s) |
| Text browser, 30-second timeout, run 2 | **Passed** in `17.83` s wall time (gateway `16.90` s) |
| Text browser, 30-second timeout, run 3 | **Passed** in `17.77` s wall time (gateway `16.81` s) |
| Pre-fix control, 30-second timeout | Failed after `52.834` s with `Error reading from socket` |
| Pre-fix control, 90-second timeout | Passed after `53.296` s |

The three browser runs all returned the same 577-byte body hash and verified
both radio directions. The 30-second target now has about 13 seconds of margin
on this near-bench path.

## What it proves

- A text browser can consume a public HTTP resource through a radio gateway.
- The request and response are separately authenticated and verified in both
  directions.
- Unit A can use its ordinary internet connection on behalf of unit C without
  exposing a general-purpose proxy or IP tunnel.
- A normal HTTP status line, content type and body can be reconstructed from a
  bounded radio response.
- Three consecutive one-request browser runs completed inside the normal
  30-second receive window.

## What it does not prove

- It is three finite one-request samples on one near-bench link, not a latency
  or completion distribution.
- It is not HTTPS, `CONNECT`, a TUN interface or transparent routing.
- It does not measure RF airtime at the antenna, energy, EIRP or field range.
- The gateway is not part of the published alpha SDK API.
- The 3-second startup gap is coordinated cold-start behavior, not a persistent
  radio session.

## Why the pre-fix path took about 53 seconds

The receiver opens the serial device, discovers the MCU, loads the SDK, builds
the authority and arms before it can answer the sender's first flow grant. The
old probe started the sender after a fixed **0.8-second** gap. Under load, the
sender raced ahead and failed with
`reliable_flow_startup_grant_unanswered`; the browser retried and the run
stretched past 50 seconds.

The post-fix probe uses a bounded **3.0-second** startup gap. The radio
payloads, fresh signed grants, authentication, receiver verification and
endpoint receipts are unchanged. A warm or persistent radio session remains
useful for lower latency and repeated requests, but it is no longer required to
clear the 30-second browser target.

## Product direction

The intended product behavior is owner-visible and opt-in. When a Lantern unit
detects Wi-Fi and internet access, it can offer to help nearby nodes route
bounded requests. The default stays off. The tested limits should travel with
the feature: public HTTP targets only, no credentials or private addresses, a
bounded body and a bounded fetch timeout.

## Public record

The post-fix record is
[`benchmarks/2026-10-18-http-gateway-30s/results.json`](../benchmarks/2026-10-18-http-gateway-30s/results.json).
It retains the three browser timings, the one-shot control, the exact body and
wire hashes, the request/response attempts and the limitations. The earlier
cold-path record remains at
[`benchmarks/2026-10-18-http-gateway/result.json`](../benchmarks/2026-10-18-http-gateway/result.json)
as the pre-fix control. Raw browser transcripts and lab credentials remain
outside the public record.

The underlying radio measurements are in
[Public SDK radio measurements](RADIO_SDK_MEASUREMENTS.md). The HTTP probe does
not change those file-transfer results or the published alpha artifacts.
