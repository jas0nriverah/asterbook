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
mkdir -p "$root/itch-corpus" "$root/parser-corpus"
python3 - "$root/parser-corpus" <<'PYSEED'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
seeds = [b"V1 NEW alice r1 ABC a BUY IOC 100 1", b"V1 NEW bob r2 ABC b SELL GTC 100 1", b"V1 CANCEL alice r3 ABC a", b"V1 REPLACE alice r4 ABC a b BUY GTC 101 2", b"V1 STATE", b"V1 METRICS", b"V1 CHECKPOINT", b"V1 NEW a r S o BUY GTC 18446744073709551615 0", b"V1 NEW ! r S o BUY GTC 1 1", b"X" * 4097]
for i, seed in enumerate(seeds): (p / str(i)).write_bytes(seed)
PYSEED
PYTHONPATH=reference python3 - "$root/itch-corpus/lifecycle" <<'PY'
import struct, sys
from test_itch import add, update
frames=[add(kind=b'F'), update(b'E',1,struct.pack('!IQ',2,9)), update(b'C',1,struct.pack('!IQcI',3,10,b'N',130000)), update(b'X',1,struct.pack('!I',1)), update(b'U',1,struct.pack('!QII',2,8,120000)), update(b'D',2)]
open(sys.argv[1], 'wb').write(b''.join(struct.pack('!H',len(x))+x for x in frames))
PY
build/fuzz/parser_fuzz "$root/parser-corpus" -runs="${ASTERBOOK_FUZZ_RUNS:-100000}" -max_len=8192
build/fuzz/itch_fuzz "$root/itch-corpus" -runs="${ASTERBOOK_FUZZ_RUNS:-100000}" -max_len=512
