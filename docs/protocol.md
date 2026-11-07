# Protocol

TCP messages are a 4-byte unsigned big-endian length followed by ASCII payload; replies use the same framing with JSON payload. Request bodies are limited to 4096 bytes. Multiple frames may share a read and frames may be split across reads. Every request begins `V1 `; tokens are separated by single spaces. Identifiers are 1–64 ASCII letters, digits, `_`, `-`, or `.`, and numbers are positive decimal signed-64-bit values without leading zeroes.

```
V1 NEW client request symbol order_id BUY|SELL GTC|IOC price quantity
V1 CANCEL client request symbol order_id
V1 REPLACE client request symbol order_id new_id price quantity
V1 STATE
V1 METRICS
V1 CHECKPOINT
V1 COMPACT
V1 DIGEST
V1 READY
```

NEW matches better prices first and FIFO at each price, at the resting order's price. Unfilled GTC quantity rests; IOC remainder emits EXPIRE. CANCEL requires the owner and a live order. REPLACE validates before removing the old order, always loses priority, and behaves as GTC. IDs are never reused within a shard, even after completion. Same client/request and identical command returns the original response with `duplicate:true`; a conflicting command returns `REQUEST_CONFLICT`. Retention is bounded and when full new requests fail closed rather than evicting deduplication history. Under sharding the ID and client/request scopes are per shard, so use globally unique IDs to avoid ambiguity.

Successful or business-rejected sequenced commands return `sequence`, `accepted`, `code`, `duplicate` and ordered `events` (CANCEL, TRADE, REST, EXPIRE). A malformed frame or command has a transport error object such as `BAD_REQUEST` and does not consume a sequence. Overload may reject or close a connection; a disconnect leaves the result ambiguous, so retry the exact request identity and payload. For multiple shards, command responses wrap `result` with `shard`; sequence is per shard. STATE, CHECKPOINT, COMPACT, DIGEST, and READY aggregate per-shard responses with `atomic:false`, not a single cross-shard point in time. METRICS describes gateway counters. All non-order commands are administrative operations: mutual TLS requires an authorized administrator identity; insecure-local mode permits local clients.

## Offline ITCH reconstruction

`build/release/asterbook_itch FILE` and `python3 reference/itch.py FILE` read a sequence of two-byte big-endian payload lengths followed by Nasdaq TotalView-ITCH 5.0 messages. The supported displayed-order subset is A/F (add), E/C (execution), X (cancel), D (delete) and U (replace). Prices use units of 1/10,000 dollars. Executions reduce the referenced order and are not sent to the matching engine. See the [ITCH specification](https://nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHSpecification.pdf), sections 1.3–1.4.

The custom framing is not SoupBinTCP or MoldUDP64. Input must be a complete, ordered stream of supported updates; unsupported messages and invalid lengths, references, reductions or stock locates stop processing. Real feeds require an upstream filter that understands other message types. Output is the final displayed orders, not a complete market picture. There is no feed transport, gap recovery, symbol directory, trading actions, trade statistics or broken-trade handling.

`asterbook_itch --sequenced FILE` reads an eight-byte big-endian sequence before each length and payload. The native `asterbook::itch::Reconstructor::apply_sequenced` likewise checks caller-supplied monotonic sequences. A gap, duplicate, overflow or invalid message poisons the session until `reset()` clears state; the envelope does not recover missing messages. Defaults allow one million live and two million seen references, configurable in the constructor. Validation errors preserve state; discard the instance after allocation failure. Stock and attribution fields must be printable ASCII.

COMPACT atomically replaces each shard WAL with an embedded checkpoint. DIGEST returns the complete checkpoint hash and standby role. READY checks local journal sync; monitor replica health separately.
