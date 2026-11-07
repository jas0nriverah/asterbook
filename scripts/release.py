#!/usr/bin/env python3
"""Install a verified release or atomically exchange current and previous."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tarfile
import tempfile

p = argparse.ArgumentParser()
p.add_argument(
    "--root", required=True, type=Path, help="dedicated installation directory"
)
sub = p.add_subparsers(dest="action", required=True)
i = sub.add_parser("install")
i.add_argument("archive", type=Path)
i.add_argument(
    "--sha256", required=True, help="expected archive SHA-256 from a trusted channel"
)
sub.add_parser("rollback")
sub.add_parser("status")
a = p.parse_args()
root = a.root.resolve()
if root == Path("/"):
    p.error("root cannot be /")
if platform.system() != "Linux":
    p.error("release installation requires Linux")
root.mkdir(parents=True, exist_ok=True)
lock_fd = os.open(root / ".release.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
fcntl.flock(lock_fd, fcntl.LOCK_EX)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


releases = root / "releases"
if releases.is_symlink():
    p.error("releases cannot be a symlink")
releases.mkdir(exist_ok=True)


def target(link):
    if not link.is_symlink():
        if link.exists():
            raise ValueError(f"{link} must be a symlink")
        return None
    value = os.readlink(link)
    if (
        not re.fullmatch(r"releases/[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value)
        or not (root / value).is_dir()
    ):
        raise ValueError(f"invalid release link: {link}")
    return value


def switch(link, value):
    temp = root / ("." + link.name + ".new")
    fd, name = tempfile.mkstemp(dir=root, prefix=".link-")
    os.close(fd)
    os.unlink(name)
    try:
        os.symlink(value, name)
        os.replace(name, link)
        sync_dir(root)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


current = root / "current"
previous = root / "previous"
transaction = root / ".release-transaction"


def publish(new_current, new_previous):
    # A durable intent makes interrupted two-link updates recoverable on next invocation.
    fd, name = tempfile.mkstemp(dir=root, prefix=".intent-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"current": new_current, "previous": new_previous}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, transaction)
        sync_dir(root)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    reconcile()


def reconcile():
    if not transaction.exists():
        return
    if transaction.is_symlink():
        raise ValueError("invalid transaction")
    intent = json.loads(transaction.read_text())
    for key in ("current", "previous"):
        value = intent[key]
        if value is not None and (
            not isinstance(value, str)
            or not re.fullmatch(r"releases/[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value)
            or not (root / value).is_dir()
        ):
            raise ValueError("invalid transaction target")
    if intent["current"] is None:
        raise ValueError("invalid current target")
    if intent["previous"] is not None:
        switch(previous, intent["previous"])
    switch(current, intent["current"])
    transaction.unlink()
    sync_dir(root)


try:
    reconcile()
    old = target(current)
    prior = target(previous)
    if a.action == "status":
        print(f"current={old} previous={prior}")
    elif a.action == "rollback":
        if not old or not prior or old == prior:
            raise ValueError(
                "two distinct installed releases are required for rollback"
            )
        publish(prior, old)
        print(f"activated {prior}")
    else:
        data = a.archive.read_bytes()
        if (
            not re.fullmatch(r"[a-fA-F0-9]{64}", a.sha256)
            or hashlib.sha256(data).hexdigest() != a.sha256.lower()
        ):
            raise ValueError("archive checksum mismatch")
        with tarfile.open(fileobj=__import__("io").BytesIO(data), mode="r:gz") as tar:
            members = tar.getmembers()
            expected = {
                "manifest.json",
                "bin/asterbook",
                "bin/asterbook_itch",
                "bin/recovery_verify",
                "tools/monitor.py",
                "tools/recovery.py",
            }
            if (
                len(members) != len(expected)
                or {m.name for m in members} != expected
                or any(not m.isfile() or m.size > 100_000_000 for m in members)
            ):
                raise ValueError("unexpected archive members")
            contents = {m.name: tar.extractfile(m).read() for m in members}
        manifest = json.loads(contents["manifest.json"])
        version = manifest["version"]
        if (
            not isinstance(version, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version)
            or version in (".", "..")
        ):
            raise ValueError("invalid version")
        if manifest["os"] != "linux" or manifest["arch"] != platform.machine():
            raise ValueError("release platform does not match host")
        bins = expected - {"manifest.json"}
        if set(manifest["sha256"]) != bins or any(
            hashlib.sha256(contents[n]).hexdigest() != manifest["sha256"][n]
            for n in bins
        ):
            raise ValueError("manifest checksum mismatch")
        dest = releases / version
        if dest.exists() or dest.is_symlink():
            raise ValueError("release version already exists")
        with tempfile.TemporaryDirectory(dir=releases, prefix=".staging-") as tmp:
            stage = Path(tmp)
            (stage / "bin").mkdir()
            (stage / "tools").mkdir()
            for name, payload in contents.items():
                path = stage / name
                with path.open("wb") as f:
                    f.write(payload)
                    f.flush()
                    os.fchmod(f.fileno(), 0o755 if name.startswith("bin/") else 0o644)
                    os.fsync(f.fileno())
            sync_dir(stage / "bin")
            sync_dir(stage / "tools")
            sync_dir(stage)
            os.rename(stage, dest)
            sync_dir(releases)
        publish(f"releases/{version}", old)
        print(f"activated releases/{version}")
except (ValueError, KeyError, OSError, tarfile.TarError, json.JSONDecodeError) as e:
    p.exit(1, f"release: {e}\n")
