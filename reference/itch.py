#!/usr/bin/env python3
"""Strict, offline subset of Nasdaq TotalView-ITCH 5.0 displayed-book updates.

Input is an application-specific sequence of big-endian uint16 length + ITCH
payload. This is not SoupBinTCP or MoldUDP64; sequencing/gap detection is the
caller's responsibility. Unsupported message types stop reconstruction.
"""
import argparse
import json
import struct
import sys

LENGTHS = {
    ord("A"): 36,
    ord("F"): 40,
    ord("E"): 31,
    ord("C"): 36,
    ord("X"): 23,
    ord("D"): 19,
    ord("U"): 35,
}


class FeedError(ValueError):
    pass


def number(data, offset, size):
    return int.from_bytes(data[offset : offset + size], "big")


class Reconstructor:
    def __init__(self):
        self.orders = {}
        self.seen = set()

    def apply(self, payload):
        if not payload:
            raise FeedError("empty payload")
        kind = payload[0]
        if kind not in LENGTHS:
            raise FeedError(f"unsupported message type {kind:#x}")
        if len(payload) != LENGTHS[kind]:
            raise FeedError(f"invalid {chr(kind)} length {len(payload)}")
        locate, ref = number(payload, 1, 2), number(payload, 11, 8)
        if kind in (ord("A"), ord("F")):
            side = payload[19:20]
            qty, price = number(payload, 20, 4), number(payload, 32, 4)
            try:
                symbol = payload[24:32].decode("ascii").rstrip(" ")
                attribution = (
                    payload[36:40].decode("ascii") if kind == ord("F") else None
                )
            except UnicodeDecodeError as exc:
                raise FeedError("non-ASCII stock or attribution") from exc
            if any(not 32 <= c <= 126 for c in payload[24:32]) or (
                kind == ord("F") and any(not 32 <= c <= 126 for c in payload[36:40])
            ):
                raise FeedError("non-printable stock or attribution")
            if (
                ref in self.seen
                or side not in (b"B", b"S")
                or not qty
                or not price
                or not symbol
            ):
                raise FeedError("invalid or duplicate add")
            self.orders[ref] = {
                "locate": locate,
                "side": side.decode(),
                "shares": qty,
                "symbol": symbol,
                "price": price,
                "attribution": attribution,
            }
            self.seen.add(ref)
            return
        if ref not in self.orders or self.orders[ref]["locate"] != locate:
            raise FeedError("unknown order or stock locate mismatch")
        old = self.orders[ref]
        if kind == ord("D"):
            del self.orders[ref]
        elif kind == ord("U"):
            newref, qty, price = (
                number(payload, 19, 8),
                number(payload, 27, 4),
                number(payload, 31, 4),
            )
            if newref in self.seen or not qty or not price:
                raise FeedError("invalid or duplicate replacement")
            self.orders[newref] = dict(old, shares=qty, price=price)
            self.seen.add(newref)
            del self.orders[ref]
        else:
            qty = number(payload, 19, 4)
            if not qty or qty > old["shares"]:
                raise FeedError("invalid reduction")
            if kind == ord("C") and payload[31:32] not in (b"Y", b"N"):
                raise FeedError("invalid printable flag")
            old["shares"] -= qty
            if not old["shares"]:
                del self.orders[ref]

    def snapshot(self):
        return [
            {"reference": ref, **order} for ref, order in sorted(self.orders.items())
        ]


def read_frames(stream):
    while True:
        header = stream.read(2)
        if not header:
            return
        if len(header) != 2:
            raise FeedError("truncated length")
        length = struct.unpack("!H", header)[0]
        data = stream.read(length)
        if len(data) != length:
            raise FeedError("truncated payload")
        yield data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", help="length-prefixed offline binary file")
    args = parser.parse_args()
    book = Reconstructor()
    try:
        with open(args.file, "rb") as stream:
            for frame in read_frames(stream):
                book.apply(frame)
    except (OSError, FeedError) as exc:
        print(f"feed error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(book.snapshot(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
