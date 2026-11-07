#!/usr/bin/env python3
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from client import request

binary = sys.argv[1]
root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as temp:
    d = Path(temp)
    certs = d / "certs"
    subprocess.run(
        [sys.executable, str(root / "tools/dev_certs.py"), str(certs)], check=True
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    args = [
        binary,
        "server",
        "--data-dir",
        str(d / "data"),
        "--port",
        str(port),
        "--tls-cert",
        str(certs / "server.crt"),
        "--tls-key",
        str(certs / "server.key"),
        "--tls-ca",
        str(certs / "ca.crt"),
        "--admin-policy",
        str(certs / "admins.txt"),
    ]
    server = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def connect(name="alice", hostname="localhost", ca=None):
        ctx = ssl.create_default_context(cafile=str(ca or certs / "ca.crt"))
        if name:
            ctx.load_cert_chain(certs / (name + ".crt"), certs / (name + ".key"))
        raw = socket.create_connection(("127.0.0.1", port), timeout=2)
        return ctx.wrap_socket(raw, server_hostname=hostname)

    try:
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    pass
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("server did not start")
        with socket.create_connection(("127.0.0.1", port), timeout=2) as stalled:
            stalled.settimeout(12)
            start = time.monotonic()
            assert stalled.recv(1) == b""
            assert 9 <= time.monotonic() - start < 12
        for name, host in [("alice", "wrong.example"), (None, "localhost")]:
            try:
                with connect(name, host) as s:
                    request(s, "V1 STATE")
                raise AssertionError("invalid peer accepted")
            except (ssl.SSLError, ConnectionError, EOFError, OSError):
                pass
        other = d / "other"
        subprocess.run(
            [sys.executable, str(root / "tools/dev_certs.py"), str(other)], check=True
        )
        try:
            with connect(ca=other / "ca.crt"):
                pass
            raise AssertionError("untrusted server accepted")
        except ssl.SSLError:
            pass
        try:
            ctx = ssl.create_default_context(cafile=str(certs / "ca.crt"))
            ctx.load_cert_chain(other / "alice.crt", other / "alice.key")
            with ctx.wrap_socket(
                socket.create_connection(("127.0.0.1", port)),
                server_hostname="localhost",
            ) as s:
                request(s, "V1 STATE")
            raise AssertionError("untrusted client accepted")
        except (ssl.SSLError, ConnectionError, EOFError, OSError):
            pass
        with connect() as s:
            assert request(s, "V1 STATE")["error"] == "FORBIDDEN"
            assert request(s, "V1 READY")["error"] == "FORBIDDEN"
            assert (
                request(s, "V1 NEW bob r1 X o1 BUY GTC 100 1")["error"]
                == "IDENTITY_MISMATCH"
            )
        with connect("admin") as s:
            assert request(s, "V1 READY")["ok"] is True
            assert "queue_depth" in request(s, "V1 METRICS")
            payload = b"V1 METRICS"
            frames = (struct.pack("!I", len(payload)) + payload) * 500
            for i in range(0, len(frames), 7):
                s.sendall(frames[i : i + 7])
            from client import recv_exact

            for _ in range(500):
                size = struct.unpack("!I", recv_exact(s, 4))[0]
                assert size > 0
                recv_exact(s, size)
        with connect("alice") as s:
            for i in range(80):
                response = request(s, f"V1 NEW alice bulk{i} X bulk{i} BUY GTC 100 1")
                assert response.get("accepted") is True, response
        with connect("admin") as s:
            payload = b"V1 STATE"
            s.sendall(struct.pack("!I", len(payload)) + payload)
            time.sleep(0.2)
            from client import recv_exact

            size = struct.unpack("!I", recv_exact(s, 4))[0]
            assert size > 4096
            data = bytearray()
            while len(data) < size:
                chunk = s.recv(min(257, size - len(data)))
                assert chunk
                data.extend(chunk)
        with connect("alice") as s:
            response = request(s, "V1 NEW alice r1 X o1 BUY GTC 100 1")
            assert response.get("accepted") is True, response
        with connect("bob") as s:
            response = request(s, "V1 CANCEL bob r2 X o1")
            assert response.get("accepted") is False, response
        with connect("alice") as s:
            response = request(s, "V1 CANCEL alice r2 X o1")
            assert response.get("accepted") is True, response
    finally:
        server.terminate()
        server.wait(timeout=10)
    server = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                with connect("alice") as s:
                    response = request(s, "V1 NEW alice r1 X o1 BUY GTC 100 1")
                    assert response.get("duplicate") is True, response
                break
            except (ConnectionError, OSError):
                time.sleep(0.05)
        else:
            raise AssertionError("restart failed")
    finally:
        server.terminate()
        server.wait(timeout=10)
    shard_args = list(args)
    shard_args[3] = str(d / "shards")
    shard_args.extend(["--shards", "2"])
    server = subprocess.Popen(
        shard_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        for _ in range(100):
            try:
                with connect("alice") as s:
                    assert request(s, "V1 NEW alice sr1 X so1 BUY GTC 100 1")["result"][
                        "accepted"
                    ]
                with connect("admin") as s:
                    assert len(request(s, "V1 STATE")["shards"]) == 2
                break
            except (ConnectionError, OSError):
                time.sleep(0.05)
        else:
            raise AssertionError("sharded server failed")
    finally:
        server.terminate()
        server.wait(timeout=10)
    bad = subprocess.run(
        [binary, "server", "--data-dir", str(d / "bad"), "--port", str(port)],
        capture_output=True,
    )
    assert bad.returncode != 0
    bad = subprocess.run(
        [binary, "server", "--bind", "0.0.0.0", "--insecure-local"], capture_output=True
    )
    assert bad.returncode != 0
    (certs / "server.key").chmod(0o644)
    bad = subprocess.run(args, capture_output=True)
    assert bad.returncode != 0
    (certs / "server.key").chmod(0o600)
    (certs / "admins.txt").write_text("admin\nadmin\n")
    bad = subprocess.run(args, capture_output=True)
    assert bad.returncode != 0
