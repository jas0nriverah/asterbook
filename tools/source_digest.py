#!/usr/bin/env python3
"""Print a deterministic SHA-256 digest of repository source and configuration."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {".git", ".venv", "build", "__pycache__", "results", "asterbook-data"}
EXCLUDED_FILES = {".DS_Store", "differential-repro.txt"}


def source_digest():
    digest = hashlib.sha256()
    files = sorted(
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and not EXCLUDED_DIRS.intersection(p.relative_to(ROOT).parts)
        and p.name not in EXCLUDED_FILES
        and not p.name.endswith((".pyc", ".journal", ".snapshot"))
    )
    for path in files:
        name = path.relative_to(ROOT).as_posix().encode()
        contents = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update(len(contents).to_bytes(8, "big"))
        digest.update(contents)
    return digest.hexdigest()


if __name__ == "__main__":
    print(source_digest())
