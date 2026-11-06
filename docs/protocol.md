# Protocol

TCP messages are a 4-byte unsigned big-endian length followed by ASCII payload; replies use the same framing with JSON payload. Request bodies are limited to 4096 bytes. Multiple frames may share a read and frames may be split across reads. Every request begins `V1 `; tokens are separated by single spaces. Identifiers are 1–64 ASCII letters, digits, `_`, `-`, or `.`, and numbers are positive decimal signed-64-bit values without leading zeroes.

```
V1 NEW client request symbol order_id BUY|SELL GTC|IOC price quantity
V1 CANCEL client request symbol order_id
V1 REPLACE client request symbol order_id new_id price quantity
V1 STATE
V1 METRICS
V1 CHECKPOINT
```

NEW matches better prices first and FIFO at each price, at the resting order's price. Unfilled GTC quantity rests; IOC remainder emits EXPIRE. CANCEL requires the owner and a live order. REPLACE validates before removing the old order, always loses priority, and behaves as GTC. IDs are never reused within a shard, even after completion. Same client/request and identical command returns the original response with `duplicate:true`; a conflicting command returns `REQUEST_CONFLICT`. Retention is bounded and when full new requests fail closed rather than evicting deduplication history. Under sharding the ID and client/request scopes are per shard, so use globally unique IDs to avoid ambiguity.

Successful or business-rejected sequenced commands return `sequence`, `accepted`, `code`, `duplicate` and ordered `events` (CANCEL, TRADE, REST, EXPIRE). A malformed frame or command has a transport error object such as `BAD_REQUEST` and does not consume a sequence. Overload may reject or close a connection; a disconnect leaves the result ambiguous, so retry the exact request identity and payload. For multiple shards, command responses wrap `result` with `shard`; sequence is per shard. STATE and CHECKPOINT aggregate per-shard responses with `atomic:false`, not a single cross-shard point in time. METRICS describes gateway counters. CHECKPOINT and STATE are administrative operations and should only be exposed to trusted local clients.
