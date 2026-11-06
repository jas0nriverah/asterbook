#!/usr/bin/env python3
"""Generate disposable local development certificates with the openssl CLI."""
import argparse
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("directory", help="new directory for development certificates")
a = p.parse_args()
d = Path(a.directory)
d.mkdir(mode=0o700, parents=True, exist_ok=False)


def run(*args):
    subprocess.run(
        ["openssl", *map(str, args)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


run(
    "req",
    "-x509",
    "-newkey",
    "rsa:2048",
    "-nodes",
    "-days",
    "2",
    "-subj",
    "/CN=AsterBook development CA",
    "-addext",
    "basicConstraints=critical,CA:TRUE",
    "-addext",
    "keyUsage=critical,keyCertSign,cRLSign",
    "-keyout",
    d / "ca.key",
    "-out",
    d / "ca.crt",
)
(d / "ca.key").chmod(0o600)
for name in ("server", "alice", "bob", "admin"):
    run(
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
    ext = d / (name + ".ext")
    ext.write_text(
        "basicConstraints=critical,CA:FALSE\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
        + (
            "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n"
            if name == "server"
            else "extendedKeyUsage=clientAuth\n"
        )
    )
    run(
        "x509",
        "-req",
        "-days",
        "2",
        "-in",
        d / (name + ".csr"),
        "-CA",
        d / "ca.crt",
        "-CAkey",
        d / "ca.key",
        "-CAcreateserial",
        "-extfile",
        ext,
        "-out",
        d / (name + ".crt"),
    )
    (d / (name + ".key")).chmod(0o600)
    (d / (name + ".csr")).unlink()
    ext.unlink()
(d / "admins.txt").write_text("admin\n")
print("Generated development certificates in", d)
