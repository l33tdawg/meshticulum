# HTTP over radio: one-request gateway

On 18 October 2026, the lab carried one bounded HTTP/1.x request from a
radio-only endpoint to a second endpoint with ordinary internet access,
fetched the page, and carried the response back over the same direct GFSK250
radio path. A `links` text browser rendered the result.

This is an application gateway experiment, not transparent IP routing. It uses
two fresh authenticated radio grants: one for the request and one for the
response. The response is bounded to 16 KiB, the internet fetch has a 20-second
timeout, and the gateway accepts only public `http://` targets. It refuses
credentials, private or loopback addresses, and HTTPS `CONNECT`.

## Measured result

| Item | Result |
| --- | --- |
| URL | `http://example.com/` |
| HTTP status | `200` |
| Body | `577` bytes, SHA-256 `25ddf2c883e0d1958ea971d279a7e4f0fd446724ee3db7db19dadabd4a62e484` |
| Request carried over radio | `83` bytes |
| Response carried over radio | `676` bytes |
| Request direction | Receiver verified; sender accepted the authenticated peer receipt |
| Response direction | Receiver verified; sender accepted the authenticated peer receipt |
| Radio attempts | `4` receiver + `1` sender for the request; `3` receiver + `4` sender for the response |
| Text browser, 30-second timeout | **Failed** after `52.834` s with `Error reading from socket` |
| Text browser, 90-second timeout | **Passed** after `53.296` s and rendered the example.com text |

The one-shot control used the same request and response path and completed with
HTTP `200`, the same 577-byte body hash and both endpoint receipts.

## What it proves

- A text browser can consume a public HTTP resource through a radio gateway.
- The request and response are separately authenticated and verified in both
  directions.
- Unit A can use its ordinary internet connection on behalf of unit C without
  exposing a general-purpose proxy or IP tunnel.
- A normal HTTP status line, content type and body can be reconstructed from a
  bounded radio response.

## What it does not prove

- It is one URL and one request, not a latency or completion distribution.
- It is not HTTPS, `CONNECT`, a TUN interface or transparent routing.
- It does not meet a 30-second browser timeout on the current cold path.
- It does not measure RF airtime, energy, EIRP or field range.
- The gateway is not part of the published alpha SDK API.

## Why the cold path takes about 53 seconds

The browser's receive window contains two endpoint starts, two fresh signed
radio grants and the internet fetch. The radio payloads are tiny; orchestration
dominates. The 30-second run gave up before the gateway finished, while the
90-second run completed in essentially the same window.

The next useful step is a warm or persistent radio session with pre-staged
grants. That is a better route to a real 30-second browser target than simply
raising every client's timeout. Until that is measured, 60 to 90 seconds is the
honest setting for this cold probe.

## Product direction

The intended product behavior is owner-visible and opt-in. When a Lantern unit
detects Wi-Fi and internet access, it can offer to help nearby nodes route
bounded requests. The default stays off. The tested limits should travel with
the feature: public HTTP targets only, no credentials or private addresses, a
bounded body and a bounded fetch timeout.

## Public record

The sanitized result is in
[`benchmarks/2026-10-18-http-gateway/result.json`](../benchmarks/2026-10-18-http-gateway/result.json).
It retains the body and wire-response hashes, radio receipts, browser timings and
the exact limitations. The operator captures and lab credentials remain outside
the public record.

The underlying radio measurements are in
[Public SDK radio measurements](RADIO_SDK_MEASUREMENTS.md). The HTTP probe does
not change those file-transfer results or the published alpha artifacts.
