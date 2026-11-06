import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reference"))
from itch import Reconstructor
from test_itch import add, update

binary = sys.argv[1]
frames = [
    add(kind=b"F"),
    update(b"E", 1, struct.pack("!IQ", 2, 9)),
    update(b"C", 1, struct.pack("!IQcI", 3, 10, b"N", 130000)),
    update(b"X", 1, struct.pack("!I", 1)),
    update(b"U", 1, struct.pack("!QII", 2, 8, 120000)),
    add(ref=3, side=b"S"),
]
ref = Reconstructor()
with tempfile.NamedTemporaryFile() as f:
    for frame in frames:
        ref.apply(frame)
        f.write(struct.pack("!H", len(frame)) + frame)
        f.flush()
        output = subprocess.check_output([binary, f.name], text=True)
        assert json.loads(output) == ref.snapshot()
    f.seek(0)
    f.write(b"\x00\x05abc")
    f.truncate()
    f.flush()
    assert subprocess.run([binary, f.name], capture_output=True).returncode != 0

with tempfile.NamedTemporaryFile() as f:
    for i, frame in enumerate(frames):
        f.write(struct.pack("!QH", 10 + i, len(frame)) + frame)
    f.flush()
    assert (
        json.loads(subprocess.check_output([binary, "--sequenced", f.name]))
        == ref.snapshot()
    )
    f.seek(0)
    f.write(
        struct.pack("!QH", 1, len(frames[0]))
        + frames[0]
        + struct.pack("!QH", 3, len(frames[1]))
        + frames[1]
    )
    f.truncate()
    f.flush()
    assert (
        subprocess.run([binary, "--sequenced", f.name], capture_output=True).returncode
        != 0
    )
