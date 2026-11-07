#!/usr/bin/env python3
"""Exercise expiration, CRL update, restart, and certificate rotation."""

import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "tools"))
from client import request

binary = sys.argv[1]

with tempfile.TemporaryDirectory() as tmp:
    d = Path(tmp)
    (d / "index").write_text("")
    (d / "serial").write_text("1000\n")
    (d / "crlnumber").write_text("1000\n")
    (d / "newcerts").mkdir()
    conf = d / "ca.cnf"
    conf.write_text(f"""[ ca ]
default_ca = local
[ local ]
database = {d}/index
new_certs_dir = {d}/newcerts
certificate = {d}/ca.crt
private_key = {d}/ca.key
serial = {d}/serial
crlnumber = {d}/crlnumber
default_md = sha256
default_days = 2
default_crl_days = 2
policy = policy
[ policy ]
commonName = supplied
[ client ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:FALSE
extendedKeyUsage = clientAuth
[ server ]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
basicConstraints = critical,CA:FALSE
extendedKeyUsage = serverAuth
subjectAltName = DNS:localhost,IP:127.0.0.1
""")

    def openssl(*args):
        subprocess.run(
            ["openssl", *map(str, args)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "2",
        "-subj",
        "/CN=Test CA",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout",
        d / "ca.key",
        "-out",
        d / "ca.crt",
    )

    def issue(name, role="client", expired=False, future=False):
        openssl(
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=" + name,
            "-keyout",
            d / (name + ".key"),
            "-out",
            d / (name + ".csr"),
        )
        (d / (name + ".key")).chmod(0o600)
        dates = []
        if expired:
            dates = ["-startdate", "20200101000000Z", "-enddate", "20200102000000Z"]
        elif future:
            dates = ["-startdate", "20400101000000Z", "-enddate", "20400102000000Z"]
        openssl(
            "ca",
            "-batch",
            "-config",
            conf,
            "-extensions",
            role,
            *dates,
            "-in",
            d / (name + ".csr"),
            "-out",
            d / (name + ".crt"),
            "-notext",
        )

    for name, role, expired in [
        ("server", "server", False),
        ("server2", "server", False),
        ("admin", "client", False),
        ("expired", "client", True),
    ]:
        issue(name, role, expired)
    issue("admin2")
    issue("server_expired", "server", expired=True)
    issue("server_future", "server", future=True)
    (d / "admins").write_text("admin\nadmin2\nexpired\n")

    def crl():
        openssl("ca", "-config", conf, "-gencrl", "-out", d / "clients.crl")

    crl()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    def args(server):
        return [
            binary,
            "server",
            "--data-dir",
            str(d / "data"),
            "--port",
            str(port),
            "--tls-cert",
            str(d / (server + ".crt")),
            "--tls-key",
            str(d / (server + ".key")),
            "--tls-ca",
            str(d / "ca.crt"),
            "--tls-crl",
            str(d / "clients.crl"),
            "--admin-policy",
            str(d / "admins"),
        ]

    def connect(name):
        ctx = ssl.create_default_context(cafile=str(d / "ca.crt"))
        ctx.load_cert_chain(d / (name + ".crt"), d / (name + ".key"))
        return ctx.wrap_socket(
            socket.create_connection(("127.0.0.1", port), timeout=1),
            server_hostname="localhost",
        )

    def start(server):
        proc = subprocess.Popen(
            args(server), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        for _ in range(100):
            if proc.poll() is not None:
                raise AssertionError(proc.stderr.read())
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    pass
                return proc
            except OSError:
                time.sleep(0.05)
        raise AssertionError("server did not start")

    def refused(name):
        try:
            with connect(name) as sock:
                request(sock, "V1 READY")
        except (ssl.SSLError, ConnectionError, EOFError, OSError):
            return
        raise AssertionError("invalid client certificate accepted")

    proc = start("server")
    try:
        refused("expired")
        old = connect("admin")
        assert request(old, "V1 READY")["ok"]
        openssl("ca", "-config", conf, "-revoke", d / "admin.crt")
        crl()
        # CRL is loaded at startup: replacing it alone doesn't disconnect clients.
        assert request(old, "V1 READY")["ok"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    try:
        assert old.recv(1) == b""
    finally:
        old.close()
    proc = start("server2")
    try:
        refused("admin")
        refused("expired")
        with connect("admin2") as sock:
            assert sock.getpeercert(binary_form=True) == ssl.PEM_cert_to_DER_cert(
                (d / "server2.crt").read_text()
            )
            assert request(sock, "V1 READY")["ok"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    # A server leaf outside its validity period must fail configuration and startup.
    conf_file = d / "server.conf"
    def write_config(server):
        conf_file.write_text(
            "\n".join(
                [
                    f"data-dir={d}/data",
                    f"tls-cert={d}/{server}.crt",
                    f"tls-key={d}/{server}.key",
                    f"tls-ca={d}/ca.crt",
                    f"tls-crl={d}/clients.crl",
                    f"admin-policy={d}/admins",
                ]
            ) + "\n"
        )

    write_config("server2")
    assert subprocess.run([binary, "validate-config", str(conf_file)], capture_output=True).returncode == 0
    for server in ("server_expired", "server_future"):
        write_config(server)
        check = subprocess.run([binary, "validate-config", str(conf_file)], capture_output=True)
        assert check.returncode != 0, (server, check.stdout, check.stderr)
        started = subprocess.Popen(args(server), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = started.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            started.kill()
            started.communicate()
            raise AssertionError(f"{server} started with an invalid certificate")
        assert started.returncode != 0, (server, out, err)
    # An expired CRL must fail validation before listening.
    openssl(
        "ca",
        "-config",
        conf,
        "-gencrl",
        "-crl_lastupdate",
        "20200101000000Z",
        "-crl_nextupdate",
        "20200102000000Z",
        "-out",
        d / "clients.crl",
    )
    write_config("server2")
    assert (
        subprocess.run(
            [binary, "validate-config", str(conf_file)], capture_output=True
        ).returncode
        != 0
    )
    (d / "clients.crl").write_text("not a CRL\n")
    assert (
        subprocess.run(
            [binary, "validate-config", str(conf_file)], capture_output=True
        ).returncode
        != 0
    )
