# 2. Order and replacement rules

**Context.** A reproducible simulator needs exact arithmetic and defined behavior at capacity and on invalid input.

**Decision.** Price ticks and quantity units are positive signed 64-bit integers; the simulator defines no currency conversion. A limit executes at the resting price, in price-time order. GTC remainder rests and IOC remainder expires. Cancel requires the original client identity. Replace validates first, preserves side, uses GTC, removes the old order and loses priority; an invalid replacement leaves the old order live. IDs are not reused within a shard. See [engine](../../src/engine.cpp) and [protocol](../protocol.md).

**Alternatives and consequences.** Floating-point prices and implicit market orders are excluded. At live-order capacity, GTC placement is rejected conservatively even if it might fully execute; this keeps the pre-mutation capacity check simple. Self-trades are allowed. Client identity is an ownership field, not authentication.
