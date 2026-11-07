# AsterBook

AsterBook is a C++20 matching engine for simulated exchanges. It matches orders by price and arrival time, serves requests over TCP, and recovers state from a journal.

## Features

- Limit, immediate-or-cancel, cancellation and replacement orders
- Single-writer symbol shards with deterministic replay
- Synchronous journal, atomic WAL compaction and bounded request deduplication
- Synchronous mTLS primary/standby replication with fenced manual failover
- Bounded TCP gateway with mutual TLS and certificate-based identities
- Offline ITCH displayed-order reconstruction
- Reference-model tests, fault injection, fuzzing and benchmark tools

## Quick start

Clone the repository and run:

```sh
git clone https://github.com/jas0nriverah/asterbook.git
cd asterbook
./start.sh
```

The script checks dependencies and prints installation commands if anything is missing. It then builds, runs tests and demonstrates mutual TLS. Use `./start.sh --check` to check dependencies without building. For Linux deployment, release packages, backups and monitoring, see [operations](docs/operations.md).

## Documentation

- [Architecture and recovery](docs/architecture.md)
- [Protocol and offline ITCH](docs/protocol.md)
- [Operations and security](docs/operations.md)
- [Benchmarking](docs/benchmarking.md)
- [Development and testing](docs/development.md)

[MIT license](LICENSE)
