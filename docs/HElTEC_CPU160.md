# Explicit Heltec CPU160 diagnostic mode

`prepare_firmware.py prepare --native-profile --heltec-cpu-mhz 160` selects a
source-bound startup CPU experiment. Omission or80 keeps the original application
bytes and behavior. Only generated main.cpp and native_profile_config.hpp change;
all original source inputs remain intact. The exact single-anchor transformation
refuses missing/duplicate anchors. The shared XIAO build keeps its existing CPU
behavior and refuses the new diagnostic selector.

On Heltec, after board.begin and before NVS boot processing/radio initialization,
the generated application requires a successful160MHz setter, CPU readback160MHz
and APB readback80,000,000Hz. Failure fences and disables serial decoding, leaving
radio initialization and subsequent radio commands unreachable. Board.begin itself
has ordinary GPIO/FEM/Wire effects; this is not a claim of zero hardware effects.
No frequency changes occur after startup. All receive floors, authority checks,
original deadlines, counters, unknown outcomes and frozen flow remain unchanged.

A same-boot NATIVE_PROFILE(19) request with exactly one byte0xfc returns a new
24-byte payload only in the enabled Heltec build and only while RF work is closed:

| Offset | Field |
| --- | --- |
|0..3|ASCII MNC1|
|4|schema1|
|5..7|zero reserved|
|8..11|requested CPU MHz160, big-endian uint32|
|12..15|live CPU MHz readback|
|16..19|live APB Hz readback|
|20..23|startup verification flag0or1, big-endian uint32|

Old firmware refuses0xfc; existing selectors/replies are unchanged. This query
performs no setter, ARM, reset, SPI or RF operation. Python `clock_query` and
`NativeClockSnapshot.from_frame` bind message type, request and boot, then reject
wrong length/schema/reserved bytes/requested mode/unknown flags. Live readback
mismatch is retained honestly; `.matches` then returns false. A successful setup
flag is historical, not permission or proof the current readbacks still match.

The pinned Arduino ESP32-S3 framework computes APB as constant80MHz, validates160
and uses esp_timer for micros/millis; native guards already use esp_timer_get_time.
SPI remains separately pinned8MHz in the proposed combined configuration. USB is
initialized before board.begin, so actual USB stability, clock readbacks, original
timebase continuity, power and thermal behavior still require finite target
qualification. No throughput, power efficiency or physical clock measurement is
claimed by source tests. See the retained native-critical-path analysis for the
non-predictive CPU-scaling sensitivity model.
