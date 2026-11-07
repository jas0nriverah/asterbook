# Development

**Workflow:** check dependencies → build → test → run sanitizers → CI.

| Command | Purpose |
| --- | --- |
| `./start.sh --check` | Check dependencies/configuration |
| `./start.sh` | Build, test and run TLS demo |

## Test locally

```sh
for preset in release asan tsan; do
  cmake --preset "$preset"
  cmake --build --preset "$preset"
  ctest --preset "$preset"
done
python3 reference/differential.py --server build/release/asterbook --seeds 100 --count 1000
```

| CI check | Coverage |
| --- | --- |
| Linux/macOS + sanitizers | Runtime correctness, memory and race errors |
| Differential tests | Matching against reference model |
| Recovery tests | Replication loss/promotion, compaction crashes, backup/restore |
| Fuzzing | Protocol and ITCH parsers |
| Static/security checks | cppcheck, Python dependency audit, dependency review |
| Prometheus tests | Alert syntax and behavior |

<details>
<summary>Linux fuzzing commands</summary>

```sh
cmake -S . -B build/fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DASTERBOOK_FUZZ=ON -DBUILD_TESTING=OFF
cmake --build build/fuzz --target parser_fuzz itch_fuzz
./scripts/fuzz-seeds.sh build/fuzz/corpus
build/fuzz/parser_fuzz build/fuzz/corpus/parser-corpus -runs=100000 -max_len=8192
build/fuzz/itch_fuzz build/fuzz/corpus/itch-corpus -runs=100000 -max_len=512
```

</details>

<details>
<summary>Alert checks</summary>

```sh
promtool check rules deploy/alerts.yml
promtool test rules tests/alerts.test.yml
```

</details>

Keep data, credentials, builds and generated reports out of Git. Patch host OpenSSL/compiler runtimes separately; dependency auditing does not cover host packages.
