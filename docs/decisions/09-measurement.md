# 9. Measurement

**Context.** Core execution time, service processing and client round-trip latency answer different questions; a single throughput number conceals queueing and rejection.

**Decision.** Keep an instrumented core benchmark and a separate scheduled-arrival client harness. Report boundaries, workload, offered load, errors or drops, sample counts, generator lag and machine/build details with raw output. See [benchmarking](../benchmarking.md), [core benchmark](../../bench/core_bench.cpp), and [load generator](../../bench/open_loop.py).

**Alternatives and consequences.** Closed-loop measurements alone reduce offered load when the service slows. The Python scheduled-arrival harness has scheduling and generator limits and is not a coordinated-omission-corrected histogram. Clock overhead affects short core timings; results are synthetic, not production guarantees. No unmeasured latency target is claimed.

The optional Google Benchmark target uses a pinned source archive. Run `scripts/setup.sh --enable-google-benchmark` to download and build it, then run `build/release/microbench --benchmark_min_warmup_time=0.1 --benchmark_repetitions=5`. The two arguments in benchmark names are allocator (0 baseline, 1 pooled) and workload (0 add, 1 cross, 2 cancel). Each sample starts with a fresh engine, and construction and preload are outside measured time. These are core-only fixed-input microbenchmarks; they do not include network, TLS, queueing, journaling, or durable sync. Short runs on a busy machine are smoke checks, not comparative performance claims.
