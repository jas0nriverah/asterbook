# Benchmarking

Core benchmarks exclude networking and persistence. `build/release/core_bench` measures matching; `build/release/durable_bench` includes journal sync. Optional Google Benchmark support is enabled with `./start.sh --enable-google-benchmark`.

`bench/open_loop.py` schedules requests against an existing server. `bench/capacity.py` starts a disposable TLS server and records scheduled-to-response latency, outcomes, resources, and recovery:

```sh
python3 tools/dev_certs.py /tmp/bench-certs
python3 bench/capacity.py --server build/release/asterbook \
  --data-dir /tmp/new-bench-data --output /tmp/new-bench-results \
  --cert-dir /tmp/bench-certs --rate 100 --seconds 60 --clients 8 --shards 2
```

Use fresh directories. The mixed workload covers new, replace, cancel, matching IOC, and expiring IOC orders. `--hot` concentrates load on one shard. `--fault term|kill --fault-at SECONDS` injects process failure. Preserve raw JSONL, `plan.json`, and `summary.json`; failures and generator drops count against the offered workload.

Default capacity thresholds are p99 ≤10 ms, 99.9% correct responses within 50 ms, 99% accepted offered rate, and recovery within 30 s. They are configurable experiment targets, not guarantees. CI checks functional behavior without asserting those latency/throughput targets. Free-disk floor defaults to 10 GiB (`--min-free-disk-bytes`); CI's small fixture uses 16 MiB.

Use `python3 tools/slo_report.py --help` to aggregate request/probe JSONL. Report workload, host, storage, allocator, batch size, build, and duration with results. Compare core, standalone service, and replicated service separately: replication adds a TLS round trip and a second sync per command. The capacity tool currently starts standalone servers; use an external client workload for a deployed replicated pair.

Linux resource samples use `/proc`; macOS uses `ps`, with unsupported counters recorded as null. Process-kill tests do not simulate host cache loss or physical power failure. Sustained capacity and storage durability require target-machine experiments.
