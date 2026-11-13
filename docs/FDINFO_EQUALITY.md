# Fresh fdinfo equality shortcut

Collision-watch probes still perform the original descriptor identity/flags check,
bounded fresh procfs read, mount-table comparison, pinned path checks, required
directory inventories and two event drains. The optimization skips only parsing
when fresh fdinfo bytes exactly equal the initially validated bytes and the current
watch lines, watch set, inode mapping and anchor equal immutable initial copies.
Changed bytes or state use the original parser. Successful fallback parsing never
refreshes the baseline. Teardown always parses and applies the original surviving
anchor/subset rule. Initialization failure cannot seed a binding.

The retained raw string is bounded by the existing 2 MiB reader limit. Immutable
watch-state copies add memory proportional to the already bounded parsed input.
No authorization result, procfs read, event queue or descriptor identity is cached.
Private-object replacement by arbitrary trusted-process code is outside the existing
object-integrity model; changes to the ordinary parser inputs are explicitly tested.

A local synthetic 42-watch / 5,027-byte parser model measured approximately 19 us
for parsing and 1.2 us for a distinct-but-equal fresh byte buffer plus state checks.
Both arms exclude the fresh read and all other ownership work. These results do
not establish target-device savings, historical loss causation or throughput.
Actual Linux procfs headers or watch ordering can change without changing watch
semantics; those cases safely retain parsing cost. Tests intentionally cover them.
