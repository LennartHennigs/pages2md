"""Deleting the last paragraph, or one that a break character ends.

The paragraph's own terminator is missing there (nothing follows, or a page break / anchor
ends it), so deleting just its text left the previous paragraph's "\\n" in front of nothing:
an empty paragraph appeared. The previous "\\n" goes too.
"""
import os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fixture import write_pages, table, rows, F_PARA_TBL
import pages_edit as E
import pages2md as P

S1, S2, S3 = 1, 2, 3


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doc(self, text, styles):
        path = write_pages(os.path.join(self.tmp.name, "t.pages"), text, {F_PARA_TBL: table(styles)})
        return E.Document(path)

    def texts(self, doc):
        doc.save(doc.path)
        return [p["raw"] for p in P.PagesDoc(doc.path).all_paragraphs()]


class LastParagraph(Case):
    def test_without_a_final_newline(self):
        doc = self.doc("A\nB\nC", [(0, S1), (2, S2), (4, S3)])
        doc.delete_paragraph(4)
        self.assertEqual(doc.text()[0], "A\nB")
        self.assertEqual(rows(doc, F_PARA_TBL), [(0, S1), (2, S2)])
        self.assertEqual(self.texts(doc), ["A", "B"])

    def test_after_an_empty_paragraph_that_keeps_its_own_style(self):
        doc = self.doc("A\n\nB", [(0, S1), (2, S2), (3, S3)])
        doc.delete_paragraph(3)
        self.assertEqual(doc.text()[0], "A\n")
        self.assertEqual(rows(doc, F_PARA_TBL), [(0, S1), (2, S2)])
        self.assertEqual(self.texts(doc), ["A", ""])

    def test_with_a_final_newline_nothing_changes(self):
        doc = self.doc("A\nB\nC\n", [(0, S1), (2, S2), (4, S3)])
        doc.delete_paragraph(4)
        self.assertEqual(doc.text()[0], "A\nB\n")

    def test_the_only_paragraph(self):
        doc = self.doc("A", [(0, S1)])
        doc.delete_paragraph(0)
        self.assertEqual(doc.text()[0], "")

    def test_a_middle_paragraph_is_unchanged(self):
        doc = self.doc("A\nB\nC", [(0, S1), (2, S2), (4, S3)])
        doc.delete_paragraph(2)
        self.assertEqual(doc.text()[0], "A\nC")
        self.assertEqual(rows(doc, F_PARA_TBL), [(0, S1), (2, S3)])


class EndedByABreakCharacter(Case):
    def test_each_break_character(self):
        for char in ("\x04", "\x05"):
            with self.subTest(char=repr(char)):
                doc = self.doc(f"A\nB{char}C\n", [(0, S1), (2, S2), (4, S3)])
                doc.delete_paragraph(2)
                self.assertEqual(doc.text()[0], f"A{char}C\n")
                self.assertEqual(rows(doc, F_PARA_TBL)[0], (0, S1))
                self.assertEqual(rows(doc, F_PARA_TBL)[-1][1], S3)
                self.assertEqual(self.texts(doc)[:2], ["A", "C"])

    def test_the_first_paragraph_has_no_newline_to_take(self):
        doc = self.doc("A\x05B\n", [(0, S1), (2, S2)])
        doc.delete_paragraph(0)
        self.assertEqual(doc.text()[0], "\x05B\n")


class ParagraphWithAFootnoteReference(Case):
    """Removing the reference would leave the note's storage behind, unreferenced."""

    def footnoted(self):
        from fixture import F_ATTACHMENTS
        path = write_pages(os.path.join(self.tmp.name, "f.pages"), "A\nSee\x0e this\nC\n",
                           {F_PARA_TBL: table([(0, S1), (2, S2), (13, S1)]),
                            F_ATTACHMENTS: table([(5, 999)])})
        return path

    def test_delete_paragraph_is_refused_and_says_why(self):
        doc = E.Document(self.footnoted())
        with self.assertRaises(SystemExit) as cm:
            doc.delete_paragraph(3)
        self.assertIn("footnote reference", str(cm.exception))
        self.assertEqual(doc.text()[0], "A\nSee\x0e this\nC\n")

    def test_the_command_refuses_in_a_dry_run_too(self):
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        path = self.footnoted()
        with mock.patch.object(E, "pages_has_open", return_value=False), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
            E.main(["delete-paragraph", "--on", "See", path])
        self.assertIn("footnote reference", str(cm.exception.code))

    def test_neighbouring_paragraphs_are_fine(self):
        doc = E.Document(self.footnoted())
        doc.delete_paragraph(0)
        self.assertEqual(doc.text()[0], "See\x0e this\nC\n")


if __name__ == "__main__":
    unittest.main()
