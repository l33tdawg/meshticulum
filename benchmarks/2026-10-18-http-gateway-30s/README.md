# HTTP over radio: 30-second browser path

On 18 October 2026, the lab reran the bounded HTTP-over-radio gateway after
fixing the receiver/sender startup race. Unit C used its SX1262 HAT to send a
plain HTTP request to unit A's Heltec V4 MCU; unit A fetched the public page
through its ordinary Internet connection and returned a bounded response over
the radio. Unit C's `links` text browser rendered the page with its normal
**30-second** receive timeout.

| Run | URL | Browser wall time | Gateway complete | Result |
| --- | --- | ---: | ---: | --- |
| 1 | `http://example.com/` | 17.99 s | 17.08 s | HTTP 200, rendered |
| 2 | `http://example.com/` | 17.83 s | 16.90 s | HTTP 200, rendered |
| 3 | `http://example.org/` | 17.77 s | 16.81 s | HTTP 200, rendered |

The pre-fix control failed the 30-second timeout after 52.834 s and only passed
with a 90-second timeout at 53.296 s. The post-fix one-shot control completed
in **17.559 s**.

## What changed

The receiver has to open the serial device, discover the MCU, load the SDK,
construct the authority and arm before it can answer the sender's first flow
grant. The old probe started the sender after a fixed 0.8-second gap; under
load, the sender raced ahead and failed with
`reliable_flow_startup_grant_unanswered`. The browser retried and the run
stretched past 50 seconds.

The post-fix probe uses a bounded **3.0-second** startup gap. The radio
payloads, fresh signed grants, authentication, receiver verification and
endpoint receipts are unchanged. The gateway is still a cold, one-request
application path; the gap is coordination, not a persistent session.

## Fixed conditions

- Source SDK: `1f85608e2c5e543130673529e21d838917094c11`
- SDK archive SHA-256: `84c5eaa59d34b619a53d30d9a296524b5333e6f943eb7e066d7f782512e435ee`
- Probe script SHA-256: `3585f0901e1d6b99ea4625e6036fc52344d72a95cb9234d9ad533580a847c9e8`
- Transport: GFSK250 direct HAT-to-MCU, approximately one metre, 0 dBm chip setting
- Request carried over radio: 83 bytes
- Response carried over radio: 676 bytes
- Body: 577 bytes, SHA-256 `25ddf2c883e0d1958ea971d279a7e4f0fd446724ee3db7db19dadabd4a62e484`
- Browser: `links -dump -no-g -no-connect`, 30-second receive timeout
- Every run used fresh signed request and response phases and fresh grants

All three runs returned HTTP 200, the same exact body hash, and receiver
verification plus authenticated sender peer receipt in both radio directions.

## Scope

This is three finite one-request browser samples on one near-bench link. It is
not a latency distribution, sustained throughput measurement, field-range
result, transparent IP router, TUN interface or HTTPS `CONNECT` proxy. It does
not measure RF airtime at the antenna, energy or EIRP. The gateway is an
application experiment, not a published alpha SDK API.

The sanitized record is [`results.json`](results.json). The raw browser
transcript and operator captures remain in the private lab workspace.
