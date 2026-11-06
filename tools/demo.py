#!/usr/bin/env python3
"""Start a temporary server, show FIFO and retry after a restart."""
import argparse
import json
import signal
import socket
import subprocess
import tempfile
import time
from client import request


def start(binary, data, port):
    process = subprocess.Popen(
        [binary, "server", "--data-dir", data, "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(process.stderr.read().decode())
        try:
            return process, socket.create_connection(("127.0.0.1", port), timeout=1)
        except OSError:
            time.sleep(0.05)
    process.terminate()
    process.wait()
    raise RuntimeError("server did not start")


def stop(process, sock):
    sock.close()
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=10)
    if process.returncode:
        raise RuntimeError(process.stderr.read().decode())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--server", required=True)
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="asterbook-demo-") as data:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        proc, sock = start(args.server, data, port)
        try:
            commands = [
                "V1 NEW demo r1 ABC a SELL GTC 100 5",
                "V1 NEW demo r2 ABC b SELL GTC 100 4",
                "V1 NEW demo r3 ABC c BUY IOC 100 7",
            ]
            for command in commands:
                print(command)
                print(json.dumps(request(sock, command), sort_keys=True))
            before = request(sock, "V1 STATE")
            print("STATE before restart:", json.dumps(before, sort_keys=True))
        finally:
            stop(proc, sock)
        proc, sock = start(args.server, data, port)
        try:
            after = request(sock, "V1 STATE")
            retry = request(sock, commands[-1])
            print("STATE after restart:", json.dumps(after, sort_keys=True))
            print("Retry last request:", json.dumps(retry, sort_keys=True))
            if before != after or not retry.get("duplicate"):
                raise AssertionError("restart/retry mismatch")
        finally:
            stop(proc, sock)


if __name__ == "__main__":
    main()
