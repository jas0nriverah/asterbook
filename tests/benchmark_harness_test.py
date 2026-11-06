import importlib.util
import random
import subprocess
import sys
from pathlib import Path

script = Path(__file__).resolve().parents[1] / "bench" / "open_loop.py"
spec = importlib.util.spec_from_file_location("open_loop", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert [module.arrival_offset_ns(i, 100, 3) for i in range(6)] == [
    0,
    0,
    0,
    30_000_000,
    30_000_000,
    30_000_000,
]
assert set(module.choose_symbols(100, 4, 100, random.Random(1))) == {0}
a = module.choose_symbols(1000, 4, 0, random.Random(1))
assert set(a) == {0, 1, 2, 3}
assert a == module.choose_symbols(1000, 4, 0, random.Random(1))
for rate in ("nan", "inf", "-inf", "0"):
    result = subprocess.run(
        [sys.executable, str(script), "--insecure-local", "--rate=" + rate],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2 and "finite" in result.stderr
