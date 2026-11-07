#!/usr/bin/env python3
"""Scheduled synthetic arrivals; records schedule-to-response and generator lag."""

import argparse, concurrent.futures, ipaddress, json, math, os, platform, random, socket, ssl, struct, subprocess, sys, threading, time, uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from client import request, recv_exact, MAX_RESPONSE
from source_digest import source_digest


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[int(q * (len(values) - 1))]


def arrival_offset_ns(i, rate, burst_size):
    return int((i // burst_size) * burst_size * 1e9 / rate)


def choose_symbols(count, symbol_count, hot_percent, randomizer):
    return [
        (
            0
            if randomizer.randrange(100) < hot_percent
            else randomizer.randrange(symbol_count)
        )
        for _ in range(count)
    ]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=9000)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--insecure-local", action="store_true")
    p.add_argument("--ca")
    p.add_argument("--cert")
    p.add_argument("--key")
    p.add_argument("--principal", default="alice")
    p.add_argument("--rate", type=float, default=100)
    p.add_argument("--count", type=int, default=1000)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--max-inflight", type=int, default=128)
    p.add_argument(
        "--workload", choices=["add", "cancel", "cross", "deep", "mixed"], default="add"
    )
    p.add_argument("--seed", type=int, default=0)
    p.add_argument(
        "--slow-client-ms", type=float, default=0, help="delay before reading responses"
    )
    p.add_argument("--max-p99-ms", type=float, default=None)
    p.add_argument("--max-error-rate", type=float, default=None)
    p.add_argument("--max-drop-rate", type=float, default=None)
    p.add_argument("--min-throughput", type=float, default=None)
    p.add_argument(
        "--burst-size", type=int, default=1, help="arrivals per scheduled burst"
    )
    p.add_argument("--symbols", type=int, default=1, help="number of synthetic symbols")
    p.add_argument(
        "--hot-symbol-percent",
        type=int,
        default=0,
        help="percentage directed to symbol S0; remaining traffic is uniform",
    )
    p.add_argument(
        "--namespace", default=None, help="unique run namespace; default random UUID"
    )
    a = p.parse_args()
    if a.insecure_local:
        try:
            if not ipaddress.ip_address(a.host).is_loopback:
                p.error("insecure mode requires loopback IP")
        except ValueError:
            p.error("insecure mode requires loopback IP")
        if a.ca or a.cert or a.key:
            p.error("cannot combine TLS and insecure options")
        context = None
    else:
        if not all((a.ca, a.cert, a.key)):
            p.error("--ca, --cert and --key are required")
        context = ssl.create_default_context(cafile=a.ca)
        context.load_cert_chain(a.cert, a.key)
    namespace = a.namespace or uuid.uuid4().hex
    if (
        not namespace.isascii()
        or not namespace.isalnum()
        or len(namespace) + 1 + len(str(a.count)) > 64
    ):
        p.error("namespace must be ASCII alphanumeric and fit 64-byte order IDs")
    if (
        not math.isfinite(a.rate)
        or min(a.rate, a.count, a.workers, a.max_inflight) <= 0
    ):
        p.error(
            "rate must be finite and rate, count, workers, and max-inflight must be positive"
        )
    if a.burst_size < 1 or a.symbols < 1 or not 0 <= a.hot_symbol_percent <= 100:
        p.error(
            "burst-size and symbols must be positive; hot-symbol-percent must be 0..100"
        )
    if not math.isfinite(a.slow_client_ms) or a.slow_client_ms < 0:
        p.error("slow-client-ms must be finite and nonnegative")
    for value in (a.max_p99_ms, a.max_error_rate, a.max_drop_rate, a.min_throughput):
        if value is not None and (not math.isfinite(value) or value < 0):
            p.error("SLO thresholds must be finite and nonnegative")
    if any(
        value is not None and value > 1 for value in (a.max_error_rate, a.max_drop_rate)
    ):
        p.error("error and drop rates must be between 0 and 1")
    randomizer = random.Random(a.seed)
    prices = [100 + randomizer.randrange(1000) for _ in range(a.count)]
    symbols = choose_symbols(a.count, a.symbols, a.hot_symbol_percent, randomizer)
    # One connection per thread avoids interleaving frames and permits bounded concurrency.
    local = threading.local()
    sockets = []
    lock = threading.Lock()

    def send(i, scheduled):
        submitted = time.monotonic_ns()
        try:
            if not hasattr(local, "sock"):
                local.sock = socket.create_connection((a.host, a.port), timeout=10)
                if context:
                    local.sock = context.wrap_socket(local.sock, server_hostname=a.host)
                local.sock.settimeout(10)
                with lock:
                    sockets.append(local.sock)
            workload = (
                ("add", "cancel", "cross")[i % 3]
                if a.workload == "mixed"
                else a.workload
            )
            side = "BUY" if workload in ("add", "cancel") or i % 2 else "SELL"
            price = (
                99
                if workload == "add"
                else (
                    prices[i]
                    if workload == "deep"
                    else (98 if workload == "cancel" else 100)
                )
            )
            # Churn uses each worker's own add then cancel; RTT covers both requests.
            symbol = f"S{symbols[i]}"
            cmd = f"V1 NEW {a.principal} {namespace}r{i} {symbol} {namespace}o{i} {side} GTC {price} 1"
            if a.slow_client_ms:
                payload = cmd.encode("ascii")
                local.sock.sendall(struct.pack("!I", len(payload)) + payload)
                time.sleep(a.slow_client_ms / 1000)
                length = struct.unpack("!I", recv_exact(local.sock, 4))[0]
                if not 0 < length <= MAX_RESPONSE:
                    raise ValueError("invalid response length")
                response = json.loads(recv_exact(local.sock, length))
            else:
                response = request(local.sock, cmd)
            response = response.get("result", response)
            if workload == "cancel" and response.get("accepted"):
                response = request(
                    local.sock,
                    f"V1 CANCEL {a.principal} {namespace}c{i} {symbol} {namespace}o{i}",
                )
                response = response.get("result", response)
            return (
                scheduled,
                submitted,
                time.monotonic_ns(),
                response.get("accepted", False),
                None,
                response.get("code", response.get("error")),
                response.get("duplicate", False),
            )
        except (OSError, EOFError, ValueError) as exc:
            # A partial request or response makes this stream unsafe to reuse.
            if hasattr(local, "sock"):
                local.sock.close()
                del local.sock
            return (
                scheduled,
                submitted,
                time.monotonic_ns(),
                False,
                str(exc),
                "TRANSPORT_ERROR",
                False,
            )

    start = time.monotonic_ns()
    pending = set()
    results = []
    dropped = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
        for i in range(a.count):
            scheduled = start + arrival_offset_ns(i, a.rate, a.burst_size)
            delay = (scheduled - time.monotonic_ns()) / 1e9
            if delay > 0:
                time.sleep(delay)
            done = {f for f in pending if f.done()}
            pending -= done
            for f in done:
                results.append(f.result())
            if len(pending) >= a.max_inflight:
                dropped += 1
                continue
            f = pool.submit(send, i, scheduled)
            f.scheduled = scheduled
            pending.add(f)
        for f in concurrent.futures.as_completed(pending):
            results.append(f.result())
    end = time.monotonic_ns()
    for s in sockets:
        s.close()
    latency = [
        (finish - scheduled) / 1e6
        for scheduled, submitted, finish, _, _, _, _ in results
    ]
    lag = [
        (submitted - scheduled) / 1e6
        for scheduled, submitted, finish, _, _, _, _ in results
    ]
    errors = [err for _, _, _, _, err, _, _ in results if err]
    output = {
        "workload": a.workload,
        "offered_rate_per_s": a.rate,
        "scheduled": a.count,
        "submitted": len(results),
        "generator_dropped": dropped,
        "accepted": sum(ok for _, _, _, ok, _, _, _ in results),
        "rejected_or_failed": sum(not ok for _, _, _, ok, _, _, _ in results),
        "elapsed_s": (end - start) / 1e9,
        "achieved_completions_per_s": len(results) / ((end - start) / 1e9),
        "latency_boundary": "scheduled arrival to decoded response; cancel includes two round trips",
        "latency_ms": {
            "p50": percentile(latency, 0.5),
            "p95": percentile(latency, 0.95),
            "p99": percentile(latency, 0.99),
        },
        "generator_lag_ms": {
            "p50": percentile(lag, 0.5),
            "p95": percentile(lag, 0.95),
            "p99": percentile(lag, 0.99),
        },
        "submission_to_response_ms": {
            "p50": percentile(
                [
                    (finish - submitted) / 1e6
                    for scheduled, submitted, finish, _, _, _, _ in results
                ],
                0.5,
            ),
            "p95": percentile(
                [
                    (finish - submitted) / 1e6
                    for scheduled, submitted, finish, _, _, _, _ in results
                ],
                0.95,
            ),
            "p99": percentile(
                [
                    (finish - submitted) / 1e6
                    for scheduled, submitted, finish, _, _, _, _ in results
                ],
                0.99,
            ),
        },
        "duplicates": sum(duplicate for _, _, _, _, _, _, duplicate in results),
        "rejection_codes": {
            code: sum(1 for _, _, _, ok, _, c, _ in results if not ok and c == code)
            for code in set(c for _, _, _, ok, _, c, _ in results if not ok)
        },
        "namespace": namespace,
        "errors_sample": errors[:5],
        "seed": a.seed,
        "burst_size": a.burst_size,
        "symbol_count": a.symbols,
        "hot_symbol_percent": a.hot_symbol_percent,
        "workers": a.workers,
        "max_inflight": a.max_inflight,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor(),
        "commit": (lambda r: r.stdout.strip() if r.returncode == 0 else None)(
            subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
        ),
        "source_tree_sha256": source_digest(),
        "git_dirty": bool(
            subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, text=True
            ).stdout
        ),
        "clock": time.get_clock_info("monotonic").implementation,
        "build_flags": "server build flags must be recorded separately",
    }
    error_rate = output["rejected_or_failed"] / a.count
    drop_rate = dropped / a.count
    checks = {
        "p99": a.max_p99_ms is None
        or (
            output["latency_ms"]["p99"] is not None
            and output["latency_ms"]["p99"] <= a.max_p99_ms
        ),
        "error_rate": a.max_error_rate is None or error_rate <= a.max_error_rate,
        "drop_rate": a.max_drop_rate is None or drop_rate <= a.max_drop_rate,
        "throughput": a.min_throughput is None
        or output["achieved_completions_per_s"] >= a.min_throughput,
    }
    output.update(
        error_rate=error_rate,
        drop_rate=drop_rate,
        slo_checks=checks,
        slow_client_ms=a.slow_client_ms,
    )
    print(json.dumps(output, sort_keys=True))
    if not all(checks.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
