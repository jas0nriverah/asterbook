# 3. Wire format

**Context.** Clients need framing that survives arbitrary TCP fragmentation and coalescing, and parsing must have a small input bound.

**Decision.** Each request has a four-byte unsigned big-endian length and an ASCII payload beginning `V1 `. Responses use the same frame with JSON payload. The gateway caps request payloads at 4,096 bytes and supports partial reads and writes; the parser validates token counts, identifiers and integer ranges. See [protocol specification](../protocol.md), [parser](../../src/protocol.cpp), and [gateway](../../src/gateway.cpp).

**Alternatives and consequences.** Text is easier to inspect than a binary schema but uses more bytes and parsing work. A version prefix allows an incompatible future grammar to be rejected explicitly. There is no protocol negotiation or streaming result pagination.
