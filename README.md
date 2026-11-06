# AsterBook

AsterBook is a deterministic C++20 exchange simulator with price-time matching, a bounded TCP gateway, a synchronous journal, recovery, sharding, tests, and measurement tools. It processes synthetic orders; it does not connect to markets or execute real trades. Linux and macOS are supported development targets. This is an educational system, not an exchange-certified or production-trading service.

## Start

Install a C++20 compiler and Python 3. On macOS install Apple command line tools with `xcode-select --install`; on Linux install a C++20 compiler and Python including `venv`. Then run:

```sh
./scripts/setup.sh
```

The script builds the release preset, runs tests and a temporary restart demonstration. If CMake or Ninja is unavailable it installs pinned versions in `.venv` using pip and network access; it never uses sudo. On macOS, double-click `Install.command` to run the same setup in Terminal. Existing suitable CMake and Ninja installations are used. If a system CMake is too old, install 3.20 or later or add the local `.venv/bin` to `PATH`.

After setup, use `.venv/bin/cmake` and `.venv/bin/ctest` if CMake was installed locally, or your system `cmake` and `ctest`. Start a server in a separate terminal:

```sh
build/release/asterbook server --data-dir ./asterbook-data --port 9000
python3 tools/client.py --port 9000 'NEW alice r1 ABC a SELL GTC 100 5' 'NEW bob r2 ABC b BUY IOC 100 3' 'STATE'
```

Prices are integer ticks and quantities are integer units chosen by the simulation; no currency or tick conversion is implied. See [protocol](docs/protocol.md) for framing, commands, identity and errors, [architecture and recovery](docs/architecture.md) for ownership and durability, [operations](docs/operations.md) for limits and deployment boundaries, [benchmarking](docs/benchmarking.md) for measurement, [ITCH](docs/itch.md) for the independent offline reconstructor, and [architectural decisions](docs/decisions/README.md) for the design tradeoffs.

The setup demonstration submits two sells at the same price, then buys seven units. The events trade five against the first sell and two against the second. The remaining two units survive a restart, and retrying the original request returns its original events with `duplicate: true`.

## Scope and evidence

| Blueprint item | Implementation or boundary |
| --- | --- |
| Price-time limit, IOC, cancel and atomic replace | Implemented; replacement loses priority and invalid replacements preserve the old order. |
| Multi-symbol and sharding | Stable symbol routing to independent single-writer shards; no cross-shard transactions or atomic global snapshot. |
| Durable recovery and retry | Synchronous per-command WAL, snapshot and retained request history; fail closed on damaged tails. See recovery contract. |
| Bounded TCP | Framed, loopback by default, bounded clients, queue, request size and idle time; no TLS or authentication. |
| Verification | Deterministic tests, independent seeded scan-model differential tests, parser fuzz target, network and injected process-fault tests; sanitizers are separate builds. |
| ITCH | Offline Python displayed-book subset only; no transport, gap recovery, or complete feed. |
| Benchmarks | Core and scheduled-arrival tools generate raw JSON. No fixed throughput claim or bundled production measurement. |
| Pool/layout optimization | Deferred pending profiling; the baseline avoids unverified pool reuse. |

No auctions, hidden orders, margin, settlement, trading controls, or live market access are implemented. Self-trading is not prevented. [MIT license](LICENSE).
