"""`shift_table` has a byte-level fast path for plain index tables. It must give the same
bytes as the general tokenizer path (`_shift_table_slow`) for every table and every edit."""
import os, random, sys, unittest, unittest.mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fixture import table
import pages_edit as E
from iwa_codec import tokenize, emit, write_varint

SAMPLES = os.path.join(HERE, "samples")


def edits(rng, size, n):
    for _ in range(n):
        a = rng.randint(0, size)
        b = min(size, a + rng.choice([0, 0, 1, 3, 20, 300]))
        yield a, b, rng.randint(-(b - a), 40)


class SameBytesAsTheGeneralPath(unittest.TestCase):
    def check(self, val, start, end, delta):
        self.assertEqual(E.shift_table(val, start, end, delta),
                         E._shift_table_slow(val, start, end, delta),
                         (start, end, delta))

    def test_synthetic_tables(self):
        rng = random.Random(7)
        for trial in range(60):
            idx = sorted(rng.sample(range(0, 400), rng.randint(1, 40)))
            rows = [(i, rng.choice([None, 1, 70000, 123456789])) for i in idx]
            val = table(rows)
            for start, end, delta in edits(rng, 420, 40):
                with self.subTest(trial=trial):
                    self.check(val, start, end, delta)

    def test_edits_that_collapse_entries_onto_one_index(self):
        val = table([(0, None), (5, 900), (9, None), (20, 901)])
        for start, end, delta in [(5, 9, -4), (4, 10, -6), (0, 20, -20), (5, 9, -3), (9, 9, 0)]:
            self.check(val, start, end, delta)

    def test_a_table_that_already_has_duplicates_or_is_unsorted(self):
        for rows in ([(3, None), (3, 900), (8, None)], [(8, None), (2, 900), (5, None)]):
            val = table(rows)
            for start, end, delta in [(0, 0, 4), (2, 5, -3), (9, 9, 1), (3, 3, 0)]:
                self.check(val, start, end, delta)

    @unittest.mock.patch("sys.stderr", new_callable=__import__("io").StringIO)   # the general path warns
    def test_not_a_plain_index_table_uses_the_general_path(self, _err):
        range_entry = emit([(1, 2, emit([(1, 0, write_varint(4)), (2, 0, write_varint(6))])),
                            (2, 2, b"\x0a\x01\x05")])
        for val in (b"", b"\x0a\x00", b"\x0a\x05\x08", emit([(1, 2, range_entry)]),
                    emit([(2, 2, b"x")]), b"\xff\xff"):
            if val != emit([(1, 2, range_entry)]):
                self.assertIsNone(E._shift_index_fast(val, 3, 5, 2))
            try:
                slow = E._shift_table_slow(val, 3, 5, 2)
            except Exception as exc:       # the general path may reject junk; so must we
                with self.assertRaises(type(exc)):
                    E.shift_table(val, 3, 5, 2)
            else:
                self.assertEqual(E.shift_table(val, 3, 5, 2), slow)

    def test_multibyte_indexes_and_lengths(self):
        val = table([(i * 130, 300000 + i) for i in range(200)])       # varints of 1-3 bytes
        for start, end, delta in [(0, 0, 200), (130, 260, -100), (26000, 26000, 130), (50, 17000, -16000)]:
            self.check(val, start, end, delta)

    def test_every_table_of_every_sample(self):
        rng = random.Random(3)
        n = 0
        for name in sorted(os.listdir(SAMPLES)):
            if not name.endswith(".pages"):
                continue
            doc = E.Document(os.path.join(SAMPLES, name))
            for handle in doc.slots:
                doc.select(handle)
                size = len(doc.text()[0])
                for num, wire, val in tokenize(doc._msg):
                    if wire != 2 or num == E.F_TEXT:
                        continue
                    for start, end, delta in edits(rng, size, 6):
                        self.check(val, start, end, delta)
                        n += 1
        self.assertGreater(n, 100)


class ScanIndexEntries(unittest.TestCase):
    def test_reads_what_the_tokenizer_reads(self):
        val = table([(0, None), (300, 900), (70000, None)])
        self.assertEqual([e[0] for e in E.scan_index_entries(val)], [0, 300, 70000])

    def test_table_kind_agrees(self):
        val = table([(0, None), (4, 900)])
        self.assertEqual(E.table_kind(val), "index")


def random_edits(rng, raw, n):
    """n non-overlapping edits: replacements, deletions, insertions (some at one point)."""
    cuts = sorted(rng.sample(range(len(raw) + 1), min(2 * n, len(raw) + 1)))
    out = []
    for a, b in zip(cuts[::2], cuts[1::2]):
        kind = rng.choice(["replace", "replace", "delete", "insert"])
        if kind == "insert":
            b = a
        elif b - a > 15:
            b = a + rng.randint(1, 15)
        new = "" if kind == "delete" else rng.choice(["X", "longer text", "é", "ab", "x" * 30])
        out.append((a, b, new))
    if out and rng.random() < 0.3:                      # two insertions where an edit starts
        a = out[0][0]
        out += [(a, a, "P"), (a, a, "Q")]
    return out


class ApplyInOnePass(unittest.TestCase):
    """`Document.apply` (one pass per table) against `_apply_sequential` (the original)."""

    def pair(self, name):
        path = os.path.join(SAMPLES, name)
        return E.Document(path), E.Document(path)

    def test_random_edits_on_every_sample_give_the_same_document(self):
        runs = 0
        for name in sorted(os.listdir(SAMPLES)):
            if not name.endswith(".pages"):
                continue
            for seed in range(12):
                rng = random.Random(seed)
                fast, slow = self.pair(name)
                handle = rng.choice(list(fast.slots))
                fast.select(handle)
                slow.select(handle)
                raw = fast.text()[0]
                if len(raw) < 5:
                    continue
                edits = random_edits(rng, raw, rng.randint(2, 25))
                # an edit may not split a surrogate pair or hit a break character
                edits = [e for e in edits if not E.splits_pair(raw, e[0]) and not E.splits_pair(raw, e[1])]
                with self.subTest(sample=name, seed=seed, handle=handle):
                    report_fast = fast.apply(edits)
                    report_slow = slow._apply_sequential(edits)
                    self.assertEqual(fast._msg, slow._msg)
                    self.assertEqual(fast.text()[0], slow.text()[0])
                    self.assertEqual(report_fast, report_slow)
                    runs += 1
        self.assertGreater(runs, 30)

    def test_the_one_pass_path_is_really_used(self):
        fast, _slow = self.pair("formatting.pages")
        raw = fast.select("body").text()[0]
        edits = [(10, 12, "zz"), (50, 52, "y"), (90, 90, "inserted")]
        real = E.shift_table_batch
        results = []

        def spy(val, eds):
            results.append(real(val, eds))
            return results[-1]

        with unittest.mock.patch.object(E, "shift_table_batch", spy):
            fast.apply(edits)
        self.assertTrue(results)
        self.assertTrue(any(r is not None for r in results))

    def test_overlapping_edits_take_the_one_at_a_time_path(self):
        fast, slow = self.pair("formatting.pages")
        edits = [(10, 30, "A"), (20, 40, "B")]
        self.assertEqual(fast.apply(edits), slow._apply_sequential(edits))
        self.assertEqual(fast._msg, slow._msg)

    def test_a_single_edit_and_no_edits(self):
        fast, slow = self.pair("formatting.pages")
        self.assertEqual(fast.apply([(10, 12, "zz")]), slow._apply_sequential([(10, 12, "zz")]))
        self.assertEqual(fast.apply([]), [])
        self.assertEqual(fast._msg, slow._msg)

    def test_edits_that_collapse_runs_still_match(self):
        fast, slow = self.pair("emoji.pages")
        raw = fast.select("body").text()[0]
        slow.select("body")
        a = raw.index("Bold")
        edits = [(a, a + 4, ""), (a + 6, a + 8, "Z")]
        self.assertEqual(fast.apply(edits), slow._apply_sequential(edits))
        self.assertEqual(fast._msg, slow._msg)


class MediaIsReadOnlyWhenSaving(unittest.TestCase):
    def setUp(self):
        import tempfile, zipfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "m.pages")
        self.blob = os.urandom(300_000)
        with zipfile.ZipFile(os.path.join(SAMPLES, "formatting.pages")) as zi, \
                zipfile.ZipFile(self.path, "w") as zo:
            for n in zi.namelist():
                zo.writestr(n, zi.read(n))
            zo.writestr("Data/photo-1.jpg", self.blob)

    def test_only_the_iwa_files_are_read_on_load(self):
        doc = E.Document(self.path)
        loaded = set(dict.keys(doc.entries))
        self.assertNotIn("Data/photo-1.jpg", loaded)
        self.assertTrue(all(n.endswith(".iwa") for n in loaded))

    def test_a_saved_copy_still_has_every_file_byte_for_byte(self):
        import zipfile
        doc = E.Document(self.path)
        doc.apply([(5, 6, "x")])
        out = os.path.join(self.tmp.name, "out.pages")
        doc.save(out)
        with zipfile.ZipFile(self.path) as a, zipfile.ZipFile(out) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for n in a.namelist():
                if n != E.BODY_ENTRY:
                    self.assertEqual(a.read(n), b.read(n), n)
        self.assertEqual(zipfile.ZipFile(out).read("Data/photo-1.jpg"), self.blob)

    def test_a_file_that_changed_since_loading_is_not_mixed_in(self):
        doc = E.Document(self.path)
        with open(self.path, "ab") as fh:
            fh.write(b"\0")                      # any change to the package on disk
        with self.assertRaises(SystemExit) as cm:
            doc.save(os.path.join(self.tmp.name, "out.pages"))
        self.assertIn("changed on disk", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "out.pages.tmp")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "out.pages")))

    def test_an_unknown_name_is_a_key_error(self):
        with self.assertRaises(KeyError):
            E.Document(self.path).entries["nope"]


if __name__ == "__main__":
    unittest.main()
