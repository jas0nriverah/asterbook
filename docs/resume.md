# Resume material

Use only statements that reflect work you personally performed. These describe the repository and do not imply production deployment or exchange certification.

- Built a C++20 price-time order book with limit, IOC, cancel and replace operations, deterministic replay, and per-symbol sharding.
- Added a bounded TCP service with mutual TLS, certificate-bound client identities, an administrator policy, and a synchronous journal with recovery and retry deduplication.
- Checked matching behavior against an independent Python model and exercised recovery, injected faults, concurrency, sanitizer builds, and coverage-guided parser fuzzing.
- Implemented an offline C++ ITCH 5.0 displayed-order subset, with reference-model comparison and explicit sequence-gap detection.

Stack: C++20, CMake, Ninja, Python, OpenSSL, TCP sockets, GitHub Actions, Clang, ASan/UBSan, TSan, and libFuzzer. Anduril's [open roles](https://www.anduril.com/open-roles) include early-career software positions; tailor emphasis to the actual posting and your contribution rather than claiming role requirements or experience not demonstrated here.

Future measured bullet template, only after collecting reproducible evidence: “Measured [workload] at [offered rate] with [p99 latency], [error/drop rate], [sample size], and [hardware/build configuration]; compared [baseline] with [change].” Do not fill in a number from the included exploratory smoke run as a production result.

For a systems-focused early-career application, choose three bullets based on your own contribution:

- Implemented deterministic price-time matching and bounded POSIX socket processing in C++20; compared order events and state with an independent Python model across 100,000 generated commands.
- Built journal replay, request deduplication and group commit with process-fault tests; added mutual TLS and certificate-bound identities to the gateway.
- Evaluated optional PMR pooling and durable batches with reproducible synthetic benchmarks, and exercised parsers with coverage-guided libFuzzer and separate sanitizer builds.

Additional technical stack: C++ standard polymorphic memory resources (PMR), POSIX sockets and poll, write-ahead logging and group commit, native offline ITCH parsing, and Google Benchmark where the optional dependency is available. Report measured throughput or latency only with workload, hardware, build, sample count and errors.
