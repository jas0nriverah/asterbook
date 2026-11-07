# Architecture and recovery

## Matching and ownership

Each shard has one worker, a bounded mutex-protected queue, an engine and a journal. Admission order determines the shard sequence. FNV-1a 64-bit hashing routes each symbol to a shard; a synced manifest records the routing version and shard count. Startup rejects a mismatch or missing journal. There is no in-place resharding, cross-shard order or atomic global snapshot.

The book uses an ordered ID map and bid/ask sets keyed by symbol, price, priority and ID. Matching walks the best opposing orders; partial fills retain priority. ID lookup is O(log N), and insertion or removal in a symbol's ordered set is O(log N_symbol). Matching K orders performs K ordered removals or lookups plus event construction.

Prices and quantities are positive signed 64-bit integers with no currency conversion. Trades execute at the resting price. GTC remainder rests; IOC remainder expires. Cancel requires the owner. Replace preserves side, validates before removing the old order, uses GTC and loses priority; an invalid replacement leaves the original live. IDs cannot be reused within a shard. At live-order capacity, GTC placement is rejected even if it might fully execute. Self-trades are allowed.

The baseline allocator is the default. `--allocator pooled` uses a single-writer pool for book order and index nodes, but not all strings or request history. `--batch-size` drains up to that many already queued commands without waiting for a batch to fill; state and checkpoint requests remain ordering barriers.

## Persistence and retry

New commands, including business rejects, are appended as framed, versioned, checksummed WAL records and synced before application and response. Malformed and pre-admission rejects are not journaled. Group commit appends a batch in order and syncs once before applying any command; retries, conflicts and capacity decisions account for earlier commands in that batch. Storage or apply failure poisons the writer and stops service.

A retained client/request pair with the same payload returns its original result; a different payload conflicts. Disconnects can leave outcomes ambiguous, so retry the identical identity and payload. Request and ID history are bounded; new identities are rejected when full rather than evicting history. These identities and sequence numbers are scoped to a shard.

A checkpoint stores book state, used IDs, sequence and deduplication results. It is written and synced to a temporary file, renamed, and followed by a directory sync. Recovery validates the snapshot and retained WAL prefix before replaying the suffix. The WAL is never trimmed. Recovery rejects empty existing, truncated or corrupt journals, including incomplete tails. Preserve damaged data and restore a known-good backup. Snapshot loading and prefix validation require work and storage proportional to retained data.

Checksums detect accidental corruption, not tampering. Sync uses the host `fsync`; macOS `F_FULLFSYNC` is not requested. Durability depends on the operating system, filesystem and storage hardware. Process-fault tests do not establish power-loss behavior.

## Other design boundaries

The versioned ASCII wire format favors inspection over a compact binary schema; framing handles TCP fragmentation and coalescing. There is no protocol negotiation or result pagination. Admission limits and deadlines bound resource use; a queued timeout occurs before execution, while an executing command cannot be canceled by a deadline. Connections are tracked by generation to avoid routing a completion to a reused descriptor. TLS handshakes use nonblocking I/O and an absolute deadline. See [protocol](protocol.md) and [operations](operations.md).

Core timing and scheduled-arrival service measurements are separate because they include different costs; see [benchmarking](benchmarking.md). The offline ITCH reconstructor applies reported feed updates independently rather than rematching executions; see [protocol](protocol.md#offline-itch-reconstruction).
