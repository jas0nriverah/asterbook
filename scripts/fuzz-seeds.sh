#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
root="${1:?provide corpus directory}"
mkdir -p "$root/itch-corpus" "$root/parser-corpus"
python3 - "$root/parser-corpus" <<'PYSEED'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
seeds = [b"V1 NEW alice r1 ABC a BUY IOC 100 1", b"V1 NEW bob r2 ABC b SELL GTC 100 1", b"V1 CANCEL alice r3 ABC a", b"V1 REPLACE alice r4 ABC a b 101 2", b"V1 STATE", b"V1 METRICS", b"V1 READY", b"V1 CHECKPOINT", b"V1 NEW a r S o BUY GTC 18446744073709551615 0", b"V1 NEW ! r S o BUY GTC 1 1", b"X" * 4097]
for i, seed in enumerate(seeds): (p / str(i)).write_bytes(seed)
PYSEED
PYTHONPATH=reference python3 - "$root/itch-corpus/lifecycle" <<'PY'
import struct, sys
from test_itch import add, update
frames=[add(kind=b'F'), update(b'E',1,struct.pack('!IQ',2,9)), update(b'C',1,struct.pack('!IQcI',3,10,b'N',130000)), update(b'X',1,struct.pack('!I',1)), update(b'U',1,struct.pack('!QII',2,8,120000)), update(b'D',2)]
open(sys.argv[1], 'wb').write(b''.join(struct.pack('!H',len(x))+x for x in frames))
PY
