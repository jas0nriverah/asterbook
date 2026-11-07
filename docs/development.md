# Development

The capacity and SLI tools are described in [experimental qualification](qualification.md).
CTest includes their functional and analyzer regressions. Prometheus rule
behavior is checked with promtool test rules tests/alerts.test.yml.

`./scripts/setup.sh` builds the release preset, runs tests and runs the secure demonstration. It can install CMake and Ninja in `.venv` and build OpenSSL in `.deps/` when needed; these steps require network access. Linux and macOS are supported development targets. On Ubuntu, install dependencies with:

```sh
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build python3 python3-venv clang libssl-dev
```

Run release, address/undefined-behavior sanitizer and thread sanitizer builds separately:

```sh
for preset in release asan tsan; do
  cmake --preset "$preset"
  cmake --build --preset "$preset"
  ctest --preset "$preset"
done
```

`./scripts/verify.sh` runs these checks. Sanitizer runtime availability depends on the host. CTest includes a seeded comparison against the independent scan-based model; run a larger comparison with:

```sh
python3 reference/differential.py --server build/release/asterbook --seeds 100 --count 1000
```

For the Linux Clang libFuzzer targets:

```sh
cmake -S . -B build/fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DASTERBOOK_FUZZ=ON -DBUILD_TESTING=OFF
cmake --build build/fuzz --target parser_fuzz itch_fuzz
./scripts/fuzz-seeds.sh build/fuzz/corpus
build/fuzz/parser_fuzz build/fuzz/corpus/parser-corpus -runs=100000 -max_len=8192
build/fuzz/itch_fuzz build/fuzz/corpus/itch-corpus -runs=100000 -max_len=512
```

The GitHub Actions workflows configure Linux builds, tests and fuzzing. Optional formatters are in `requirements-dev.txt`; keep builds, runtime data and local environments out of commits. Preserve seeds when adding randomized tests.

Linux release packaging and systemd units are documented in [operations](operations.md). CI also runs release activation and rollback tests in temporary directories, static analysis, optional dependency review, and Python dependency auditing; these checks do not replace host-specific deployment and restore tests.

Security workflow coverage: cppcheck analyzes C++ sources; pip-audit checks the optional Python development requirements; dependency review checks supported pull-request dependency changes on public repositories; private repositories must set repository variable `ENABLE_DEPENDENCY_REVIEW` to `true` only when GitHub Code Security or Advanced Security is enabled. Dependabot tracks GitHub Actions and the Python requirements. These do not audit the host OpenSSL/C++ runtime or the pinned OpenSSL source used by `scripts/bootstrap-openssl.sh`. Track OpenSSL security advisories and vendor updates separately, update the bootstrap version and checksum together, and rebuild artifacts after updating. CI scans run on GitHub, not automatically on a developer host.
