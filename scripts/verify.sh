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
  ./scripts/fuzz-local.sh
fi
