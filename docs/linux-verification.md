# Ubuntu verification

These commands reproduce the Linux CI checks on Ubuntu 26.04 from the repository root. Install the compiler, CMake, Ninja, Python and OpenSSL development headers:

```sh
sudo apt-get update
sudo apt-get install -y build-essential cmake ninja-build python3 python3-venv clang libssl-dev
```

Build and run the release, address/undefined-behavior sanitizer, and thread sanitizer test suites separately:

```sh
for preset in release asan tsan; do
  cmake --preset "$preset"
  cmake --build --preset "$preset"
  ctest --preset "$preset"
done
```

The CTest suite includes a seeded differential comparison against the independent scan-based reference model. To run a larger comparison explicitly after building release:

```sh
python3 reference/differential.py --server build/release/asterbook --seeds 100 --count 1000
```

For the Linux Clang libFuzzer targets, matching CI's seeded input budget:

```sh
cmake -S . -B build/fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DASTERBOOK_FUZZ=ON -DBUILD_TESTING=OFF
cmake --build build/fuzz --target parser_fuzz itch_fuzz
./scripts/fuzz-seeds.sh build/fuzz/corpus
build/fuzz/parser_fuzz build/fuzz/corpus/parser-corpus -runs=100000 -max_len=8192
build/fuzz/itch_fuzz build/fuzz/corpus/itch-corpus -runs=100000 -max_len=512
```

These tests provide evidence about correctness for exercised inputs and fault scenarios; they do not establish durability through physical power loss. They also do not establish throughput or latency capacity. For measurement conditions, limitations and reproducible performance commands, see [benchmarking](benchmarking.md). Record the OS, compiler, dependencies, commit, commands and results when reporting a run. See [verification](verification.md) for the existing local record.
