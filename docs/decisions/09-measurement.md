# 9. Measurement

**Context.** Core execution time, service processing and client round-trip latency answer different questions; a single throughput number conceals queueing and rejection.

**Decision.** Keep an instrumented core benchmark and a separate scheduled-arrival client harness. Report boundaries, workload, offered load, errors or drops, sample counts, generator lag and machine/build details with raw output. See [benchmarking](../benchmarking.md), [core benchmark](../../bench/core_bench.cpp), and [load generator](../../bench/open_loop.py).

**Alternatives and consequences.** Closed-loop measurements alone reduce offered load when the service slows. The Python scheduled-arrival harness has scheduling and generator limits and is not a coordinated-omission-corrected histogram. Clock overhead affects short core timings; results are synthetic, not production guarantees. No unmeasured latency target is claimed.
