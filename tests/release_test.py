import hashlib
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import tempfile

repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    build = base / "build"
    build.mkdir()
    for name in ("asterbook", "asterbook_itch", "recovery_verify"):
        (build / name).write_text("#!/bin/sh\nexit 0\n")
    output = base / "packages"
    root = base / "install"

    def package(version):
        # Exercise packaging on development hosts as well as Linux CI.
        code = (
            "import platform,runpy; platform.system=lambda:'Linux'; runpy.run_path(%r,run_name='__main__')"
            % str(repo / "scripts/package-release.py")
        )
        subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                "--version",
                version,
                "--build-dir",
                str(build),
                "--output",
                str(output),
            ],
            check=True,
        )
        return output / f"asterbook-{version}-linux-{platform.machine()}.tar.gz"

    def install(archive, digest, check=True):
        code = (
            "import platform,runpy; platform.system=lambda:'Linux'; runpy.run_path(%r,run_name='__main__')"
            % str(repo / "scripts/release.py")
        )
        return subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                "--root",
                str(root),
                "install",
                str(archive),
                "--sha256",
                digest,
            ],
            check=check,
            capture_output=True,
        )

    def checksum(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def mode(path):
        return stat.S_IMODE(path.stat().st_mode)

    def check_release_modes(install_root, version):
        release = install_root / "releases" / version
        assert all(mode(p) == 0o755 for p in (release, release / "bin", release / "tools"))
        assert all(mode(release / "bin" / n) == 0o755 for n in
                   ("asterbook", "asterbook_itch", "recovery_verify"))
        assert all(mode(release / "tools" / n) == 0o644 for n in ("monitor.py", "recovery.py"))
        assert mode(release / "manifest.json") == 0o644

    one = package("1.0")
    two = package("2.0")
    assert install(one, "0" * 64, False).returncode != 0
    install(one, checksum(one))
    assert os.readlink(root / "current") == "releases/1.0"
    assert mode(root) == 0o755 and mode(root / "releases") == 0o755
    check_release_modes(root, "1.0")
    # Even a restrictive installer umask must permit a service account to read
    # a newly created installation, while respecting existing directory policy.
    restricted = base / "restricted-umask"
    preserved = base / "preserved"
    preserved.mkdir(mode=0o700)
    (preserved / "releases").mkdir(mode=0o700)
    for destination in (restricted, preserved):
        code = (
            "import platform,runpy; platform.system=lambda:'Linux'; runpy.run_path(%r,run_name='__main__')"
            % str(repo / "scripts/release.py")
        )
        subprocess.run(
            [sys.executable, "-c", code, "--root", str(destination), "install", str(one),
             "--sha256", checksum(one)], check=True, capture_output=True,
            preexec_fn=lambda: os.umask(0o077),
        )
        check_release_modes(destination, "1.0")
    assert mode(restricted) == mode(restricted / "releases") == 0o755
    assert mode(preserved) == mode(preserved / "releases") == 0o700
    assert install(one, checksum(one), False).returncode != 0
    install(two, checksum(two))
    assert os.readlink(root / "previous") == "releases/1.0"
    code = (
        "import platform,runpy; platform.system=lambda:'Linux'; runpy.run_path(%r,run_name='__main__')"
        % str(repo / "scripts/release.py")
    )
    subprocess.run(
        [sys.executable, "-c", code, "--root", str(root), "rollback"], check=True
    )
    assert os.readlink(root / "current") == "releases/1.0"
    assert os.readlink(root / "previous") == "releases/2.0"
    assert install(two, "x" * 64, False).returncode != 0
    # Simulate a crash after previous changes and before current is replaced.
    three = package("3.0")
    code = """import os,platform,runpy
platform.system=lambda:'Linux'
original=os.replace
def fail(src,dst):
    if str(dst).endswith('/current'): raise OSError('injected interruption')
    return original(src,dst)
os.replace=fail
runpy.run_path(%r,run_name='__main__')
""" % str(repo / "scripts/release.py")
    interrupt_code = code
    interrupted = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            "--root",
            str(root),
            "install",
            str(three),
            "--sha256",
            checksum(three),
        ],
        capture_output=True,
    )
    assert interrupted.returncode != 0 and (root / ".release-transaction").exists()
    code = (
        "import platform,runpy; platform.system=lambda:'Linux'; runpy.run_path(%r,run_name='__main__')"
        % str(repo / "scripts/release.py")
    )
    subprocess.run(
        [sys.executable, "-c", code, "--root", str(root), "status"], check=True
    )
    assert os.readlink(root / "current") == "releases/3.0"
    assert os.readlink(root / "previous") == "releases/1.0"
    assert not (root / ".release-transaction").exists()
    unsafe = base / "unsafe"
    unsafe.mkdir()
    (unsafe / "releases").symlink_to(root / "releases")
    assert (
        subprocess.run(
            [sys.executable, "-c", code, "--root", str(unsafe), "status"],
            capture_output=True,
        ).returncode
        != 0
    )
    interrupted = subprocess.run(
        [sys.executable, "-c", interrupt_code, "--root", str(root), "rollback"],
        capture_output=True,
    )
    assert interrupted.returncode != 0 and (root / ".release-transaction").exists()
    subprocess.run(
        [sys.executable, "-c", code, "--root", str(root), "status"], check=True
    )
    assert os.readlink(root / "current") == "releases/1.0"
    assert os.readlink(root / "previous") == "releases/3.0"
