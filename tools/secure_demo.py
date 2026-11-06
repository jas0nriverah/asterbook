#!/usr/bin/env python3
"""Run a disposable mutual-TLS trading and recovery demonstration."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time

from client import request


def connect(port, certs, identity):
    context = ssl.create_default_context(cafile=str(certs / "ca.crt"))
    context.load_cert_chain(certs / f"{identity}.crt", certs / f"{identity}.key")
    raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        return context.wrap_socket(raw, server_hostname="localhost")
    except BaseException:
        raw.close()
        raise


@contextmanager
def server(binary, data, port, certs):
    args = [
        str(binary),
        "server",
        "--data-dir",
        str(data),
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
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=errors)
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    errors.seek(0)
                    raise RuntimeError(
                        "server failed to start: "
                        + errors.read().decode(errors="replace")
                    )
                try:
                    with connect(port, certs, "admin"):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("server startup timed out")
                    time.sleep(0.05)
            yield
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.returncode and sys.exc_info()[0] is None:
                errors.seek(0)
                raise RuntimeError(
                    "server failed: " + errors.read().decode(errors="replace")
                )


def send(port, certs, identity, command):
    with connect(port, certs, identity) as sock:
        result = request(sock, "V1 " + command)
    print(f"{identity}: {command}")
    print(json.dumps(result, sort_keys=True))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="asterbook-secure-demo-") as temporary:
        base = Path(temporary)
        certs = base / "certs"
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name("dev_certs.py")), str(certs)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        sell = "NEW alice sell1 ABC ask1 SELL GTC 100 5"
        buy = "NEW bob buy1 ABC bid1 BUY IOC 100 3"
        with server(args.server, base / "data", port, certs):
            if not send(port, certs, "alice", sell).get("accepted"):
                raise RuntimeError("sell rejected")
            trade = send(port, certs, "bob", buy)
            if not trade.get("accepted") or not trade.get("events"):
                raise RuntimeError("trade rejected or missing events")
            before = send(port, certs, "admin", "STATE")
            checkpoint = send(port, certs, "admin", "CHECKPOINT")
            if not checkpoint.get("ok"):
                raise RuntimeError("checkpoint failed")
        print("Restarting server with the same durable data.")
        with server(args.server, base / "data", port, certs):
            after = send(port, certs, "admin", "STATE")
            retry = send(port, certs, "bob", buy)
            if before != after or not retry.get("duplicate"):
                raise RuntimeError("recovery or retry mismatch")
        print("Secure demo complete; temporary data and certificates removed.")


if __name__ == "__main__":
    main()
