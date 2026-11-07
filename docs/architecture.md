# Architecture

## Request path

```mermaid
flowchart LR
    C[Client] --> T[mTLS gateway]
    T --> V[Validate identity and request]
    V --> Q[Bounded queue by symbol hash]
    Q --> S[Single-writer shard]
    S --> W[Journal and fsync]
    W --> B[Apply to order book]
    B --> R[JSON response]
```

Each shard owns its book, journal, queue and retry history.

| Rule | Behavior |
| --- | --- |
| Matching | Best price, then FIFO; trade at resting price |
| Replacement | Validate first; new order loses priority |
| Retry | Same client/request + payload returns saved result |
| History full | Reject new identities; never evict retry history |
| Multiple shards | Independent ordering; no cross-shard transaction or atomic snapshot |

## Replicated writes

```mermaid
sequenceDiagram
    participant C as Client
    participant P as Primary
    participant S as Standby
    C->>P: Order
    P->>S: Forward over mTLS
    S->>S: Journal, fsync, apply
    S-->>P: Result
    P->>P: Journal, fsync, apply
    P->>P: Compare results
    P-->>C: Acknowledge
```

| Condition | Action |
| --- | --- |
| Startup | Compare full checkpoint hashes, capacities and shard count |
| Missing/divergent standby | Refuse startup |
| Replication error or result mismatch | Stop primary; no silent standalone writes |
| Reply lost after standby commit | Retry exact request after fenced promotion; re-seed stale node |
| Transport | One TLS connection per command; five-second deadline |

Only the replication identity can write to the standby. [Failover is manual](operations.md#failover): no elections or online catch-up.

## WAL compaction

```mermaid
flowchart LR
    A[Pause shard at queue barrier] --> B[Write checkpoint to temporary WAL]
    B --> C[fsync temporary WAL]
    C --> D[Atomic rename]
    D --> E[fsync directory]
    E --> F[Resume appending commands]
```

| Preserved | Changed |
| --- | --- |
| Orders, used IDs, retry results, capacities | Old command replay replaced by checkpoint |
| Exclusive ownership via separate lock file | Embedded checkpoint supersedes external snapshots |

Compaction may increase file size; it does not free history capacity. Recovery reads the old or new WAL and rejects corruption/incomplete data. Legacy WALs remain readable; **older binaries cannot read compacted WALs**.

Durability depends on storage honoring `fsync`. Checksums detect corruption, not tampering; process-crash tests do not prove power-loss durability.
