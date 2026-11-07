import fcntl, json, os, shutil, signal, socket, struct, subprocess, sys, tempfile, time
from pathlib import Path

binary, verifier = sys.argv[1:3]
script = Path(__file__).resolve().parents[1] / "tools/recovery.py"


def call(*args, ok=True):
    r = subprocess.run(
        [sys.executable, str(script), *map(str, args), "--verifier", verifier],
        capture_output=True,
        text=True,
    )
    assert (r.returncode == 0) == ok, (r.stdout, r.stderr)
    return r.stdout.strip()


def request(s, command):
    b = command.encode()
    s.sendall(struct.pack("!I", len(b)) + b)

    def read(n):
        out = b""
        while len(out) < n:
            x = s.recv(n - len(out))
            if not x:
                raise EOFError()
            out += x
        return out

    return json.loads(read(struct.unpack("!I", read(4))[0]))


def start(d, port):
    p = subprocess.Popen(
        [
            binary,
            "server",
            "--insecure-local",
            "--data-dir",
            str(d),
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(100):
        try:
            return p, socket.create_connection(("127.0.0.1", port), timeout=1)
        except OSError:
            time.sleep(0.05)
    raise AssertionError(p.stderr.read())


def stop(p, s):
    s.close()
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    data = root / "data"
    backups = root / "backups"
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    p, s = start(data, port)
    cmd = "V1 NEW c r1 XYZ a SELL GTC 100 5"
    first = request(s, cmd)
    assert first["accepted"]
    bench = Path(__file__).resolve().parents[1] / "bench/open_loop.py"
    smoke = subprocess.run(
        [
            sys.executable,
            str(bench),
            "--insecure-local",
            "--port",
            str(port),
            "--workload",
            "mixed",
            "--rate",
            "30",
            "--count",
            "30",
            "--seed",
            "42",
            "--burst-size",
            "3",
            "--symbols",
            "4",
            "--hot-symbol-percent",
            "80",
            "--slow-client-ms",
            "1",
            "--max-p99-ms",
            "1000",
            "--max-error-rate",
            "0",
            "--max-drop-rate",
            "0",
            "--min-throughput",
            "10",
        ],
        capture_output=True,
        text=True,
    )
    assert smoke.returncode == 0, (smoke.stdout, smoke.stderr)
    result = json.loads(smoke.stdout)
    assert (
        result["scheduled"] == 30
        and result["generator_dropped"] == 0
        and all(result["slo_checks"].values())
    )
    assert request(s, "V1 CHECKPOINT")["ok"]
    assert request(s, "V1 NEW c r2 XYZ b BUY GTC 99 2")["accepted"]
    call("backup", "--data-dir", data, "--root", backups, ok=False)
    stop(p, s)
    # Crash after a copy starts, leaving a marked partial staging directory.
    injected = """import os, runpy, sys
path = sys.argv.pop(1)
ns = runpy.run_path(path, run_name='backup_fault')
g = ns['backup_locked'].__globals__
copy = g['copy_sync']
def interrupt(*args):
    copy(*args)
    os._exit(77)
g['copy_sync'] = interrupt
sys.exit(ns['main']())
"""
    crashed = subprocess.run(
        [sys.executable, "-c", injected, str(script), "backup", "--data-dir", str(data),
         "--root", str(backups), "--verifier", verifier],
        capture_output=True,
    )
    assert crashed.returncode == 77
    staging = list(backups.glob(".incomplete-*"))
    assert len(staging) == 1
    assert (staging[0] / ".asterbook-backup-staging").is_file()
    assert (staging[0] / "payload" / "manifest").is_file()
    # Names alone, and symlinked markers or staging entries, never authorize deletion.
    unknown = backups / ".incomplete-abcdefgh"
    unknown.mkdir()
    (unknown / "keep").write_text("keep")
    (staging[0] / "external-link").symlink_to(unknown, target_is_directory=True)
    linked_marker = backups / ".incomplete-ijklmnop"
    linked_marker.mkdir()
    (linked_marker / ".asterbook-backup-staging").symlink_to(
        staging[0] / ".asterbook-backup-staging"
    )
    linked_dir = backups / ".incomplete-qrstuvwx"
    linked_dir.symlink_to(unknown, target_is_directory=True)
    root_fd = os.open(backups, os.O_RDONLY)
    fcntl.flock(root_fd, fcntl.LOCK_EX)
    waiting = subprocess.Popen(
        [sys.executable, str(script), "backup", "--data-dir", str(data), "--root",
         str(backups), "--verifier", verifier], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        assert waiting.poll() is None and staging[0].exists()
    finally:
        os.close(root_fd)
    out, err = waiting.communicate(timeout=10)
    assert waiting.returncode == 0, (out, err)
    b = Path(out.strip())
    assert not staging[0].exists()
    assert (unknown / "keep").read_text() == "keep"
    assert linked_marker.is_dir() and linked_dir.is_symlink()
    restored = root / "restored"
    report = json.loads(call("restore", "--backup", b, "--destination", restored))
    assert report["duration_s"] >= 0 and "journal" in report["sha256"]
    p, s = start(restored, port)
    retry = request(s, cmd)
    assert retry["duplicate"] and retry["sequence"] == first["sequence"]
    assert request(s, "V1 NEW c r2 XYZ b BUY GTC 99 2")["duplicate"]
    stop(p, s)
    call("restore", "--backup", b, "--destination", restored, ok=False)
    broken = root / "broken"
    shutil.copytree(b, broken)
    with open(broken / "snapshot", "ab") as f:
        f.write(b"x")
    call("restore", "--backup", broken, "--destination", root / "bad", ok=False)
    manifest = json.loads((broken / "backup-manifest.json").read_text())
    manifest["files"]["../escape"] = "0" * 64
    (broken / "backup-manifest.json").write_text(json.dumps(manifest))
    call("restore", "--backup", broken, "--destination", root / "bad", ok=False)
    incomplete = root / "incomplete"
    shutil.copytree(b, incomplete)
    (incomplete / "journal").unlink()
    call("restore", "--backup", incomplete, "--destination", root / "bad", ok=False)
    linked = root / "linked"
    shutil.copytree(b, linked)
    (linked / "journal").unlink()
    (linked / "journal").symlink_to(b / "journal")
    call("restore", "--backup", linked, "--destination", root / "bad", ok=False)
    # A modified snapshot with a matching outer hash must still fail replay validation.
    import hashlib

    damaged = root / "damaged"
    shutil.copytree(b, damaged)
    with open(damaged / "snapshot", "ab") as f:
        f.write(b"x")
    m = json.loads((damaged / "backup-manifest.json").read_text())
    m["files"]["snapshot"] = hashlib.sha256(
        (damaged / "snapshot").read_bytes()
    ).hexdigest()
    (damaged / "backup-manifest.json").write_text(json.dumps(m))
    call("restore", "--backup", damaged, "--destination", root / "bad", ok=False)
