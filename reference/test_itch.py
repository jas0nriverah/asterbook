import io
import struct
import unittest
from itch import FeedError, Reconstructor, read_frames


def header(kind):
    return kind + struct.pack("!HH", 3, 0) + b"\0" * 6


def add(ref=1, side=b"B", shares=10, kind=b"A"):
    return (
        header(kind)
        + struct.pack("!QcI8sI", ref, side, shares, b"ABC     ", 123400)
        + (b"TEST" if kind == b"F" else b"")
    )


def update(kind, ref, suffix=b""):
    return header(kind) + struct.pack("!Q", ref) + suffix


class ItchTests(unittest.TestCase):
    def test_lifecycle(self):
        book = Reconstructor()
        book.apply(add(kind=b"F"))
        book.apply(update(b"E", 1, struct.pack("!IQ", 2, 9)))
        book.apply(update(b"C", 1, struct.pack("!IQcI", 3, 10, b"N", 130000)))
        book.apply(update(b"X", 1, struct.pack("!I", 1)))
        book.apply(update(b"U", 1, struct.pack("!QII", 2, 8, 120000)))
        self.assertEqual(book.orders[2]["shares"], 8)
        self.assertEqual(book.orders[2]["attribution"], "TEST")
        book.apply(update(b"D", 2))
        self.assertEqual(book.snapshot(), [])
        with self.assertRaises(FeedError):
            book.apply(add())

    def test_invalid_is_atomic(self):
        book = Reconstructor()
        book.apply(add())
        before = book.snapshot()
        for message in [
            update(b"X", 1, struct.pack("!I", 11)),
            update(b"U", 1, struct.pack("!QII", 1, 1, 1)),
            b"Z",
            add()[:-1],
        ]:
            with self.assertRaises(FeedError):
                book.apply(message)
            self.assertEqual(book.snapshot(), before)

    def test_frames(self):
        message = add()
        self.assertEqual(
            list(read_frames(io.BytesIO(struct.pack("!H", len(message)) + message))),
            [message],
        )
        for data in [b"\0", b"\0\x05abc"]:
            with self.assertRaises(FeedError):
                list(read_frames(io.BytesIO(data)))


if __name__ == "__main__":
    unittest.main()
