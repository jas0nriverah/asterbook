# 4. Admission and deadlines

**Context.** A client must not be able to create unlimited queued commands or occupy a connection indefinitely.

**Decision.** The gateway limits clients, frame size, per-client in-flight work, per-shard queue size and request rate. It uses a per-client token bucket, an idle timeout, an absolute frame assembly deadline, and a queue deadline. An expired queued command returns `DEADLINE_EXCEEDED` before execution and does not enter deduplication history. Once execution starts it is not canceled by the deadline. A disconnected client's completion is routed by connection generation rather than file descriptor. See [gateway](../../src/gateway.cpp), [queue](../../include/asterbook/queue.hpp), and [operations](../operations.md).

**Alternatives and consequences.** Waiting indefinitely or dropping old deduplication records would hide overload or weaken retry behavior. A full queue returns `OVERLOADED`; callers retry using the same request identity. A disconnect after admission remains ambiguous.
