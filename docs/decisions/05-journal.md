# 5. Synchronous journal and recovery

**Context.** An acknowledgment must have a defined relationship to persistence and retry.

**Decision.** New commands are framed with versioned headers, sequence ordinals and checksums, appended to the shard WAL and `fsync`ed before execution and response. Business rejects are journaled; malformed and pre-admission rejects are not. Recovery validates configuration, framing, checksums and ordinals before replay. Existing empty, truncated or corrupt WALs fail closed; an incomplete tail is not automatically discarded. See [journal](../../src/journal.cpp).

**Alternatives and consequences.** Synchronous per-command persistence costs throughput and does not promise power-loss behavior beyond the filesystem and hardware's guarantees. If a process stops after append or sync but before its response, the caller cannot know whether the command was applied; retry the same identity after recovery. I/O or apply errors poison the writer and stop the service. Checksums detect accidental damage, not adversarial modification.
