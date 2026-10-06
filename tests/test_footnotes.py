"""A footnote reference (\\x0e with an attachment-table entry) is inline.

The reader and editor used to treat every \\x0e as a section break, so each
footnote cut its paragraph in two: the sentence broke around the note, and
the second half lost its paragraph style. Found in a real document (Pages
14.5) and in tests/samples/kitchen-sink.pages.
"""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture import (write_pages, table, rows, char_style, T_CHAR_STYLE,
                     F_PARA_TBL, F_CHAR_TBL, F_ATTACHMENTS)
import pages_edit as E
import pages2md as P

REF = 999
#        0         1         2         3
#        0123456789012345678901234567890123456
TEXT = "A wicked problem\x0e is ill-defined.\nSecond paragraph.\nThird.\n"
MARK = TEXT.index("\x0e")
SECOND = TEXT.index("Second")
THIRD = TEXT.index("Third")


class FootnoteIsInline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_pages(
            os.path.join(self.tmp.name, "t.pages"), TEXT,
            {F_PARA_TBL: table([(0, 1), (SECOND, 2), (THIRD, 1)]),
             F_ATTACHMENTS: table([(MARK, REF)])})

    def test_reader_keeps_the_sentence_in_one_paragraph(self):
        paras = P.PagesDoc(self.path).paragraphs()
        self.assertEqual([p["offset"] for p in paras], [0, SECOND, THIRD, len(TEXT)])
        self.assertEqual(paras[0]["raw"], TEXT[:SECOND - 1])
        self.assertIn("is ill-defined.", paras[0]["raw"])

    def test_reader_markdown(self):
        out = P.render_markdown(None, P.PagesDoc(self.path).all_paragraphs())
        self.assertTrue(out.startswith("A wicked problem is ill-defined.\n\n"))

    def test_runs_after_the_marker_stay_in_their_paragraph(self):
        # bold on "ill-defined" must belong to paragraph 0, at the right place
        start = TEXT.index("ill-defined")
        doc = E.Document(write_pages(
            os.path.join(self.tmp.name, "b.pages"), TEXT,
            {F_PARA_TBL: table([(0, 1)]), F_ATTACHMENTS: table([(MARK, REF)]),
             F_CHAR_TBL: table([(0, None), (start, 900), (start + 11, None)])},
            extra=[(900, T_CHAR_STYLE, char_style(bold=True))]))
        para = doc.reader.paragraphs()[0]
        self.assertEqual([r[:3] for r in para["runs"]], [(start, start + 11, True)])

    def test_styled_reference_leaves_no_stray_markers(self):
        # the reference itself is italic+underline in Pages; it must not turn
        # "*wicked problem*" into "*wicked problem***"
        doc = E.Document(write_pages(
            os.path.join(self.tmp.name, "i.pages"), TEXT,
            {F_PARA_TBL: table([(0, 1)]), F_ATTACHMENTS: table([(MARK, REF)]),
             F_CHAR_TBL: table([(2, 901), (MARK, 902), (MARK + 1, None)])},
            extra=[(901, T_CHAR_STYLE, char_style(italic=True)),
                   (902, T_CHAR_STYLE, char_style(italic=True))]))
        out = P.render_markdown(None, doc.reader.all_paragraphs())
        self.assertTrue(out.startswith("A *wicked problem* is ill-defined."), out)

    def test_paragraph_bounds_span_the_marker(self):
        doc = E.Document(self.path)
        self.assertEqual(doc.paragraph_bounds(3), (0, SECOND - 1))
        self.assertEqual(doc.paragraph_bounds(MARK + 5), (0, SECOND - 1))

    def test_paragraph_starts(self):
        doc = E.Document(self.path)
        self.assertEqual(doc.paragraph_starts(0, len(TEXT)), [0, SECOND, THIRD])

    def test_delete_paragraph_removes_the_whole_sentence(self):
        doc = E.Document(self.path)
        doc.delete_paragraph(3)
        self.assertEqual(doc.text()[0], "Second paragraph.\nThird.\n")
        self.assertEqual(rows(doc, F_PARA_TBL), [(0, 2), (18, 1)])

    def test_retag_styles_the_whole_paragraph_once(self):
        doc = E.Document(self.path)
        doc.retag(MARK + 3, 7)
        self.assertEqual(rows(doc, F_PARA_TBL)[:2], [(0, 7), (SECOND, 2)])

    def test_insert_after_lands_after_the_sentence(self):
        doc = E.Document(self.path)
        raw, _ps, pe = E.located_paragraph(doc, 3)
        self.assertEqual(pe, SECOND - 1)

    def test_replacing_the_marker_itself_is_still_refused(self):
        doc = E.Document(self.path)
        with self.assertRaises(SystemExit):
            E.check_edits(doc, [(MARK, MARK + 1, 0, 1, "\x0e", None)], "x", False)

    def test_a_marker_without_an_attachment_is_still_a_break(self):
        path = write_pages(os.path.join(self.tmp.name, "s.pages"),
                           "Before\x0eAfter\n", {F_PARA_TBL: table([(0, 1)])})
        self.assertEqual([p["raw"] for p in P.PagesDoc(path).paragraphs()],
                         ["Before", "After", ""])


KITCHEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples",
                       "kitchen-sink.pages")


@unittest.skipUnless(os.path.exists(KITCHEN), "kitchen-sink sample not present")
class KitchenSinkFootnote(unittest.TestCase):
    def test_sentence_is_not_split(self):
        md = P.render_markdown(None, P.PagesDoc(KITCHEN).all_paragraphs())
        self.assertIn("What about a footnote?\n\n> What about it?", md)
        self.assertNotIn("\n\n?\n", md)


if __name__ == "__main__":
    unittest.main()
