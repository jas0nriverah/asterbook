# AsterBook

AsterBook is a deterministic C++20 exchange simulator with price-time matching, a bounded TCP gateway, a synchronous journal, recovery, sharding, tests, and measurement tools. It processes synthetic orders; it does not connect to markets or execute real trades. Linux and macOS are supported development targets. This is an educational system, not an exchange-certified or production-trading service.

## Start

Install a C++20 compiler and Python 3. On macOS install Apple command line tools with `xcode-select --install`; on Linux install a C++20 compiler and Python including `venv`. Then run:

```sh
./scripts/setup.sh
```

The script builds the release preset, runs tests and a disposable mutual-TLS demonstration. If CMake or Ninja is unavailable it installs pinned versions in `.venv` using pip and network access; it never uses sudo. If OpenSSL cannot be discovered, setup builds pinned OpenSSL 3.5.8 in `.deps/` using curl, Perl and make (network required). On macOS, double-click `Install.command` to run the same setup in Terminal. Existing suitable CMake and Ninja installations are used. If a system CMake is too old, install 3.24 or later or add the local `.venv/bin` to `PATH`.

After setup, use `.venv/bin/cmake` and `.venv/bin/ctest` if CMake was installed locally, or your system `cmake` and `ctest`. Start a server in a separate terminal:

```sh
build/release/asterbook server --insecure-local --data-dir ./asterbook-data --port 9000
python3 tools/client.py --insecure-local --port 9000 'NEW alice r1 ABC a SELL GTC 100 5' 'NEW bob r2 ABC b BUY IOC 100 3' 'STATE'
```

A readable recovery trace (use a fresh data directory):

```sh
# Start the server above, then send these in order:
python3 tools/client.py --insecure-local --port 9000 'NEW alice r1 ABC a SELL GTC 100 5' 'NEW alice r2 ABC b SELL GTC 100 4' 'NEW bob r3 ABC c BUY IOC 100 7' 'STATE'
# Stop and restart the server with the same --data-dir, then:
python3 tools/client.py --insecure-local --port 9000 'STATE' 'NEW bob r3 ABC c BUY IOC 100 7'
```

The buy fills five from `a` and two from `b`; the remaining book has two units of `b`. After restart, state is the same and the identical retry returns the prior result marked duplicate. The separate [`recovery_fault_test`](tests/recovery_fault_test.cpp) exercises injected process failures; this manual trace does not simulate power loss.

Prices are integer ticks and quantities are integer units chosen by the simulation; no currency or tick conversion is implied. See [protocol](docs/protocol.md) for framing, commands, identity and errors, [architecture and recovery](docs/architecture.md) for ownership and durability, [operations](docs/operations.md) for limits and deployment boundaries, [benchmarking](docs/benchmarking.md) for measurement, [ITCH](docs/itch.md) for the independent offline reconstructor, and [architectural decisions](docs/decisions/README.md) for the design tradeoffs.

Run `./scripts/secure-demo.sh` to repeat the disposable certificate, mutual TLS, identity and administrator checks. Keys are created in a temporary directory. The command above uses explicit loopback-only plaintext for local experimentation; see [operations](docs/operations.md) for TLS configuration.

## Scope and evidence

| Blueprint item | Implementation or boundary |
| --- | --- |
| Price-time limit, IOC, cancel and atomic replace | Implemented; replacement loses priority and invalid replacements preserve the old order. |
| Multi-symbol and sharding | Stable symbol routing to independent single-writer shards; no cross-shard transactions or atomic global snapshot. |
| Durable recovery and retry | Synchronous WAL with optional group commit, snapshot and retained request history; fail closed on damaged tails. See recovery contract. |
| Bounded TCP | Framed, loopback by default, bounded clients, queue, request size and idle time; mutual TLS and certificate-bound identity; explicit loopback-only plaintext mode. |
| Verification | Deterministic tests, independent seeded scan-model differential tests, parser fuzz target, network and injected process-fault tests; sanitizers are separate builds. |
| ITCH | Offline C++ and Python displayed-book subset; optional C++ sequence validation, no live feed or recovery. |
| Benchmarks | Core and scheduled-arrival tools generate raw JSON. No fixed throughput claim or bundled production measurement. |
| Pool/layout optimization | Optional PMR pooled allocation; benchmark on the intended workload before choosing it. |

No auctions, hidden orders, margin, settlement, trading controls, or live market access are implemented. Self-trading is not prevented. [MIT license](LICENSE).
