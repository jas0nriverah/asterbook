#!/usr/bin/env python3
import json, os, signal, socket, struct, subprocess, sys, tempfile, time

binary = sys.argv[1]


def recvexact(s, n):
    out = b""
    while len(out) < n:
        b = s.recv(n - len(out))
        if not b:
            raise EOFError()
        out += b
    return out


def frame(s):
    b = s.encode()
    return struct.pack("!I", len(b)) + b


def read(s):
    return json.loads(recvexact(s, struct.unpack("!I", recvexact(s, 4))[0]))


def request(s, x):
    s.sendall(frame(x))
    return read(s)


with tempfile.TemporaryDirectory() as d:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    p = subprocess.Popen(
        [binary, "server", "--insecure-local", "--data-dir", d, "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(100):
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("server did not start")
        s.settimeout(5)
        data = frame("V1 NEW c r1 XYZ a SELL GTC 100 5")
        s.sendall(data[:2])
        s.sendall(data[2:])
        first = read(s)
        assert first["accepted"]
        s.sendall(frame("V1 NEW c r2 XYZ b BUY IOC 100 3") + frame("V1 STATE"))
        assert read(s)["accepted"]
        assert isinstance(read(s), dict)
        # Frames already in the gateway input buffer must not each wait for the
        # poll timeout after the preceding response is sent.
        s.sendall(frame("V1 METRICS") * 80)
        started = time.monotonic()
        for _ in range(80):
            assert "queue_depth" in read(s)
        assert time.monotonic() - started < 3.0
        assert not request(s, "V1 NEW bad")["ok"]
        retry = request(s, "V1 NEW c r1 XYZ a SELL GTC 100 5")
        assert retry["duplicate"] and retry["sequence"] == first["sequence"]
        t = socket.create_connection(("127.0.0.1", port))
        t.sendall(struct.pack("!I", 4097))
        assert t.recv(1) == b""
        t.close()
        t = socket.create_connection(("127.0.0.1", port))
        t.sendall(frame("V1 NEW c r3 XYZ z BUY GTC 99 1"))
        t.close()
        metrics = request(s, "V1 METRICS")
        assert "malformed" in metrics
        assert (
            len(metrics["queue_wait_log2_ns"]) == len(metrics["service_log2_ns"]) == 32
        )
        assert (
            sum(metrics["queue_wait_log2_ns"]) >= 2
            and sum(metrics["service_log2_ns"]) >= 2
        )
        assert (
            metrics["batches"] >= 2 and 1 <= metrics["wal_syncs"] <= metrics["batches"]
        )
        assert request(s, "V1 CHECKPOINT")["ok"]
        s.close()
    finally:
        p.send_signal(signal.SIGTERM)
        p.wait(timeout=10)
        if p.returncode:
            raise RuntimeError(p.stderr.read().decode())
