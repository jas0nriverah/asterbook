#!/usr/bin/env python3
"""Seeded, scan-based independent oracle against `asterbook replay`."""
import argparse
import json
import random
import subprocess
import sys


class Model:
    def __init__(self):
        self.orders = {}
        self.used = set()
        self.sequence = 0
        self.priority = 0
        self.requests = {}

    def run(self, line):
        t = line.split()
        op, client, req, symbol, oid = t[1:6]
        if op in ("NEW", "REPLACE") and ("0" in t[-2:]):
            return {"ok": False, "error": "BAD_REQUEST"}
        key = (client, req)
        if line in self.requests:
            result = dict(self.requests[line])
            result["duplicate"] = True
            return result
        for previous, remembered in self.requests.items():
            parts = previous.split()
            if (parts[2], parts[3]) == key:
                return dict(
                    sequence=remembered["sequence"],
                    accepted=False,
                    code="REQUEST_CONFLICT",
                    duplicate=False,
                    events=[],
                )
        self.sequence += 1
        result = {
            "sequence": self.sequence,
            "accepted": False,
            "code": "",
            "duplicate": False,
            "events": [],
        }
        self.requests[line] = result
        old = self.orders.get(oid)
        if op != "NEW" and (not old or old["symbol"] != symbol):
            result["code"] = "UNKNOWN_ORDER"
            return result
        if op != "NEW" and old["owner"] != client:
            result["code"] = "NOT_OWNER"
            return result
        new_id = oid if op == "NEW" else (t[6] if op == "REPLACE" else None)
        if new_id is not None and new_id in self.used:
            result["code"] = "DUPLICATE_ORDER_ID"
            return result
        result.update(accepted=True, code="OK")

        def event(kind, order, contra, price, qty):
            result["events"].append(
                dict(
                    type=kind,
                    symbol=symbol,
                    order_id=order,
                    contra_id=contra,
                    price=price,
                    qty=qty,
                )
            )

        if op != "NEW":
            del self.orders[oid]
            event("CANCEL", oid, "", old["price"], old["qty"])
        if op == "CANCEL":
            return result
        if op == "NEW":
            side, tif, price, qty = t[6], t[7], int(t[8]), int(t[9])
        else:
            side, tif, price, qty = old["side"], "GTC", int(t[7]), int(t[8])
        self.priority += 1
        self.used.add(new_id)
        while qty:
            eligible = [
                o
                for o in self.orders.values()
                if o["symbol"] == symbol
                and o["side"] != side
                and (o["price"] <= price if side == "BUY" else o["price"] >= price)
            ]
            if not eligible:
                break
            best = min(
                eligible,
                key=lambda o: (
                    (o["price"] if side == "BUY" else -o["price"]),
                    o["priority"],
                ),
            )
            amount = min(qty, best["qty"])
            qty -= amount
            best["qty"] -= amount
            event("TRADE", new_id, best["id"], best["price"], amount)
            if not best["qty"]:
                del self.orders[best["id"]]
        if qty and tif == "GTC":
            self.orders[new_id] = dict(
                id=new_id,
                owner=client,
                symbol=symbol,
                side=side,
                price=price,
                qty=qty,
                priority=self.priority,
            )
            event("REST", new_id, "", price, qty)
        elif qty:
            event("EXPIRE", new_id, "", price, qty)
        return result

    def state(self):
        return {
            "sequence": self.sequence,
            "orders": sorted(self.orders.values(), key=lambda o: o["id"]),
        }


def generate(seed, count):
    r = random.Random(seed)
    lines = []
    model = Model()
    for i in range(count):
        client = r.choice(["alice", "bob"])
        symbol = r.choice(["ABC", "XYZ"])
        op = r.choices(["NEW", "CANCEL", "REPLACE"], [6, 2, 2])[0]
        oid = "o" + str(r.randrange(max(1, i + 1)))
        if op != "NEW" and model.orders and r.random() < 0.7:
            old = r.choice(list(model.orders.values()))
            oid, client, symbol = old["id"], old["owner"], old["symbol"]
        if op == "NEW":
            if r.random() < 0.8:
                oid = "o" + str(i)
            line = f'NEW {client} r{i} {symbol} {oid} {r.choice(["BUY", "SELL"])} {r.choice(["GTC", "IOC"])} {r.randint(95, 105)} {r.randint(1, 10)}'
        elif op == "CANCEL":
            line = f"CANCEL {client} r{i} {symbol} {oid}"
        else:
            line = f"REPLACE {client} r{i} {symbol} {oid} n{i} {r.randint(95,105)} {r.randint(1,10)}"
        line = "V1 " + line
        if op in ("NEW", "REPLACE") and r.random() < 0.04:
            tokens = line.split()
            tokens[r.choice([-1, -2])] = "0"
            line = " ".join(tokens)
        if lines and r.random() < 0.05:
            line = r.choice(lines)
        elif lines and r.random() < 0.05:
            tokens = r.choice(lines).split()
            tokens[5] = "conflict" + str(i)
            line = " ".join(tokens)
        lines.append(line)
        model.run(line)
    return lines


def compare(binary, lines):
    inputs = [item for line in lines for item in (line, "V1 STATE")]
    model = Model()
    expected = []
    for line in lines:
        expected.append(model.run(line))
        expected.append(json.loads(json.dumps(model.state())))
    p = subprocess.run(
        [binary, "replay"],
        input="\n".join(inputs) + "\n",
        text=True,
        capture_output=True,
        check=True,
    )
    actual = [json.loads(line) for line in p.stdout.splitlines()]
    if actual != expected:
        for i, (a, e) in enumerate(zip(actual, expected)):
            if a != e:
                return i, a, e
        return min(len(actual), len(expected)), actual, expected
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--count", type=int, default=300)
    args = parser.parse_args()
    for seed in range(args.seeds):
        lines = generate(seed, args.count)
        mismatch = compare(args.server, lines)
        if mismatch:
            # Earliest failing prefix gives a small, deterministic reproduction while retaining dependencies.
            lo, hi = 1, len(lines)
            while lo < hi:
                mid = (lo + hi) // 2
                if compare(args.server, lines[:mid]):
                    hi = mid
                else:
                    lo = mid + 1
            with open("differential-repro.txt", "w") as f:
                f.write("\n".join(lines[:lo]) + "\nV1 STATE\n")
            print(
                f"seed {seed}, first failing prefix {lo}; saved differential-repro.txt\nactual/expected: {compare(args.server, lines[:lo])}",
                file=sys.stderr,
            )
            return 1
    print(f"differential: {args.seeds} seeds × {args.count} commands passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
