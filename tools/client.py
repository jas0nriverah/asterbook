#!/usr/bin/env python3
"""Send V1 protocol commands over a length-prefixed TCP connection."""
import argparse
import json
import socket
import struct
import sys

MAX_REQUEST = 4096
MAX_RESPONSE = 32 * 1024 * 1024


def recv_exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise EOFError("connection closed during response")
        chunks.extend(chunk)
    return bytes(chunks)


def request(sock, command):
    payload = command.encode("ascii")
    if not payload or len(payload) > MAX_REQUEST:
        raise ValueError("invalid request length")
    sock.sendall(struct.pack("!I", len(payload)) + payload)
    length = struct.unpack("!I", recv_exact(sock, 4))[0]
    if not 0 < length <= MAX_RESPONSE:
        raise ValueError("invalid response length")
    return json.loads(recv_exact(sock, length))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument(
        "commands",
        nargs="*",
        help="quoted commands, without V1 prefix; stdin lines if omitted",
    )
    args = parser.parse_args()
    commands = args.commands or (line.strip() for line in sys.stdin if line.strip())
    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        for command in commands:
            print(
                json.dumps(
                    request(
                        sock, command if command.startswith("V1 ") else "V1 " + command
                    ),
                    sort_keys=True,
                )
            )


if __name__ == "__main__":
    main()
