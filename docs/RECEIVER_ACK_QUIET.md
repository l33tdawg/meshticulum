# Receiver ACK quiet experiment

`radio_file transfer --receiver-ack-quiet-us 150000` is a default-off SF5 train24 experiment. The endpoint must opt in before any activity. Other values, profiles, train lengths, repeated setup and late setup fail. The flag is included in retained FileHardware argument binding and the resulting value appears in the timing policy snapshot.

Only the receiver's partial-train silence deadline changes from 250ms to 150ms. Full-train and completed-object ACKs remain immediately eligible; original 2-second coalescing maximum, sender 1-second fallback, physical 100ms reverse interval, all authority/owner checks, native limits and frozen flow are unchanged. Rejected/replayed packets cannot refresh the silence deadline.

Evidence from the successful 16KiB campaign shows four approximately 330ms gaps, each waiting for a quiet ACK. The expected best-case saving is approximately 400ms, giving roughly 2.56kB/s from the 6.808-second baseline. This is not a 4kB/s claim. Silence is a heuristic, not proof of a completed train: several consecutive lost packets may cause earlier ACK collision. The next measured campaign must compare loss, retries, verified goodput and clean closure under fresh authority.
