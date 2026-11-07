# Development

`./start.sh` checks dependencies, builds, tests, and runs the TLS demo. `./start.sh --check` checks dependencies and configuration only.

```sh
for preset in release asan tsan; do
  cmake --preset "$preset"
  cmake --build --preset "$preset"
  ctest --preset "$preset"
done
python3 reference/differential.py --server build/release/asterbook --seeds 100 --count 1000
```

CI runs Linux/macOS tests, sanitizers, differential checks, fuzzing, cppcheck, Python dependency auditing, and Prometheus rule tests. Tests cover replication loss/promotion, compaction crash boundaries, and backup/restore. Keep runtime data, credentials, builds, and generated reports out of Git.

For Linux libFuzzer:

```sh
cmake -S . -B build/fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DASTERBOOK_FUZZ=ON -DBUILD_TESTING=OFF
cmake --build build/fuzz --target parser_fuzz itch_fuzz
./scripts/fuzz-seeds.sh build/fuzz/corpus
build/fuzz/parser_fuzz build/fuzz/corpus/parser-corpus -runs=100000 -max_len=8192
build/fuzz/itch_fuzz build/fuzz/corpus/itch-corpus -runs=100000 -max_len=512
```

Run alert checks with `promtool check rules deploy/alerts.yml` and `promtool test rules tests/alerts.test.yml`. Keep system OpenSSL and compiler runtimes patched; dependency auditing does not cover host packages.
