# Blueprint scope review

Reviewed against `cpp_matching_engine_blueprint.html` and `orderbook_blueprint_body.html` on 2026-11-06. The blueprint distinguishes core deliverables from optional extensions, suggested implementation choices, and learning assignments. The matrix records implementation and evidence, not a proof of correctness.

| Blueprint scope | Implementation and evidence | Boundary or remaining work |
| --- | --- | --- |
| Price-time matching, limits, IOC, cancel, atomic replace, deterministic events and invalid commands | `src/book.cpp`, `src/engine.cpp`; `tests/core_test.cpp`, `reference/differential.py` | Simulator policy; no auctions, hidden orders, margin, settlement or self-trade prevention. |
| Stable order lookup and explicit ownership | Per-symbol ordered sets of price, priority and ID, with indexed live orders in `src/book.cpp`; `docs/architecture.md` and decisions 01–02 | Ordered-set operations are O(log N_symbol); matching also depends on fills. The blueprint's particular container choices are suggestions. Pool mode is optional. |
| One writer, bounded admission, framing and retry identity | `src/gateway.cpp`, `src/protocol.cpp`, `include/asterbook/queue.hpp`; network, queue and protocol tests | Per-shard ordering; no global transaction or atomic cross-shard snapshot. TCP disconnection leaves outcome ambiguous. |
| Durability, replay, snapshots, dedup and recovery | `src/journal.cpp`, `src/engine.cpp`; recovery and fault tests, decisions 05–06; README trace | Process-level injections do not reproduce power loss. Checksums detect accidental corruption, not malicious tampering. Snapshot validation does not prove each retained result semantically equals WAL replay. |
| Reference and parser testing | `reference/differential.py`, CTest, `fuzz/`; separate ASan/UBSan and TSan presets and CI jobs | Fuzzing is bounded, not exhaustive. CI configuration is not evidence of a completed remote run. |
| Benchmarks and workload families | `bench/core_bench.cpp`, `bench/durable_bench.cpp`, `bench/open_loop.py`, optional Google Benchmark; `docs/benchmarking.md`; raw local examples | Synthetic; the open-loop harness has no warmup or preconnect. Bursts and symbol skew are configurable, but no controlled balanced/skew comparison, profiling result, or repeated Linux study is included. Record host, compiler and flags externally. No throughput claim is made. |
| Optional ITCH reconstructor | `src/itch.cpp`, `tools/itch_main.cpp`, `reference/itch.py`; native/reference and sequencing tests | Offline A/F/E/C/X/D/U subset; unsupported messages fail, no live transport or gap recovery. Sequenced mode poisons on error. See `docs/itch.md`. |
| Optional sharding and pool experiments | Per-symbol shard routing and pooled allocator; `tests/network_shards_test.py`, baseline/oracle comparison | No cross-symbol atomicity; single local allocator samples are illustrative, not proof of speedup. |
| README, demo, design, decisions and operations | `README.md`, `tools/secure_demo.py`, `docs/architecture.md`, `docs/decisions/`, protocol and operations docs | README trace is a manual example; reproducible fault cases are in tests. |
| Release history and tag | Decision records and local release tag `v0.1.0` | No fabricated review or production usage. |

A separate `build/review-scope` release configuration on macOS arm64, Apple Clang 21 and OpenSSL 3.6.4 passed all 16 CTest cases after the benchmark changes. A pooled crossing benchmark smoke run and benchmark harness checks passed. This was not a Linux runtime test. The gateway review also built OpenSSL 3.6.4 and 4.0.3 and ran protocol, TLS and network tests against each; the final release, ASan/UBSan and TSan runs each passed all 16 tests. Coverage-guided fuzzing ran 100,000 inputs per target; see `verification.md`.

A four-shard local server smoke test exercised 40 arrivals in bursts of four at 50 arrivals/s for each of uniform eight-symbol selection and 100% hot-symbol selection. Both runs submitted and accepted all 40, with zero generator drops, rejections or transport errors; accounting identities were checked. This checks harness integration, not shard balance or performance.

Review found and fixed crossed-book checkpoint acceptance, a 50 ms delay between already-buffered pipelined frames, an OpenSSL 4 build failure, and inconsistent CMake minimum versions. The benchmark discarded transport rejection reasons, reused broken connections, and accepted non-finite rates; those defects were corrected.

The review also added burst and symbol-distribution controls, event-content oracle checks, and recovery tests that reopen after injected append and sync errors. The expanded independent-model run passed 100 fixed seeds of 1,000 commands each. These checks do not establish Linux runtime behavior or power-loss durability.

The setup script also completed with `PIP_NO_INDEX=1`, passing all 16 tests and the secure trade, checkpoint, restart and retry demonstration using dependencies already present locally.
