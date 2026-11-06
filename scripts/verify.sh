#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x .venv/bin/cmake ]]; then export PATH="$PWD/.venv/bin:$PATH"; fi
for preset in release asan tsan; do
  cmake --preset "$preset"
  cmake --build --preset "$preset"
  ctest --preset "$preset"
done
if [[ "${ASTERBOOK_RUN_FUZZ:-0}" == 1 ]]; then
  cmake -S . -B build/fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DASTERBOOK_FUZZ=ON -DBUILD_TESTING=OFF
  cmake --build build/fuzz --target parser_fuzz
  build/fuzz/parser_fuzz -runs=10000
fi
