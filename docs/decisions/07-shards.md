# 7. Symbol sharding

**Context.** Independent symbols can be processed concurrently, but their orders still need stable routing after restart.

**Decision.** FNV-1a 64-bit hash of the symbol modulo the configured shard count selects one independent worker, queue, book and WAL. A synced manifest records the routing version and shard count; startup rejects a mismatch or missing existing journal. Sequences, order IDs and `(client, request)` deduplication identities are scoped to a shard. See [gateway](../../src/gateway.cpp) and [protocol](../protocol.md).

**Alternatives and consequences.** Routing by client would split a symbol's book. Changing the shard count in place would move symbols and invalidate retry routing; no resharding migration is implemented. Aggregate STATE and CHECKPOINT responses are explicitly non-atomic across shards. A failed initial creation can leave a manifest without all journals and requires operator recovery, rather than silently creating missing data.
