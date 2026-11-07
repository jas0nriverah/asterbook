#!/usr/bin/env python3
"""Loopback Prometheus adapter for the mutually authenticated admin protocol."""

import argparse
import ipaddress
import socket
import ssl
import struct
import time
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

COUNTERS = (
    "batches",
    "wal_syncs",
    "rejected_commands",
    "accepted_connections",
    "processed",
    "overload",
    "malformed",
    "rate_limited",
)
HISTOGRAMS = ("queue_wait_log2_ns", "service_log2_ns")
COUNTER_HELP = {
    "batches": "Command batches processed by shard workers.",
    "wal_syncs": "Successful journal synchronization operations.",
    "rejected_commands": "Business commands rejected by shard workers.",
    "accepted_connections": "Client connections accepted by the gateway.",
    "processed": "Work items processed by shard workers.",
    "overload": "Gateway connection and shard queue overload events.",
    "malformed": "Malformed requests received by the gateway.",
    "rate_limited": "Requests rejected by the per-connection rate limit.",
}
UP_HELP = "Whether upstream metrics and data filesystem availability were collected successfully."


def upstream(args, command):
    deadline = time.monotonic() + args.timeout
    context = ssl.create_default_context(cafile=args.ca)
    context.load_cert_chain(args.cert, args.key)
    with socket.create_connection(
        (args.upstream_host, args.upstream_port), timeout=args.timeout
    ) as raw:
        raw.settimeout(max(0.001, deadline - time.monotonic()))
        with context.wrap_socket(raw, server_hostname=args.server_name) as conn:

            def remaining():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("upstream deadline exceeded")
                conn.settimeout(left)

            payload = ("V1 " + command).encode("ascii")
            remaining()
            conn.sendall(struct.pack("!I", len(payload)) + payload)

            def receive(n):
                data = bytearray()
                while len(data) < n:
                    remaining()
                    chunk = conn.recv(n - len(data))
                    if not chunk:
                        raise EOFError("upstream closed")
                    data.extend(chunk)
                return bytes(data)

            length = struct.unpack("!I", receive(4))[0]
            if not 0 < length <= 16384:
                raise ValueError("invalid upstream response size")
            value = json.loads(receive(length))
            if not isinstance(value, dict):
                raise ValueError("invalid upstream response")
            return value


def ready(result):
    if result.get("ok") is True:
        return True
    shards = result.get("shards")
    return bool(shards) and all(s.get("result", {}).get("ok") is True for s in shards)


def metrics(data):
    lines = []
    for name in COUNTERS:
        value = data[name]
        if type(value) is not int or value < 0:
            raise ValueError("invalid counter")
        lines.extend(
            (
                f"# HELP asterbook_{name}_total {COUNTER_HELP[name]}",
                f"# TYPE asterbook_{name}_total counter",
                f"asterbook_{name}_total {value}",
            )
        )
    depth = data["queue_depth"]
    if type(depth) is not int or depth < 0:
        raise ValueError("invalid queue depth")
    lines.extend(
        ("# HELP asterbook_queue_depth Total number of queued work items.",
         "# TYPE asterbook_queue_depth gauge", f"asterbook_queue_depth {depth}")
    )
    capacity = data["queue_capacity"]
    if type(capacity) is not int or capacity <= 0:
        raise ValueError("invalid queue capacity")
    lines.extend(
        (
            "# HELP asterbook_queue_capacity Total capacity of shard work queues.",
            "# TYPE asterbook_queue_capacity gauge",
            f"asterbook_queue_capacity {capacity}",
        )
    )
    histories, history_capacity = data["history_usage"], data["history_capacity"]
    if (
        not isinstance(histories, list)
        or not 1 <= len(histories) <= 16
        or type(history_capacity) is not int
        or history_capacity <= 0
        or any(type(v) is not int or not 0 <= v <= history_capacity for v in histories)
    ):
        raise ValueError("invalid history capacity")
    lines.extend(
        (
            "# HELP asterbook_history_usage Retained request history entries per shard.",
            "# TYPE asterbook_history_usage gauge",
            "# HELP asterbook_history_capacity Maximum retained request history entries per shard.",
            "# TYPE asterbook_history_capacity gauge",
        )
    )
    for shard, usage in enumerate(histories):
        lines.append(f'asterbook_history_usage{{shard="{shard}"}} {usage}')
        lines.append(
            f'asterbook_history_capacity{{shard="{shard}"}} {history_capacity}'
        )
    for name in HISTOGRAMS:
        values = data[name]
        if (
            not isinstance(values, list)
            or len(values) != 32
            or any(type(v) is not int or v < 0 for v in values)
        ):
            raise ValueError("invalid histogram")
        # The gateway reports non-cumulative log2 bins, with no sum. Expose the
        # actual bins as counters rather than inventing a Prometheus histogram sum.
        family = f"asterbook_{name.removesuffix('_ns')}_bin_total"
        description = "Queue wait" if name.startswith("queue_wait") else "Service time"
        lines.append(f"# HELP {family} {description} observations per log2 nanosecond bin.")
        lines.append(f"# TYPE {family} counter")
        lines.extend(
            f'{family}{{log2_ns="{i}"}} {v}'
            for i, v in enumerate(values)
        )
    lines.extend((f"# HELP asterbook_up {UP_HELP}", "# TYPE asterbook_up gauge", "asterbook_up 1"))
    return "\n".join(lines) + "\n"


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    request_deadline = 15

    def __init__(self, address, handler_class, limit=16):
        self.slots = threading.BoundedSemaphore(limit)
        super().__init__(address, handler_class)

    def handle_error(self, request, client_address):
        # Disconnects and deadlines are expected and contain no useful payload.
        pass

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(3)
        return conn, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        # An absolute deadline prevents trickled HTTP headers from monopolizing
        # all slots even if each individual socket read stays below its timeout.
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        timer = threading.Timer(self.request_deadline, expire)
        timer.daemon = True
        timer.start()
        try:
            super().process_request_thread(request, client_address)
        finally:
            timer.cancel()
            self.slots.release()


def handler(args):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/healthz":
                status, body = 200, "ok\n"
            elif self.path in ("/readyz", "/metrics"):
                try:
                    value = upstream(
                        args, "READY" if self.path == "/readyz" else "METRICS"
                    )
                    if self.path == "/readyz":
                        status, body = (
                            (200, "ready\n") if ready(value) else (503, "not ready\n")
                        )
                    else:
                        status, body = 200, metrics(value)
                        disk = os.statvfs(args.data_dir)
                        body += (
                            "# HELP asterbook_disk_available_bytes Bytes available to unprivileged users on the data filesystem.\n"
                            "# TYPE asterbook_disk_available_bytes gauge\nasterbook_disk_available_bytes "
                            + str(disk.f_bavail * disk.f_frsize)
                            + "\n"
                        )
                except (OSError, ValueError, KeyError, TypeError, EOFError):
                    status, body = 503, (
                        f"# HELP asterbook_up {UP_HELP}\n# TYPE asterbook_up gauge\nasterbook_up 0\n"
                        if self.path == "/metrics"
                        else "not ready\n"
                    )
            else:
                status, body = 404, "not found\n"
            payload = body.encode("ascii")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--listen", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9108)
    p.add_argument("--upstream-host", default="127.0.0.1")
    p.add_argument("--upstream-port", type=int, default=9000)
    p.add_argument("--server-name", default="localhost")
    p.add_argument("--ca", required=True)
    p.add_argument("--cert", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--data-dir", default="asterbook-data")
    p.add_argument("--timeout", type=float, default=2.0)
    args = p.parse_args()
    try:
        if not ipaddress.ip_address(args.listen).is_loopback:
            p.error("--listen must be a loopback IP")
    except ValueError:
        p.error("--listen must be a loopback IP")
    try:
        ipaddress.ip_address(args.upstream_host)
    except ValueError:
        p.error("--upstream-host must be a numeric IP address")
    if (
        not 0 < args.timeout <= 10
        or not 0 < args.port <= 65535
        or not 0 < args.upstream_port <= 65535
    ):
        p.error("invalid timeout or port")
    # Fail early for unusable credentials.
    context = ssl.create_default_context(cafile=args.ca)
    context.load_cert_chain(args.cert, args.key)
    BoundedServer((args.listen, args.port), handler(args)).serve_forever()


if __name__ == "__main__":
    main()
