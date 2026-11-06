# 1. Single writer and ordered indexes

**Context.** Matching must preserve price-time priority and produce repeatable results under concurrent clients.

**Decision.** One worker owns each shard's engine and consumes a mutex-protected bounded queue. The book stores orders in an ordered map by ID and bid/ask sets keyed by symbol, price, priority and ID. A match walks the best opposing keys; partial fills preserve the resting priority. Replacement receives a new priority. See [book](../../include/asterbook/book.hpp), [engine](../../src/engine.cpp), and [gateway](../../src/gateway.cpp).

**Alternatives and consequences.** A lock-free queue would add concurrency obligations and is not used. The optional per-book memory pool adds lifetime constraints and is selected explicitly; baseline allocation remains the default. ID lookup is O(log N), and each per-symbol ordered-set insertion or removal is O(log N_symbol); matching K orders performs K ordered removals/lookups plus event construction. This is not a price-level tree with FIFO lists, and O(log L) in the number of price levels is not its bound. One shard has one sequencing point; different shards have no global order.
