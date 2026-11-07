"""Synchronous TLS replication, fail-closed writes, compaction and fenced promotion."""

from pathlib import Path
import shutil
import json
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
import socket
import ssl
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from client import request

binary = str(Path(sys.argv[1]).resolve())
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    certs = root / "certs"
    subprocess.run(
        [sys.executable, str(ROOT / "tools/dev_certs.py"), str(certs)], check=True
    )
    processes = []

    def port():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def query(endpoint, command, identity="admin"):
        ctx = ssl.create_default_context(cafile=str(certs / "ca.crt"))
        ctx.load_cert_chain(certs / f"{identity}.crt", certs / f"{identity}.key")
        with socket.create_connection(("127.0.0.1", endpoint), timeout=7) as raw:
            with ctx.wrap_socket(raw, server_hostname="localhost") as sock:
                return request(sock, "V1 " + command)

    def spawn(data, endpoint, shards, *extra, wait=True):
        args = [
            binary,
            "server",
            "--data-dir",
            str(data),
            "--port",
            str(endpoint),
            "--shards",
            str(shards),
            "--tls-cert",
            str(certs / "server.crt"),
            "--tls-key",
            str(certs / "server.key"),
            "--tls-ca",
            str(certs / "ca.crt"),
            "--admin-policy",
            str(certs / "admins.txt"),
            *extra,
        ]
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        processes.append(proc)
        if wait:
            for _ in range(200):
                if proc.poll() is not None:
                    raise AssertionError(proc.stderr.read().decode())
                try:
                    query(endpoint, "READY")
                    break
                except (OSError, EOFError):
                    time.sleep(0.05)
            else:
                raise AssertionError("startup timeout")
        return proc

    def stop(proc):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=15)

    def mirror(endpoint, name="localhost"):
        return [
            "--replica-host",
            "127.0.0.1",
            "--replica-port",
            str(endpoint),
            "--replica-name",
            name,
            "--replica-cert",
            str(certs / "bob.crt"),
            "--replica-key",
            str(certs / "bob.key"),
            "--replica-ca",
            str(certs / "ca.crt"),
        ]

    def result(value):
        return value.get("result", value)

    try:
        for shards in (1, 2):
            standby_port, primary_port = port(), port()
            standby_data, primary_data = root / f"s{shards}", root / f"p{shards}"
            standby = spawn(
                standby_data, standby_port, shards, "--standby-identity", "bob"
            )
            primary = spawn(primary_data, primary_port, shards, *mirror(standby_port))
            command = "NEW alice r1 XYZ a SELL GTC 100 5"
            assert query(standby_port, command, "alice")["error"] == "STANDBY_READ_ONLY"
            first = result(query(primary_port, command, "alice"))
            assert first["accepted"] and not first["duplicate"]
            assert result(query(primary_port, command, "alice"))["duplicate"]
            for command in [
                "REPLACE alice r2 XYZ a b 101 4",
                "NEW alice r3 XYZ c BUY IOC 101 2",
                "CANCEL alice r4 XYZ b",
                "NEW alice r5 ABC d BUY GTC 99 7",
            ]:
                assert result(query(primary_port, command, "alice"))["accepted"]
            with ThreadPoolExecutor(max_workers=8) as pool:
                responses = list(
                    pool.map(
                        lambda i: query(
                            primary_port,
                            f"NEW alice concurrent{i} S{i} concurrent{i} BUY GTC 2 1",
                            "alice",
                        ),
                        range(16),
                    )
                )
            assert all(result(response)["accepted"] for response in responses)
            expected = query(primary_port, "STATE")
            assert query(standby_port, "STATE") == expected
            assert query(primary_port, "COMPACT").get("ok", True)
            assert query(standby_port, "COMPACT").get("ok", True)
            stop(primary)
            stop(standby)
            backup_root = root / f"backups{shards}"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/recovery.py"),
                    "backup",
                    "--data-dir",
                    str(standby_data),
                    "--root",
                    str(backup_root),
                    "--verifier",
                    str(Path(binary).with_name("recovery_verify")),
                ],
                check=True,
            )
            backup = next(backup_root.glob("backup-*"))
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools/recovery.py"),
                    "restore",
                    "--backup",
                    str(backup),
                    "--destination",
                    str(root / f"restored{shards}"),
                    "--verifier",
                    str(Path(binary).with_name("recovery_verify")),
                ],
                check=True,
            )
            standby = spawn(
                standby_data, standby_port, shards, "--standby-identity", "bob"
            )
            primary = spawn(primary_data, primary_port, shards, *mirror(standby_port))
            assert query(primary_port, "STATE") == expected
            assert result(query(primary_port, command, "alice"))["duplicate"]
            # Loss of the standby cannot silently degrade acknowledged writes.
            stop(standby)
            try:
                reply = query(
                    primary_port,
                    "NEW alice unavailable XYZ unavailable BUY GTC 1 1",
                    "alice",
                )
                assert not result(reply).get("accepted", False), reply
            except (OSError, EOFError):
                pass
            primary.wait(timeout=10)
            assert primary.returncode != 0
            # The failed primary is fenced (its process has exited) before promotion.
            promoted = spawn(standby_data, standby_port, shards)
            assert query(standby_port, "STATE") == expected
            assert result(query(standby_port, command, "alice"))["duplicate"]
            assert result(
                query(standby_port, "NEW alice promoted XYZ new BUY GTC 1 1", "alice")
            )["accepted"]
            stop(promoted)
            # Stale state and wrong hostnames fail startup, never accept clients.
            standby = spawn(
                standby_data, standby_port, shards, "--standby-identity", "bob"
            )
            stale = spawn(
                primary_data, primary_port, shards, *mirror(standby_port), wait=False
            )
            assert stale.wait(timeout=10) != 0
            bad_name = spawn(
                root / f"bad{shards}",
                primary_port,
                shards,
                *mirror(standby_port, "wrong.invalid"),
                wait=False,
            )
            assert bad_name.wait(timeout=10) != 0
            stop(standby)
            # Re-seed a stopped node from the authoritative copy before rejoining.
            shutil.copytree(standby_data, root / f"reseed{shards}")
            standby = spawn(
                root / f"reseed{shards}",
                primary_port,
                shards,
                "--standby-identity",
                "bob",
            )
            promoted = spawn(standby_data, standby_port, shards, *mirror(primary_port))
            assert result(
                query(standby_port, "NEW alice rejoined XYZ last BUY GTC 1 1", "alice")
            )["accepted"]
            assert query(primary_port, "STATE") == query(standby_port, "STATE")
            stop(promoted)
            stop(standby)
        # Drop a reply after the standby has synced: no acknowledgement is issued,
        # the primary stops, and promotion retains the ambiguous command.
        standby_port, proxy_port, primary_port = port(), port(), port()
        standby = spawn(
            root / "ambiguous-standby", standby_port, 1, "--standby-identity", "bob"
        )
        listener = socket.socket()
        listener.bind(("127.0.0.1", proxy_port))
        listener.listen()
        listener.settimeout(0.2)
        shutdown = threading.Event()
        dropped = threading.Event()
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(certs / "server.crt", certs / "server.key")
        context.load_verify_locations(cafile=str(certs / "ca.crt"))
        context.verify_mode = ssl.CERT_REQUIRED

        def relay():
            while not shutdown.is_set():
                try:
                    raw, _ = listener.accept()
                except socket.timeout:
                    continue
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as sock:

                        def read_exact(size):
                            data = b""
                            while len(data) < size:
                                part = sock.recv(size - len(data))
                                if not part:
                                    raise EOFError()
                                data += part
                            return data

                        length = struct.unpack("!I", read_exact(4))[0]
                        command = read_exact(length).decode()[3:]
                        value = query(standby_port, command, "bob")
                        if command.startswith("NEW"):
                            dropped.set()
                            continue
                        data = json.dumps(value, separators=(",", ":")).encode()
                        sock.sendall(struct.pack("!I", len(data)) + data)

        thread = threading.Thread(target=relay)
        thread.start()
        try:
            primary = spawn(
                root / "ambiguous-primary", primary_port, 1, *mirror(proxy_port)
            )
            command = "NEW alice ambiguous X uncertain BUY GTC 1 1"
            try:
                value = query(primary_port, command, "alice")
                assert not value.get("accepted", False), value
            except (OSError, EOFError):
                pass
            primary.wait(timeout=10)
            assert primary.returncode != 0 and dropped.wait(2)
            assert query(standby_port, "STATE")["orders"][0]["id"] == "uncertain"
            stop(standby)
            promoted = spawn(root / "ambiguous-standby", standby_port, 1)
            assert query(standby_port, command, "alice")["duplicate"]
            stop(promoted)
        finally:
            shutdown.set()
            thread.join(timeout=6)
            listener.close()
            assert not thread.is_alive()
    finally:
        for proc in processes:
            stop(proc)
            if proc.stderr:
                proc.stderr.close()
