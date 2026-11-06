# 5. Synchronous journal and recovery

**Context.** An acknowledgment must have a defined relationship to persistence and retry.

**Decision.** New commands are framed with versioned headers, sequence ordinals and checksums, appended to the shard WAL and `fsync`ed before execution and response. Business rejects are journaled; malformed and pre-admission rejects are not. Recovery validates configuration, framing, checksums and ordinals before replay. Existing empty, truncated or corrupt WALs fail closed; an incomplete tail is not automatically discarded. See [journal](../../src/journal.cpp).

**Alternatives and consequences.** Synchronous persistence costs throughput; optional group commit amortizes sync across queued commands and does not promise power-loss behavior beyond the filesystem and hardware's guarantees. If a process stops after append or sync but before its response, the caller cannot know whether the command was applied; retry the same identity after recovery. I/O or apply errors poison the writer and stop the service. Checksums detect accidental damage, not adversarial modification.

`execute_batch` appends all newly admitted commands in order and issues one sync before applying any of them or returning results. Retries, conflicts, and capacity decisions include earlier commands in the same batch. An I/O or post-sync application failure poisons the instance; reopening replays the durable log. An interrupted write may leave an incomplete tail, which recovery rejects rather than truncating automatically. WAL and snapshot-prefix validation scan bounded record buffers; checkpoints themselves are still loaded as a complete state image.

The sync primitive is the host OS `fsync`; on macOS this implementation does not request `F_FULLFSYNC`. Power-loss guarantees therefore depend on the operating system, filesystem, and storage device; the crash tests simulate process failure and do not establish power-loss durability.
