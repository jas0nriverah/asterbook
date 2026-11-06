# 6. Snapshots and retained history

**Context.** Recovery needs a consistent book and the original responses for retained request identities.

**Decision.** Each shard serializes book state, used IDs, sequence and deduplication results into a checkpoint. It writes and syncs a temporary file, atomically renames it, and syncs the directory. Recovery validates the snapshot and the retained WAL prefix, then replays the suffix. The WAL is not trimmed. See [snapshot code](../../src/journal.cpp) and [checkpoint serialization](../../src/engine.cpp).

**Alternatives and consequences.** Evicting deduplication history or trimming a log after merely writing a checkpoint would weaken recovery and retry guarantees. History and ID capacity are bounded; when full, new identities are rejected rather than evicted. Snapshot and full-prefix validation require disk space and work proportional to retained data. A snapshot is not an atomic cross-shard checkpoint.
