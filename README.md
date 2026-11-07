# AsterBook

AsterBook is a C++20 matching engine for simulated exchanges. It matches orders by price and arrival time, serves requests over TCP, and recovers state from a journal.

## Features

- Limit, immediate-or-cancel, cancellation and replacement orders
- Single-writer symbol shards with deterministic replay
- Synchronous journal, checkpoints and bounded request deduplication
- Bounded TCP gateway with mutual TLS and certificate-based identities
- Offline ITCH displayed-order reconstruction
- Reference-model tests, fault injection, fuzzing and benchmark tools

## Quick start

Install a C++20 compiler and Python 3, then run:

```sh
./scripts/setup.sh
```

The script builds the release preset, runs tests and demonstrates mutual TLS. For Linux deployment, release packages, backups and monitoring, see [operations](docs/operations.md).

## Documentation

- [Architecture and recovery](docs/architecture.md)
- [Protocol and offline ITCH](docs/protocol.md)
- [Operations and security](docs/operations.md)
- [Benchmarking](docs/benchmarking.md)
- [Development and testing](docs/development.md)

[MIT license](LICENSE)
