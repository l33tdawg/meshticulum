# Fresh owner inventory: conditional event equivalence

Opt-in source candidate; default behavior remains full fresh enumeration. Hardware activation still requires independent review and the bounded Linux lifecycle check.

The measured alternatives do not justify a representation patch: existing inventory ~932us, frozenset ~1045us, list ~1062us, Counter ~1390us across forty directories. Continue using the exact existing representation unless a different mechanism is proved.

A narrowly classified tmpfs/devtmpfs directory could avoid repeated enumeration while retaining a fresh kernel event proof on every check. Preserve initial complete inventory, all watch registration, exact fdinfo watch-set/mask binding, fresh mount-table equality, fresh path identity, both nonblocking event drains, and failure on every event, malformed record or overflow. Keep fresh enumeration for devpts, unknown filesystems and any classification ambiguity. This is not an authorization cache: each current() still obtains fresh mount, descriptor, identity and event evidence.

Reference proof: upstream Linux v6.1 VFS successful create/mknod/symlink/mkdir/unlink/rename routes emit fsnotify namespace events. Its devtmpfs kernel node creation/deletion uses the same VFS mknod/mkdir/unlink/rmdir entry points. An inventory records names, inodes and file kinds, not file contents; mmap content writes do not change those fields. C reports Linux6.1.115-vendor-rk35xx. The bounded lifecycle helper tests its actual namespace-event behavior; upstream references do not independently prove all vendor modifications.

Primary sources:

- [VFS namespace operations](https://raw.githubusercontent.com/torvalds/linux/v6.1/fs/namei.c)
- [Kernel devtmpfs operations](https://raw.githubusercontent.com/torvalds/linux/v6.1/drivers/base/devtmpfs.c)
- [fsnotify directory event routing](https://raw.githubusercontent.com/torvalds/linux/v6.1/include/linux/fsnotify.h)
- [inotify API and limitations](https://man7.org/linux/man-pages/man7/inotify.7.html)

Classification must bind the actual covering mount, filesystem type and device identity for each watched directory, including escaped mount paths and nested mounts. Do not infer support from a path prefix or TMPFS_MAGIC alone (devtmpfs needs explicit treatment). Record the complete original mount table even if no nodev namespace happens to exist. Any fresh mount change refuses. Do not resolve ambiguous mount stacks optimistically.

Tests required before activation: real Linux tmpfs create/delete between checks; rename in/out/exchange and inode replacement; hardlink/symlink alias creation; chmod; watched directory removal/move; unknown watch/event, overflow and malformed bytes; recycled fd or reduced mask; mount over and bind mount changes; startup mutations during inventory. Assert unknown/devpts still enumerate every check. Existing ownership, fork, descriptor, native lease and hardware guard suites must remain unchanged and pass.

C reports Linux6.1.115-vendor-rk35xx, with38devtmpfs,1hugetlbfs and1devpts watched directories. Actual lifecycle validation and vendor-source provenance remain distinct from upstream reference evidence. Potential savings are bounded by the measured inventory component, approximately0.9ms/check; no throughput improvement has been measured.

The CLI opt-in is `radio_file transfer --event-complete-inventory`. It is part of the retained FileHardware argument binding. Unknown, malformed or ambiguous mount classification never qualifies for the fast path. Directory watches still retain their exact original fdinfo lines on every call; partial event masks cannot qualify.

Run the standalone `tools/check_event_inventory_linux.py --expires-epoch-us ORIGINAL_EXPIRY` only under the original management window after all RF children close, using the reviewed candidate package on PYTHONPATH. It imports no radio worker and issues no radio commands. It creates a root-owned0700 random `/dev/meshticulum-bench-selftest-*` directory, runs twelve namespace mutations against watches within that private tree, and removes that tree. The hard10second cooperative budget is checked around every case; system-call blocking is not preempted, so the controller must retain its outer execution timeout. It requires at least15seconds remaining at entry, reserving time for cleanup, and does not alter any existing device node.

The helper flushes an initial JSON line with its newly created path, device, inode, UID, mode, PID and original expiry before running cases. If the controller must terminate it, this receipt permits identity-checked cleanup of only that created tree; termination must never authorize deleting a replacement path. A final success JSON line confirms cleanup.
