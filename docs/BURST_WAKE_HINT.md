# Burst guard wake hint

`prepare_firmware.py prepare --burst-wake-hint` optionally creates one task-dispatched ESP timer for the lifetime of the existing firmware loop task. Its callback only notifies that task. It never reads a queue, changes a deadline, performs SPI, or authorizes a transmission.

Immediately before the existing one-tick notification wait, the loop selects the existing absolute `burstNextStartUs` as a hint only while serial service, radio readiness, arming and queued work permit it. A changed target stops the old timer and starts a one-shot for the remaining interval. Repeated loop visits do not restart the same target. Expired or cancelled callbacks may still wake the loop, which follows the unchanged receive-first service and burst checks. The handle is never deleted, so a delayed callback cannot dereference freed timer-owner state. Its sole argument is the lifetime-stable task handle.

The one-millisecond periodic fallback remains. Creation/start failures or unexpected stop failures disable the optimization for the boot. Stopping an already-expired timer is an accepted condition. No recovery retry loop is introduced. Timer API scheduling overhead may produce a late hint; it cannot move the guard earlier. The guard remains anchored after successful checked RX rearm, with the same5ms interframe and100ms reverse floors.

The recipe uses a strict boolean defaulting off, a generated forced-include setting, and the normal source/application identity checks. The new firmware source and helper are part of application identity even with runtime hints disabled. No compatibility with an earlier binary identity is claimed for these changed sources.

The deterministic compiled helper tests cover stale callbacks after cancellation, expired-stop races, unchanged targets, create/start/stop failures and null-task refusal. They demonstrate notification-only behavior, not physical timing improvements. Actual latency remains unmeasured; timer hints cannot remove RadioLib setup or checked RX rearm costs.
