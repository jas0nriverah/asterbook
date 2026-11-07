#!/usr/bin/env python3
"""Package already-built Linux executables; run on the target architecture."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import tarfile
import tempfile

p = argparse.ArgumentParser()
p.add_argument("--version", required=True)
p.add_argument("--build-dir", type=Path, required=True)
p.add_argument("--output", type=Path, required=True)
a = p.parse_args()
if platform.system() != "Linux":
    p.error("release packages must be built on Linux")
if not __import__("re").fullmatch(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", a.version
) or a.version in (".", ".."):
    p.error("invalid version")
a.output.mkdir(parents=True, exist_ok=True)
name = f"asterbook-{a.version}-linux-{platform.machine()}"
files = {
    f"bin/{n}": (a.build_dir / n).read_bytes()
    for n in ("asterbook", "asterbook_itch", "recovery_verify")
}
for tool in ("monitor.py", "recovery.py"):
    files[f"tools/{tool}"] = (
        Path(__file__).resolve().parents[1] / "tools" / tool
    ).read_bytes()
manifest = {
    "version": a.version,
    "os": "linux",
    "arch": platform.machine(),
    "sha256": {n: hashlib.sha256(data).hexdigest() for n, data in files.items()},
}
files["manifest.json"] = (
    json.dumps(manifest, sort_keys=True, indent=2) + "\n"
).encode()
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    for name_in, data in files.items():
        path = root / name_in
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if name_in.startswith("bin/") else 0o644)
    archive = a.output / (name + ".tar.gz")
    if archive.exists() or (a.output / (archive.name + ".sha256")).exists():
        p.error("package or checksum already exists")
    with tarfile.open(archive, "x:gz") as tar:
        for path in sorted(files):
            tar.add(root / path, arcname=path, recursive=False)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (a.output / (archive.name + ".sha256")).write_text(f"{digest}  {archive.name}\n")
    print(archive)
