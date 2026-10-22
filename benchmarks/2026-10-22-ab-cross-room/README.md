# A/B cross-room signed-file result

This is one finite 22 October 2026 Level 4 result reported in
[issue #43](https://github.com/l33tdawg/meshticulum/issues/43#issuecomment-6333261599).

## Conditions

| Item | Recorded condition |
| --- | --- |
| Sender | Unit B, Heltec LoRa 32 V4 |
| Receiver | Unit A, Heltec LoRa 32 V4 |
| Arrangement | Approximately 20 m apart in separate rooms; distance is operator-reported, not independently measured |
| Firmware | `mtb1-020e287255` on both endpoints |
| SDK source | `673eb60d24dc60f67371e24bc5c9caeec3a68508` |
| Firmware source | `c8957e56b017ecd42486acc2c8a46c24cb238d2d` |
| SDK wheel | `dce5127fed0956e5b3d2cd9ce7dd859a2a4f6af8cdd491bfaf9ba5723e495c1c` |
| Modem | GFSK250 at 921.375 MHz and 0 dBm configured chip power |
| Window | One new 240-second original scope |

Unit B was first updated app-only from `mtb1-9816f92488` to the current
`mtb1-020e287255` Heltec application image
`eca7772095cc9c9e63c9f6e6d9fe48ba3c376cd6bfef557ffcc441621a846fa2`.
The firmware build and SDK wheel retain distinct source identities.
The exact prior 393,216-byte application range was retained with SHA-256
`b143884e63e7ad3694a72de8beff241c8b9918618bc91bfbab8a04caf2d71059`
and root flash receipt SHA-256
`89e8918fd722b0cff872773a362ef0f2204a865b3840a3b4ebd328701b50009d`.

## Result

- 100,000 useful decoded bytes in five signed parts
- Whole-file SHA-256:
  `65d19fca4bd31ce9db664e4d0457b1d677132d4c4463d5aeb5c40352170938f1`
- Five of five parts passed authenticated peer receipt and whole-file
  verification
- First sender startup through final authenticated receipt: 56.311923 seconds
- Useful whole-file goodput: 1,775.822857 B/s (1.776 kB/s; 14.207 kbit/s)
- First DATA worker call through final receipt: 55.554563 seconds
- DATA-boundary goodput: 1,800.032159 B/s
- Scope issue through both endpoints closed: 71.051173 seconds
- Receiver DATA RSSI: -57.5 to -56.5 dBm, median -57.0 dBm across 539 frames
- MCU receive-drop deltas: zero on both endpoints
- Attempt debt: A 162 attempts / 646,866 us; B 544 attempts / 5,659,198 us
- All ten endpoint processes exited zero and no forced cleanup occurred
- Tracked service lifetimes stayed unchanged

The earlier same-day A/B scope failed before any DATA transfer because
per-endpoint boot limits produced different authenticated wire profiles. That
failed scope remains separate and is not counted as a pass. The successful run
used one identical cryptographic wire profile while checking each endpoint's
actual boot baseline separately.

## Evidence

The sanitized result is [results.json](results.json). Its verified hashes are:

- `prepared_sha256`: `b289bcd4864e20dc8ac8e6a34caf4b36a8bedc0bac3c6f5f1feefd2c01523ba8`
- `scope_sha256`: `9a0c7972a370a7b7ee8175e016141c1b76f1f4512b1999f8a2ac9fc56e092d93`
- `staging_sha256`: `d97ec99cc0cabf8d5377763a4ad108f45f53bfc4f5ddc4173aabba80cc5f5f1d`
- `result_sha256`: `7be5180ed0eb1d53de40d7a7b115e5e8506b96ae3a7f58b830ad68c57254727a`

The exact SDK wheel remained unchanged. The test composed the public signed
bundle with a narrowly scoped harness adaptation that permits the exact GFSK250
USB serial sender as well as the existing Linux HAT sender. That adaptation is
recorded as source evidence in the private operator capture; it is not part of
the published wheel.

## Claim boundary

This is one cross-room GFSK250 sample. It does not establish field range,
sustained or distribution-wide capacity, LoRa-profile performance, mixed-path
recovery, private production readiness, wire compatibility, another hardware
installation or overall release qualification. Distance and antenna conditions
were operator-supplied rather than independently measured.
