"""The Pages round-trip harness (tests/pages_roundtrip.py), with a stand-in for Pages.

The real thing needs macOS and Pages. Here a fake `osascript` copies the input to the
output, which is what a Pages that rewrites a file without changing its content would
do. The harness must pass that and must catch anything less.
"""
import os, shutil, subprocess, sys, tempfile, unittest, zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import pages_roundtrip as R
import pages2md as P
import pages_edit as E

SAMPLES = os.path.join(HERE, "samples")
HAVE = all(os.path.exists(os.path.join(SAMPLES, f"{n}.pages"))
           for n in ("formatting", "kitchen-sink", "sample-content", "emoji"))


@unittest.skipUnless(HAVE, "sample documents not present")
class Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = os.path.join(cls.tmp.name, "pack")
        cls.names = R.prepare(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def edited(self, name):
        return os.path.join(self.out, "edited", name + ".pages")

    def original(self, name):
        return os.path.join(self.out, "input", name + ".pages")

    def fake_pages(self, how="copy"):
        """Stand in for Pages: write `saved/` from `edited/` (or `input/`)."""
        saved = os.path.join(self.out, "saved")
        shutil.rmtree(saved, ignore_errors=True)
        os.makedirs(saved)
        if os.path.exists(os.path.join(self.out, "run.json")):
            os.remove(os.path.join(self.out, "run.json"))   # no stale Pages log
        for name in self.names:
            src = (self.edited if how == "copy" else self.original)(name)
            shutil.copy(src, os.path.join(saved, name + ".pages"))

    def test_every_case_is_prepared(self):
        self.assertGreaterEqual(len(self.names), 15)
        self.assertIn("control", self.names)
        for name in self.names:
            self.assertTrue(os.path.exists(self.edited(name)), name)
            self.assertTrue(os.path.exists(self.original(name)), name)

    def test_each_edit_changes_the_document_and_the_control_does_not(self):
        for name in self.names:
            with self.subTest(case=name):
                same = P.text_fingerprint(self.edited(name)) == P.text_fingerprint(
                    self.original(name))
                snap_same = (R.snapshot(self.edited(name))
                             == R.snapshot(self.original(name)))
                if name.startswith("control"):
                    self.assertTrue(snap_same)
                else:
                    self.assertFalse(snap_same, f"{name} did not change anything")
                    self.assertIsInstance(same, bool)

    def test_every_prepared_file_has_well_formed_tables(self):
        for name in self.names:
            with self.subTest(case=name):
                self.assertEqual(E.table_problems(E.Document(self.edited(name))), [])

    def test_an_ill_formed_write_is_a_failure_even_if_it_renders_the_same(self):
        with tempfile.TemporaryDirectory() as d:          # its own pack: this one is damaged
            pack = os.path.join(d, "p")
            R.prepare(pack, only=["control", "replace-plain"])
            shutil.copytree(os.path.join(pack, "edited"), os.path.join(pack, "saved"),
                            dirs_exist_ok=True)
            edited = os.path.join(pack, "edited", "replace-plain.pages")
            doc = E.Document(edited)
            doc.select("body")
            doc._put_field(E.F_CHAR_TBL, E.emit([(1, 2, E.entry_bytes(3, None)),
                                                 (1, 2, E.entry_bytes(3, None))]))
            payload = E.pack_archives(doc.arcs)     # write the bad bytes the way a bug would,
            with zipfile.ZipFile(edited, "w") as z:  # past save()'s own refusal
                for n in doc.names:
                    z.writestr(n, E.iwa_encode(payload) if n == E.BODY_ENTRY else doc.entries[n])
            results = {r.name: r for r in R.check(pack)}
        self.assertFalse(results["replace-plain"].ok)
        self.assertIn("ill-formed tables", " ".join(results["replace-plain"].problems))
        self.assertTrue(results["control"].ok)

    def test_edited_files_read_in_both_tools(self):
        for name in self.names:
            with self.subTest(case=name):
                E.Document(self.edited(name))
                P.PagesDoc(self.edited(name)).all_paragraphs()

    def test_an_identity_pages_passes_every_case(self):
        self.fake_pages()
        results = R.check(self.out)
        self.assertEqual({r.name: r.ok for r in results},
                         {name: True for name in self.names})

    def test_a_pages_that_loses_the_edit_is_caught(self):
        self.fake_pages(how="input")                    # saved == untouched original
        results = {r.name: r for r in R.check(self.out)}
        self.assertTrue(all(r.ok for n, r in results.items() if n.startswith("control")))
        failed = [n for n, r in results.items() if not r.ok]
        self.assertEqual(sorted(failed),
                         sorted(n for n in self.names if not n.startswith("control")))
        self.assertIn("markdown", " ".join(results[failed[0]].problems))

    def test_a_missing_saved_file_is_a_failure_not_a_pass(self):
        self.fake_pages()
        os.remove(os.path.join(self.out, "saved", self.names[-1] + ".pages"))
        results = {r.name: r for r in R.check(self.out)}
        self.assertFalse(results[self.names[-1]].ok)
        self.assertIn("not saved", " ".join(results[self.names[-1]].problems))

    def test_lost_heading_styles_are_caught(self):
        # what the documented batch-deletion corruption looks like: the headings
        # of the whole document lose their style
        self.fake_pages()
        name = [n for n in self.names if n.startswith("delete-12")][0]
        saved = os.path.join(self.out, "saved", name + ".pages")
        doc = E.Document(saved)
        doc.select("body")
        raw = doc.text()[0]
        doc.retag(raw.index("Headline"), E.style_ids(doc.reader)["Body 1"][0])
        doc.save(saved)
        results = {r.name: r for r in R.check(self.out)}
        self.assertFalse(results[name].ok)
        self.assertTrue(any("heading" in p or "style" in p or "markdown" in p
                            for p in results[name].problems))

    def test_report_is_written(self):
        self.fake_pages()
        R.write_report(self.out, R.check(self.out))
        text = open(os.path.join(self.out, "report.md"), encoding="utf-8").read()
        self.assertIn("| control | pass |", text)

    def test_run_drives_osascript_once_per_case_with_paths_as_arguments(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            self.assertEqual(cmd[0], "osascript")
            src, dst = cmd[-2:]
            shutil.copy(src, dst)
            return subprocess.CompletedProcess(cmd, 0, stdout="3 1500\n", stderr="")

        shutil.rmtree(os.path.join(self.out, "saved"), ignore_errors=True)
        with mock.patch("subprocess.run", side_effect=fake_run), \
                mock.patch("time.sleep"):
            R.run(self.out, timeout=5, pause=0)
        self.assertEqual(len(calls), len(self.names))
        # the document paths are arguments, never spliced into the script text
        self.assertNotIn(self.out, calls[0][2])
        results = R.check(self.out)
        self.assertEqual([r.name for r in results if not r.ok], [])
        counts = R.load_counts(self.out)
        self.assertEqual(counts["control"], {"pages": 3, "chars": 1500})

    def test_a_timeout_is_recorded_and_the_rest_continue(self):
        def fake_run(cmd, **kw):
            src, dst = cmd[-2:]
            if os.path.basename(src) == "replace-plain.pages":
                raise subprocess.TimeoutExpired(cmd, 5)
            shutil.copy(src, dst)
            return subprocess.CompletedProcess(cmd, 0, stdout="2 10\n", stderr="")

        shutil.rmtree(os.path.join(self.out, "saved"), ignore_errors=True)
        with mock.patch("subprocess.run", side_effect=fake_run), \
                mock.patch("time.sleep"):
            R.run(self.out, timeout=5, pause=0)
        results = {r.name: r for r in R.check(self.out)}
        self.assertFalse(results["replace-plain"].ok)
        self.assertIn("timed out", " ".join(results["replace-plain"].problems))
        self.assertTrue(results["control"].ok)

    def test_a_page_count_that_grows_after_deleting_is_flagged(self):
        self.fake_pages()
        counts = {n: {"pages": 4, "chars": 1000} for n in self.names}
        counts["delete-12-formatting"] = {"pages": 9, "chars": 1000}
        R.save_counts(self.out, counts)
        results = {r.name: r for r in R.check(self.out)}
        self.assertFalse(results["delete-12-formatting"].ok)
        self.assertIn("pages", " ".join(results["delete-12-formatting"].problems))

    def test_cases_can_be_selected(self):
        with tempfile.TemporaryDirectory() as d:
            names = R.prepare(os.path.join(d, "p"), only=["control", "emoji-edit"])
            self.assertEqual(names, ["control", "emoji-edit"])


if __name__ == "__main__":
    unittest.main()
