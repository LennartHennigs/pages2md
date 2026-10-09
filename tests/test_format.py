"""`format` (review finding 1.5): bold/italic/plain over a range of text."""
import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture import (write_pages, table, rows, tables_of, char_style,
                     T_CHAR_STYLE, F_PARA_TBL, F_CHAR_TBL)
import pages_edit as E
import pages2md

BOLD, ITALIC, BOLD_ITALIC, FANCY = 900, 901, 902, 903
STYLES = [(BOLD, T_CHAR_STYLE, char_style(bold=True)),
          (ITALIC, T_CHAR_STYLE, char_style(italic=True)),
          (BOLD_ITALIC, T_CHAR_STYLE, char_style(bold=True, italic=True))]
TEXT = "0123456789abcdefghij\n"


class FormatRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doc(self, char=None, styles=STYLES):
        fields = {F_PARA_TBL: table([(0, 1)])}
        if char is not None:
            fields[F_CHAR_TBL] = table(char)
        path = write_pages(os.path.join(self.tmp.name, "t.pages"), TEXT,
                           fields, extra=styles)
        return E.Document(path)

    def values(self, doc, lo=0, hi=len(TEXT)):
        val = tables_of(doc).get(F_CHAR_TBL)
        return [E.char_value_at(val, i) for i in range(lo, hi)]

    def test_bold_on_plain_text(self):
        doc = self.doc([(0, None)])
        doc.format_run(4, 8, bold=True)
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (4, BOLD), (8, None)])

    def test_bold_overrides_runs_inside_the_range(self):
        # italic 5-7 sits inside the range; it used to win for 5..7 again
        doc = self.doc([(0, None), (5, ITALIC), (7, None)])
        doc.format_run(2, 10, bold=True)
        self.assertEqual(self.values(doc, 2, 10), [BOLD] * 8)
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (2, BOLD), (10, None)])

    def test_run_crossing_the_end_resumes_after_it(self):
        doc = self.doc([(0, None), (5, ITALIC), (15, None)])
        doc.format_run(2, 10, bold=True)
        self.assertEqual(self.values(doc, 0, 16),
                         [None] * 2 + [BOLD] * 8 + [ITALIC] * 5 + [None])

    def test_run_crossing_the_start_is_cut_there(self):
        doc = self.doc([(0, ITALIC), (6, None)])
        doc.format_run(3, 10, bold=True)
        self.assertEqual(self.values(doc, 0, 11),
                         [ITALIC] * 3 + [BOLD] * 7 + [None])

    def test_bold_italic(self):
        doc = self.doc([(0, None)])
        doc.format_run(1, 3, bold=True, italic=True)
        self.assertEqual(self.values(doc, 0, 4), [None, BOLD_ITALIC, BOLD_ITALIC, None])

    def test_plain_removes_emphasis(self):
        # no (False, False) character style exists, and none is needed
        doc = self.doc([(0, None), (2, BOLD), (12, None)])
        doc.format_run(4, 8)
        self.assertEqual(self.values(doc, 0, 13),
                         [None] * 2 + [BOLD] * 2 + [None] * 4 + [BOLD] * 4 + [None])

    def test_plain_over_a_whole_run_leaves_no_override(self):
        doc = self.doc([(0, None), (2, BOLD), (6, None)])
        doc.format_run(2, 6)
        self.assertEqual(self.values(doc), [None] * len(TEXT))

    def test_range_to_the_end_writes_nothing_past_it(self):
        doc = self.doc([(0, None)])
        doc.format_run(15, len(TEXT), bold=True)
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (15, BOLD)])

    def test_storage_without_a_char_table_gets_one(self):
        doc = self.doc(char=None)
        self.assertEqual(doc.format_run(4, 8, italic=True), 1)
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (4, ITALIC), (8, None)])
        # and the result survives a save and a fresh read
        doc.save(doc.path)
        self.assertEqual(rows(E.Document(doc.path), F_CHAR_TBL),
                         [(0, None), (4, ITALIC), (8, None)])

    def test_reader_sees_the_new_run(self):
        doc = self.doc([(0, None), (5, ITALIC), (7, None)])
        doc.format_run(2, 10, bold=True)
        doc.save(doc.path)
        para = pages2md.PagesDoc(doc.path).paragraphs()[0]
        self.assertEqual([r[:3] for r in para["runs"]], [(2, 10, True)])

    def test_missing_style_is_refused_clearly(self):
        doc = self.doc([(0, None)], styles=STYLES[1:])        # no plain bold
        with self.assertRaises(SystemExit) as cm:
            doc.format_run(1, 3, bold=True)
        self.assertIn("no plain bold character style", str(cm.exception))

    def test_styles_carrying_more_than_weight_are_not_reused(self):
        # bold *and* a font: reusing it would drag the font along too
        fancy = E.emit([(11, 2, E.emit([(1, 0, b"\x01"),
                                        (5, 2, b"Helvetica")]))])
        doc = self.doc([(0, None)], styles=[(FANCY, T_CHAR_STYLE, fancy)])
        with self.assertRaises(SystemExit):
            doc.format_run(1, 3, bold=True)

    def test_empty_range_is_a_no_op(self):
        doc = self.doc([(0, None)])
        before = doc._msg
        self.assertEqual(doc.format_run(5, 5, bold=True), 0)
        self.assertEqual(doc._msg, before)

    def test_does_not_reparse_the_document_per_run(self):
        doc = self.doc([(0, None)])
        with mock.patch.object(pages2md.PagesDoc, "__init__",
                               side_effect=AssertionError("re-parsed")):
            for a in range(0, 10, 2):
                doc.format_run(a, a + 1, bold=True)
        self.assertEqual(self.values(doc, 0, 10), [BOLD, None] * 5)


class FormatCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_pages(os.path.join(self.tmp.name, "t.pages"), TEXT,
                                {F_PARA_TBL: table([(0, 1)]),
                                 F_CHAR_TBL: table([(0, None)])}, extra=STYLES)

    def run_cli(self, *args):
        with mock.patch.object(E, "pages_has_open", return_value=False), \
                mock.patch("sys.stdout"):
            E.main(["format", *args, self.path])

    def test_plain_with_bold_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("--on", "2345", "--bold", "--plain")
        self.assertIn("--plain", str(cm.exception))

    def test_write_then_plain_round_trip(self):
        self.run_cli("--on", "2345", "--bold", "--write", "--no-backup")
        self.assertEqual(rows(E.Document(self.path), F_CHAR_TBL),
                         [(0, None), (2, BOLD), (6, None)])
        self.run_cli("--on", "2345", "--plain", "--write", "--no-backup")
        doc = E.Document(self.path)
        val = tables_of(doc)[F_CHAR_TBL]
        self.assertEqual([E.char_value_at(val, i) for i in range(8)], [None] * 8)


if __name__ == "__main__":
    unittest.main()
