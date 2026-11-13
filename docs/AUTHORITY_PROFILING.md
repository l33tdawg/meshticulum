# Optional authority profiling

The public file CLI accepts `--profile-authority`. It enables a separate fixed five-phase collector, `meshticulum.authority-phase-timing.v1`. Existing `--profile-host`, its32 phase labels and its v1 schema are unchanged. Neither option grants authority or changes a deadline. Default-off performs no diagnostic clock sampling; decorated methods still incur a Python wrapper/attribute lookup, so default-off is not a zero-overhead or byte-identical execution path.

| Phase | Included body |
| --- | --- |
| `hat_live` | HATWorker's original dispatch/ownership, transport-clock, authorization and lease checks |
| `application_live` | RunAuthority.live, owner probe and original deadline check |
| `authority_live` | Original clock/admission checks, fresh grant/active/secret reads and path checks |
| `owner_current` | HeldHardware's original process/boot/descriptor/path/collision-watch checks |
| `file_window_check` | Original window file and boot checks, followed by the original authority clock callback |

Coverage starts after worker setup and before session construction, continuing through cleanup. Initial hardware acquisition/borrow, RunAuthority construction and worker construction are excluded. The existing owner and file-window objects can be borrowed across runs: diagnostic attributes are saved and restored after cleanup, including failure paths. Restoration faults make diagnostics explicitly unavailable and increment bounded binding-error metadata without replacing the operation's result or original error. Cancellation is propagated after all possible restorations when no earlier operation failure exists.

Each phase has independent wall and thread-CPU aggregates: count, total, minimum, maximum, invalid samples, dropped intervals and saturation. Wall time uses the trusted `time.perf_counter_ns` reference; CPU time uses `time.thread_time_ns` when available. These are diagnostic clocks only. They never replace the captured authority/transport clock, authorize callback or deadline. Unsupported CPU timing leaves CPU rows null; ordinary CPU faults do not discard valid wall measurements, and vice versa. Same-phase reentrancy is dropped explicitly. Integers saturate at2^63−1. Storage is fixed to five rows per supported channel; the maximum saturated schema is tested below8192 bytes. No packet, path, grant identifier or per-call event list is added.

Intervals are inclusive and overlap. Do not add the five totals together or add them to the existing host phase totals. In particular, HAT live contains application live, which contains authority live and the owner/window callback chain. CPU and wall channels may have different valid intervals after faults; equal counts alone do not establish paired measurements. A wall/CPU difference can include blocking, preemption and instrumentation; it does not identify scheduler delay or prove the cause of a past packet loss. CPU time excludes work on other threads.

The result and failure exception expose `authority_phase_timings`; unavailable serialization/restoration is explicitly flagged. This is a bounded aggregate, not a replacement for the receive timeline. Existing host timing can bracket receive/poll/drain/read/rearm, but its global rearm phase also includes TX restoration, so naive subtraction from receive totals is invalid.

Tests cover default-off/no diagnostic samples, unchanged authority callback counts and FileWindow callback order, ordinary and cancellation failures, independent channel faults, reentrancy, saturation, maximum serialization size, actual run_object cleanup, borrowed owner/window restoration and original failure preservation. Modeled on/off cost comparisons do not establish target hardware overhead or RF performance. A fresh finite authorization is still required for any later device measurement; exhausted RF headroom must not be bypassed by reducing an allowance merely to fit another test.
