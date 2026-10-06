"""Every iwa_codec layer round-trips byte-for-byte."""
import os, random, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import iwa_codec as C


class Varints(unittest.TestCase):
    def test_round_trip(self):
        for n in (0, 1, 127, 128, 300, 2**32, 2**63 - 1):
            self.assertEqual(C.read_varint(C.write_varint(n), 0),
                             (n, len(C.write_varint(n))))


class Protobuf(unittest.TestCase):
    def test_tokenize_emit_preserves_order_and_wire_types(self):
        msg = C.emit([(3, 2, b"text"), (1, 0, C.write_varint(300)),
                      (9, 5, b"\x01\x02\x03\x04"), (9, 1, b"\x00" * 8),
                      (3, 2, b"")])
        self.assertEqual(C.emit(C.tokenize(msg)), msg)
        self.assertEqual([t[:2] for t in C.tokenize(msg)],
                         [(3, 2), (1, 0), (9, 5), (9, 1), (3, 2)])


class Snappy(unittest.TestCase):
    def check(self, data):
        self.assertEqual(C.snappy_decompress(C.snappy_compress(data)), data)

    def test_empty_and_tiny(self):
        for data in (b"", b"a", b"abc", b"abcd"):
            self.check(data)

    def test_repetitive_text_compresses(self):
        data = b"the quick brown fox " * 2000
        packed = C.snappy_compress(data[:0x10000])
        self.assertLess(len(packed), len(data[:0x10000]) // 4)
        self.check(data[:0x10000])

    def test_random_bytes_and_long_literals(self):
        rng = random.Random(1)
        self.check(bytes(rng.randrange(256) for _ in range(0x10000)))

    def test_overlapping_copies(self):
        self.check(b"ab" * 5000 + b"x" * 300)


class Container(unittest.TestCase):
    def test_iwa_round_trip_across_chunks(self):
        rng = random.Random(2)
        words = [b"alpha ", b"beta ", b"gamma ", b"\x00\x01", b"delta\n"]
        payload = b"".join(rng.choice(words) for _ in range(40000))
        self.assertGreater(len(payload), 0x10000 * 2)
        self.assertEqual(C.iwa_decode(C.iwa_encode(payload)), payload)

    def test_archives_pack_round_trip_and_rewrite_lengths(self):
        def archive(ident, mtype, body):
            info = C.emit([(1, 0, C.write_varint(ident)),
                           (2, 2, C.emit([(1, 0, C.write_varint(mtype)),
                                          (3, 0, C.write_varint(len(body)))]))])
            return C.write_varint(len(info)) + info + body
        payload = archive(1, 2001, b"x" * 10) + archive(2, 2022, b"yy")
        arcs = C.archives(payload)
        self.assertEqual(C.pack_archives(arcs), payload)
        arcs[0][1][0][1] = b"z" * 200            # grow a message past 127
        again = C.archives(C.pack_archives(arcs))
        self.assertEqual(again[0][1][0], [2001, b"z" * 200])
        self.assertEqual(again[1][1][0], [2022, b"yy"])


if __name__ == "__main__":
    unittest.main()
