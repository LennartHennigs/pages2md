"""Write safety: what the save check catches, a package changed on disk, malformed table
entries, and tracked edits inside someone else's pending change. Found by code review."""
import io, os, shutil, sys, tempfile, unittest, zipfile
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fixture import write_pages, table, rows, F_PARA_TBL, F_INSERTIONS, F_DELETIONS
import pages_edit as E

SAMPLE = os.path.join(HERE, "samples", "formatting.pages")


def cli(*args):
    out = io.StringIO()
    try:
        with mock.patch.object(E, "pages_has_open", return_value=False), redirect_stdout(out):
            E.main(list(args))
    except SystemExit as exc:
        return str(exc.code), out.getvalue()
    return None, out.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "d.pages")
        shutil.copy(SAMPLE, self.path)

    def read(self):
        with open(self.path, "rb") as fh:
            return fh.read()


class UnreadableOutputIsRefused(Case):
    """Reading back undecodable IWA exits through package_errors (SystemExit), which the
    save check's `except Exception` let through: no refusal, and the .tmp stayed."""

    def test_save_refuses_and_cleans_up(self):
        before = self.read()
        doc = E.Document(self.path)
        with mock.patch.object(E, "iwa_encode", return_value=b"\x00\x05\x00junk"):
            with self.assertRaises(SystemExit) as cm:
                doc.save(self.path)
        self.assertIn("refusing to write", str(cm.exception))
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertEqual(self.read(), before)

    def test_verify_reports_instead_of_exiting(self):
        bad = os.path.join(self.tmp.name, "bad.pages")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("Index/Document.iwa", b"\x00\x05\x00junk")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(E.verify(bad, []), "?")
        self.assertIn("VERIFY FAILED", out.getvalue())


class ChangedOnDisk(Case):
    def change_on_disk(self):
        with open(self.path, "ab") as fh:
            fh.write(b"\0")

    def test_a_package_with_only_iwa_files_is_checked_too(self):
        iwa_only = os.path.join(self.tmp.name, "i.pages")
        with zipfile.ZipFile(SAMPLE) as zi, zipfile.ZipFile(iwa_only, "w") as zo:
            for n in zi.namelist():
                if n.endswith(".iwa"):
                    zo.writestr(n, zi.read(n))
        doc = E.Document(iwa_only)
        with open(iwa_only, "ab") as fh:
            fh.write(b"\0")
        with self.assertRaises(SystemExit) as cm:
            doc.save(iwa_only)
        self.assertIn("changed on disk", str(cm.exception))

    def test_nothing_is_snapshotted_or_backed_up_first(self):
        cli("config", "--vcs", "on", self.path)
        root = E.vcs_root(self.path)
        commits = E.git(root, "rev-list", "--count", "HEAD").stdout
        doc = E.Document(self.path)
        doc.apply([(0, 5, "Title")])
        self.change_on_disk()
        args = mock.Mock(write=True, file=self.path, no_backup=False)
        with mock.patch.object(E, "pages_has_open", return_value=False), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            E.commit(doc, args, "test")
        self.assertIn("changed on disk", str(cm.exception))
        self.assertEqual(E.git(root, "rev-list", "--count", "HEAD").stdout, commits)

    def test_saving_twice_from_one_document_is_fine(self):
        doc = E.Document(self.path)
        doc.apply([(0, 5, "Title")])
        doc.save(self.path)
        doc.apply([(0, 5, "Title")])
        doc.save(self.path)


class MalformedEntries(unittest.TestCase):
    def test_an_index_that_runs_past_its_entry_is_not_scanned(self):
        good = table([(4, None)])
        bad = b"\x0a\x01\x08" + good             # an entry holding only the 08 tag
        self.assertIsNone(E.scan_index_entries(bad))

    def test_the_general_path_handles_it(self):
        bad = b"\x0a\x01\x08" + table([(4, None)])
        with mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(E.shift_table(bad, 0, 0, 3), E._shift_table_slow(bad, 0, 0, 3))


class TrackedEditInsideAPendingChange(unittest.TestCase):
    TEXT = "0123456789abcdefghijklmnopqrstuvwxyz\n"

    def doc(self, field):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        return E.Document(write_pages(os.path.join(tmp, "x.pages"), self.TEXT,
                                      {F_PARA_TBL: table([(0, 1)]),
                                       field: table([(0, None), (10, 700), (30, None)])}))

    def test_inside_an_insertion_is_refused(self):
        doc = self.doc(F_INSERTIONS)
        with self.assertRaises(SystemExit) as cm:
            doc.apply_tracked([(15, 18, "XY")])
        self.assertIn("overlaps an existing tracked change", str(cm.exception))
        self.assertEqual(rows(doc, F_INSERTIONS), [(0, None), (10, 700), (30, None)])

    def test_a_pure_insertion_inside_one_is_refused(self):
        doc = self.doc(F_INSERTIONS)
        with self.assertRaises(SystemExit):
            doc.apply_tracked([(20, 20, "XY")])

    def test_inside_a_deletion_is_still_refused(self):
        with self.assertRaises(SystemExit):
            self.doc(F_DELETIONS).apply_tracked([(15, 18, "XY")])

    def test_next_to_one_is_fine(self):
        for at in ((5, 8), (30, 33), (10, 10), (30, 30)):
            with self.subTest(at=at):
                self.doc(F_INSERTIONS).apply_tracked([(at[0], at[1], "XY")])


if __name__ == "__main__":
    unittest.main()
