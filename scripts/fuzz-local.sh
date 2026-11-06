#!/usr/bin/env bash
# Bootstrap official compiler-rt libFuzzer in the ignored build tree; no sudo.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -x .venv/bin/cmake ]]; then export PATH="$PWD/.venv/bin:$PATH"; fi
root="$PWD/build/fuzzer-runtime"
mkdir -p "$root"
archive="$root/compiler-rt-21.1.8.src.tar.xz"
url=https://github.com/llvm/llvm-project/releases/download/llvmorg-21.1.8/compiler-rt-21.1.8.src.tar.xz
expected=dd54ae21aee1780fac59445b51ebff601ad016b31ac3a7de3b21126fd3ccb229
[[ -f "$archive" ]] || curl -fL --retry 2 "$url" -o "$archive"
actual=$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$archive")
[[ "$actual" == "$expected" ]] || { echo 'compiler-rt archive SHA-256 mismatch' >&2; exit 1; }
if [[ ! -f "$root/libFuzzer.a" ]]; then
  tar -xf "$archive" -C "$root"
  (cd "$root" && CXX=clang++ sh compiler-rt-21.1.8.src/lib/fuzzer/build.sh)
fi
cmake -S . -B build/fuzz -G Ninja -DCMAKE_CXX_COMPILER=clang++ -DASTERBOOK_FUZZ=ON -DBUILD_TESTING=OFF -DASTERBOOK_FUZZ_RUNTIME="$root/libFuzzer.a"
cmake --build build/fuzz --target parser_fuzz itch_fuzz
./scripts/fuzz-seeds.sh "$root"
build/fuzz/parser_fuzz "$root/parser-corpus" -runs="${ASTERBOOK_FUZZ_RUNS:-100000}" -max_len=8192
build/fuzz/itch_fuzz "$root/itch-corpus" -runs="${ASTERBOOK_FUZZ_RUNS:-100000}" -max_len=512
