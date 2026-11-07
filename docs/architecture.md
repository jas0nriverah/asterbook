# Architecture

Each symbol hashes to one single-writer shard. A shard owns its book, bounded queue, journal, and request history. Orders match by price, then arrival order. Cancel checks ownership; replace validates before removal and loses priority. Shard count and capacity must match on restart.

## Durability

The journal stores framed, checksummed commands. A batch is synced before application and response. An I/O or apply failure stops the service. Retries with the same client/request and payload return the saved result; conflicting payloads fail. History and used IDs are retained up to capacity, then new identities are rejected.

`COMPACT` replaces a shard's journal with a capacity header, checksummed engine checkpoint, and subsequent commands. Publication uses a synced temporary file, atomic rename, and directory sync. A separate lock file protects ownership across inode replacement. Recovery sees the old or new journal; corrupt or incomplete data fails closed. Old external snapshots are superseded by the embedded checkpoint. Legacy journals remain readable; older binaries cannot read compacted journals.

Compaction removes command replay, not retained orders, IDs, or deduplication results. It may not reduce bytes when the retained checkpoint is larger than the old log. It does not reset history capacity. `CHECKPOINT` remains available for legacy journals.

## Replication

A primary with `replica-host` sends commands over authenticated TLS to a standby before committing locally. It acknowledges only after both nodes sync and produce identical results. Each shard preserves order; there is no cross-shard transaction. Replication uses a separate TLS connection per command with a five-second deadline and adds network/handshake overhead.

Startup compares SHA-256 digests of complete shard checkpoints, including capacity and deduplication state. Missing or divergent replicas block startup. A replication failure stops the primary; it never silently switches to single-node writes. A lost response can leave the standby ahead for an unacknowledged command. Re-seed the stale node from the authoritative copy before restarting.

A standby accepts writes only from its configured replication certificate identity. Promotion is manual and requires fencing the old primary. There is no election, quorum, automatic failover, or online catch-up. See [operations](operations.md#replication-and-failover).

## Boundaries

State and maintenance requests are shard barriers; multi-shard responses are not atomic snapshots. Response size, connection count, queue depth, deadlines, and request rates are bounded. WAL/checkpoint checksums detect corruption, not malicious edits. Durability relies on host `fsync` and storage behavior; process-crash tests do not establish physical power-loss durability.
