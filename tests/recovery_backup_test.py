import json, os, shutil, signal, socket, struct, subprocess, sys, tempfile, time
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
    b = Path(call("backup", "--data-dir", data, "--root", backups))
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
