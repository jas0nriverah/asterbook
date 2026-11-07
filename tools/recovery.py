#!/usr/bin/env python3
"""Offline, integrity-checked backup and restore of a durable engine."""

import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

MARKER = ".asterbook-backup-root"
STAGING_MARKER = ".asterbook-backup-staging"
STAGING_CONTENT = "asterbook backup staging v1\n"


def syncdir(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def remove_staging(path):
    # Keep the ownership marker until the potentially large payload is gone.
    for child in path.iterdir():
        if child.name == STAGING_MARKER:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    (path / STAGING_MARKER).unlink(missing_ok=True)
    path.rmdir()


def regular(path):
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"not a regular file: {path}")


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError(f"not a regular file: {path}")
    return os.fdopen(fd, "rb")


def digest(path):
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("not a regular file")
    with os.fdopen(fd, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def copy_sync(source, dest):
    regular(source)
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("not a regular file")
    with os.fdopen(fd, "rb") as src, open(dest, "xb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())


def layout(directory):
    regular(directory / "manifest")
    parts = (directory / "manifest").read_text().split()
    if (
        len(parts) != 3
        or parts[:2] != ["asterbook-shards-v1", "fnv1a64"]
        or not parts[2].isdigit()
    ):
        raise ValueError("invalid shard manifest")
    count = int(parts[2])
    if count < 1 or count > 16:
        raise ValueError("invalid shard count")
    names = ["manifest"]
    for i in range(count):
        base = Path(".") if count == 1 else Path("shard-" + str(i))
        if count > 1 and (
            (directory / base).is_symlink() or not (directory / base).is_dir()
        ):
            raise ValueError("invalid shard directory")
        journal = base / "journal"
        regular(directory / journal)
        names.append(str(journal))
        snapshot = base / "snapshot"
        if (directory / snapshot).exists() or (directory / snapshot).is_symlink():
            regular(directory / snapshot)
            names.append(str(snapshot))
    return names


def verify_engine(verifier, directory, names):
    for name in names:
        if Path(name).name != "journal":
            continue
        cmd = [verifier, str(directory / name)]
        snap = str(Path(name).parent / "snapshot")
        if snap in names:
            cmd.append(str(directory / snap))
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def verify_backup(directory, verifier):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("invalid backup directory")
    regular(directory / "backup-manifest.json")
    manifest = json.loads((directory / "backup-manifest.json").read_text())
    if (
        set(manifest) != {"version", "files"}
        or manifest["version"] != 1
        or not isinstance(manifest["files"], dict)
    ):
        raise ValueError("invalid backup manifest")
    names = layout(directory)
    if set(manifest["files"]) != set(names):
        raise ValueError("invalid backup file names")
    actual = set()
    for root, dirs, files in os.walk(directory, followlinks=False):
        for n in dirs:
            if (Path(root) / n).is_symlink():
                raise ValueError("symlink in backup")
        for n in files:
            actual.add(str((Path(root) / n).relative_to(directory)))
    locks = {name + ".lock" for name in names if Path(name).name == "journal"}
    for name in actual & locks:
        regular(directory / name)
        if (directory / name).stat().st_size != 0:
            raise ValueError("invalid journal lock file")
    if actual - locks != set(names) | {"backup-manifest.json"}:
        raise ValueError("unexpected backup entries")
    for name in names:
        expected = manifest["files"][name]
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or digest(directory / name) != expected
        ):
            raise ValueError("backup checksum mismatch")
    verify_engine(verifier, directory, names)
    return manifest["files"]


def publish(src, dest):
    # Both platforms provide an atomic no-replace directory rename.
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        fn = libc.renamex_np
        args = [os.fsencode(src), os.fsencode(dest), 4]  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        fn = libc.renameat2
        args = [-100, os.fsencode(src), -100, os.fsencode(dest), 1]  # RENAME_NOREPLACE
    else:
        raise ValueError("atomic no-replace rename unsupported on this platform")
    if fn(*args):
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))


def backup_locked(a):
    source, root = Path(a.data_dir), Path(a.root)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("invalid data directory")
    if (
        root.is_symlink()
        or source.resolve() in root.resolve().parents
        or root.resolve() == source.resolve()
    ):
        raise ValueError("invalid backup root")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError("unmanaged backup root must be empty")
        with open(marker, "x") as f:
            f.write("asterbook backup root v1\n")
            f.flush()
            os.fsync(f.fileno())
        syncdir(root)
    regular(marker)
    if marker.read_text() != "asterbook backup root v1\n":
        raise ValueError("invalid backup root marker")
    # The root lock excludes another backup. Only remove staging directories
    # bearing our marker; unmarked or symlinked entries may belong to others.
    for entry in root.iterdir():
        if not re.fullmatch(r"\.incomplete-[a-z0-9_]{8}", entry.name):
            continue
        if entry.is_symlink() or not entry.is_dir():
            continue
        ownership = entry / STAGING_MARKER
        try:
            with open_regular(ownership) as f:
                owned = f.read(128) == STAGING_CONTENT.encode()
        except (OSError, ValueError):
            owned = False
        if owned:
            remove_staging(entry)
            syncdir(root)
    regular(source / "service.lock")
    with open_regular(source / "service.lock") as service:
        fcntl.flock(service, fcntl.LOCK_EX | fcntl.LOCK_NB)
        names = layout(source)
        locks = []
        try:
            for name in names:
                if Path(name).name == "journal":
                    f = open_regular(source / name)
                    locks.append(f)
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            staging = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=root))
            try:
                with open(staging / STAGING_MARKER, "x") as f:
                    f.write(STAGING_CONTENT)
                    f.flush()
                    os.fsync(f.fileno())
                syncdir(staging)
                tmp = staging / "payload"
                tmp.mkdir()
                for name in names:
                    (tmp / name).parent.mkdir(parents=True, exist_ok=True)
                    copy_sync(source / name, tmp / name)
                verify_engine(a.verifier, tmp, names)
                manifest = {"version": 1, "files": {n: digest(tmp / n) for n in names}}
                with open(tmp / "backup-manifest.json", "x") as f:
                    json.dump(manifest, f, sort_keys=True)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                for d in sorted(
                    (p for p in tmp.rglob("*") if p.is_dir()), reverse=True
                ):
                    syncdir(d)
                syncdir(tmp)
                dest = root / (
                    "backup-"
                    + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                    + "-"
                    + uuid.uuid4().hex
                )
                publish(tmp, dest)
                syncdir(root)
            finally:
                if staging.exists():
                    remove_staging(staging)
        finally:
            for f in locks:
                f.close()
    if a.keep:
        candidates = sorted(
            (
                p
                for p in root.iterdir()
                if re.fullmatch(r"backup-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}", p.name)
                and not p.is_symlink()
                and p.is_dir()
            ),
            reverse=True,
        )
        for old in candidates[a.keep :]:
            verify_backup(old, a.verifier)
            shutil.rmtree(old)
            syncdir(root)
    print(dest)


def backup(a):
    root = Path(a.root)
    if root.is_symlink():
        raise ValueError("backup root cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        backup_locked(a)
    finally:
        os.close(fd)


def restore(a):
    started = time.monotonic()
    source, dest = Path(a.backup), Path(a.destination)
    if dest.exists() or dest.is_symlink():
        raise ValueError("destination must not exist")
    if source.resolve() in dest.resolve().parents:
        raise ValueError("destination cannot be inside backup")
    expected = verify_backup(source, a.verifier)
    names = list(expected)
    tmp = Path(tempfile.mkdtemp(prefix=".asterbook-restore-", dir=dest.parent))
    try:
        for n in names:
            (tmp / n).parent.mkdir(parents=True, exist_ok=True)
            copy_sync(source / n, tmp / n)
        for n in names:
            if expected[n] != digest(tmp / n):
                raise ValueError("copy checksum mismatch")
        verify_engine(a.verifier, tmp, names)
        for d in sorted((p for p in tmp.rglob("*") if p.is_dir()), reverse=True):
            syncdir(d)
        syncdir(tmp)
        publish(tmp, dest)
        syncdir(dest.parent)
        print(
            json.dumps(
                {
                    "destination": str(dest),
                    "files": sorted(names),
                    "sha256": {n: digest(dest / n) for n in names},
                    "duration_s": time.monotonic() - started,
                },
                sort_keys=True,
            )
        )
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--data-dir", required=True)
    b.add_argument("--root", required=True)
    b.add_argument("--keep", type=int, default=0)
    b.add_argument("--verifier", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--backup", required=True)
    r.add_argument("--destination", required=True)
    r.add_argument("--verifier", required=True)
    a = p.parse_args()
    if a.action == "backup" and a.keep < 0:
        p.error("--keep must be nonnegative")
    try:
        (backup if a.action == "backup" else restore)(a)
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
    ) as e:
        print(f"recovery: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
