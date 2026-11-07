#!/usr/bin/env python3
"""Exercise the HTTP exporter against a real sharded TLS gateway."""

import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tools"))
from client import request

binary = sys.argv[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


with tempfile.TemporaryDirectory() as temp:
    d = Path(temp)
    certs = d / "certs"
    subprocess.run(
        [sys.executable, str(root / "tools/dev_certs.py"), str(certs)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    port, http_port = free_port(), free_port()
    gateway_args = [
        binary,
        "server",
        "--data-dir",
        str(d / "data"),
        "--port",
        str(port),
        "--shards",
        "2",
        "--queue-capacity",
        "4",
        "--tls-cert",
        str(certs / "server.crt"),
        "--tls-key",
        str(certs / "server.key"),
        "--tls-ca",
        str(certs / "ca.crt"),
        "--admin-policy",
        str(certs / "admins.txt"),
    ]
    gateway = subprocess.Popen(
        gateway_args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    exporter = None
    try:
        for _ in range(100):
            if gateway.poll() is not None:
                raise AssertionError(gateway.stderr.read())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    pass
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("gateway did not start")
        ctx = ssl.create_default_context(cafile=str(certs / "ca.crt"))
        ctx.load_cert_chain(certs / "alice.crt", certs / "alice.key")
        with ctx.wrap_socket(
            socket.create_connection(("127.0.0.1", port), timeout=2),
            server_hostname="localhost",
        ) as sock:
            result = request(sock, "V1 CANCEL alice req1 X missing")
            assert result["result"]["accepted"] is False
        exporter = subprocess.Popen(
            [
                sys.executable,
                str(root / "tools/monitor.py"),
                "--port",
                str(http_port),
                "--upstream-port",
                str(port),
                "--ca",
                str(certs / "ca.crt"),
                "--cert",
                str(certs / "admin.crt"),
                "--key",
                str(certs / "admin.key"),
                "--data-dir",
                str(d / "data"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        base = f"http://127.0.0.1:{http_port}"
        for _ in range(100):
            if exporter.poll() is not None:
                raise AssertionError(exporter.stderr.read())
            try:
                with urllib.request.urlopen(base + "/readyz", timeout=1) as response:
                    assert response.status == 200
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            raise AssertionError("exporter did not start")
        body = urllib.request.urlopen(base + "/metrics", timeout=2).read().decode()
        assert "asterbook_up 1\n" in body
        assert "asterbook_queue_capacity 8\n" in body
        assert "asterbook_rejected_commands_total 1\n" in body
        assert 'asterbook_service_log2_ns_bin_total{log2_ns="0"}' in body
        assert "asterbook_disk_available_bytes " in body
        assert 'asterbook_history_capacity{shard="0"} 1000000\n' in body
        assert (
            sum(
                int(line.rsplit(" ", 1)[1])
                for line in body.splitlines()
                if line.startswith("asterbook_history_usage{")
            )
            == 1
        )
        admin = ssl.create_default_context(cafile=str(certs / "ca.crt"))
        admin.load_cert_chain(certs / "admin.crt", certs / "admin.key")
        with admin.wrap_socket(
            socket.create_connection(("127.0.0.1", port), timeout=2),
            server_hostname="localhost",
        ) as sock:
            assert all(
                part["result"]["ok"]
                for part in request(sock, "V1 CHECKPOINT")["shards"]
            )
        gateway.terminate()
        gateway.wait(timeout=10)
        gateway = subprocess.Popen(
            gateway_args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
        )
        for _ in range(100):
            try:
                body = (
                    urllib.request.urlopen(base + "/metrics", timeout=1).read().decode()
                )
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.05)
        else:
            raise AssertionError("restarted gateway unavailable")
        assert (
            sum(
                int(line.rsplit(" ", 1)[1])
                for line in body.splitlines()
                if line.startswith("asterbook_history_usage{")
            )
            == 1
        )
        gateway.terminate()
        gateway.wait(timeout=10)
        try:
            urllib.request.urlopen(base + "/metrics", timeout=3)
            raise AssertionError("dead upstream reported healthy")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503 and b"asterbook_up 0" in exc.read()
    finally:
        if exporter:
            exporter.terminate()
            exporter.wait(timeout=10)
        if gateway.poll() is None:
            gateway.terminate()
            gateway.wait(timeout=10)
