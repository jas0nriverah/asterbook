# Implemented scope and evidence

| Capability | Implementation | Evidence |
| --- | --- | --- |
| Matching, price-time priority, IOC, cancel, replace | `src/book.cpp`, `src/engine.cpp` | `tests/core_test.cpp`, `reference/differential.py` |
| Journal, restart, deduplication, fail-closed recovery | `src/journal.cpp` | `tests/recovery_test.cpp`, `tests/recovery_fault_test.cpp` |
| Framed bounded network service and symbol shards | `src/gateway.cpp`, `src/protocol.cpp` | `tests/network*_test.py`, `tests/protocol_test.cpp` |
| TLS, identities, admin policy | `src/tls.cpp`, `src/gateway.cpp` | `tests/tls_test.py`, `scripts/secure-demo.sh` |
| Optional pool and batched journal commits | `include/asterbook/book.hpp`, `src/journal.cpp` | core and recovery tests |
| Offline ITCH A/F/E/C/X/D/U | `src/itch.cpp`, `reference/itch.py` | `tests/itch_native_test.py`, `tests/itch_sequence_test.cpp`, `reference/test_itch.py` |
| Measurement and fuzzing | `bench/`, `fuzz/` | `docs/verification.md`, `docs/benchmarking.md` |

The offline feed is a subset with custom file framing; it is not an exchange connection. Tests and local measurements are evidence for exercised cases, not proof of production safety or performance.
