#!/usr/bin/env python3
import json
import socket
import time
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from http.server import ThreadingHTTPServer

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tools"))
import monitor

binary = sys.argv[1]
with tempfile.TemporaryDirectory() as tmp:
    config = Path(tmp) / "server.conf"

    def validate(text):
        config.write_text(text)
        return subprocess.run(
            [binary, "validate-config", str(config)], capture_output=True
        ).returncode

    assert validate("insecure-local=true\nbind=127.0.0.1\nport=9000\n") == 0
    for text in (
        "port=0\n",
        "unknown=1\n",
        "port=123\nport=124\n",
        "insecure-local=maybe\n",
        "insecure-local=true\nbind=0.0.0.0\n",
        "insecure-local=true\ndata-dir=/dev/null/child\n",
    ):
        assert validate(text) != 0


# Failed upstream must produce a scrape failure and explicit up=0, never stale data.
def fail(*_):
    raise OSError("unavailable")


monitor.upstream = fail
server = monitor.BoundedServer(
    ("127.0.0.1", 0), monitor.handler(SimpleNamespace()), limit=2
)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_port}"
try:
    assert urllib.request.urlopen(base + "/healthz").status == 200
    for path in ("/readyz", "/metrics"):
        try:
            urllib.request.urlopen(base + path)
            raise AssertionError("upstream failure accepted")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            if path == "/metrics":
                assert b"asterbook_up 0" in exc.read()
finally:
    server.shutdown()
    server.server_close()
    thread.join()
assert monitor.ready({"shards": [{"result": {"ok": True}}, {"result": {"ok": True}}]})
assert not monitor.ready({"shards": [{"result": {"error": "STORAGE_UNAVAILABLE"}}]})

# Slow headers occupy at most the configured number of worker slots; timeout frees them.
server = monitor.BoundedServer(
    ("127.0.0.1", 0), monitor.handler(SimpleNamespace()), limit=1
)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
first = socket.create_connection(server.server_address)
first.sendall(b"GET /healthz HTTP/1.1\r\n")
time.sleep(0.1)
second = socket.create_connection(server.server_address)
second.settimeout(1)
second.sendall(b"GET /healthz HTTP/1.0\r\n\r\n")
try:
    assert second.recv(64) == b""
except ConnectionResetError:
    pass
second.close()
first.close()
time.sleep(0.1)
assert (
    urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/healthz").status
    == 200
)
server.shutdown()
server.server_close()
thread.join()
result = subprocess.run(
    [
        sys.executable,
        str(root / "tools/monitor.py"),
        "--upstream-host",
        "localhost",
        "--ca",
        "unused",
        "--cert",
        "unused",
        "--key",
        "unused",
    ],
    capture_output=True,
)
assert result.returncode != 0 and b"numeric IP" in result.stderr


# Slow-drip headers cannot hold a worker indefinitely despite continuing traffic.
class ShortDeadlineServer(monitor.BoundedServer):
    request_deadline = 0.3


server = ShortDeadlineServer(
    ("127.0.0.1", 0), monitor.handler(SimpleNamespace()), limit=1
)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
slow = socket.create_connection(server.server_address)
slow.settimeout(1)
slow.sendall(b"GET /healthz HTTP/1.1\r\nX: ")
for _ in range(10):
    time.sleep(0.06)
    try:
        slow.sendall(b"a")
    except OSError:
        break
try:
    assert slow.recv(1) == b""
except (ConnectionResetError, BrokenPipeError):
    pass
slow.close()
assert (
    urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/healthz").status
    == 200
)
server.shutdown()
server.server_close()
thread.join()
