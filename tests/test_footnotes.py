"""A footnote reference (\\x0e with an attachment-table entry) is inline.

The reader and editor used to treat every \\x0e as a section break, so each
footnote cut its paragraph in two: the sentence broke around the note, and
the second half lost its paragraph style. Found in a real document (Pages
14.5) and in tests/samples/kitchen-sink.pages.
"""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture import (write_pages, table, rows, char_style, footnote, T_CHAR_STYLE,
                     F_PARA_TBL, F_CHAR_TBL, F_ATTACHMENTS, F_DELETIONS)
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

    def test_deleting_a_paragraph_with_a_reference_is_refused(self):
        # the note's storage would stay behind unreferenced; see Document.require_no_footnotes
        doc = E.Document(self.path)
        self.assertEqual(doc.paragraph_bounds(3), (0, SECOND - 1))     # the whole sentence
        with self.assertRaises(SystemExit) as cm:
            doc.delete_paragraph(3)
        self.assertIn("footnote reference", str(cm.exception))
        self.assertEqual(doc.text()[0], TEXT)

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


class FootnotesAsMarkdown(unittest.TestCase):
    """Footnote references render as [^n] with the definitions at the end."""

    NOTE1, NOTE2 = 1000, 1001

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doc(self, text, anchors, notes, extra_tables=None, name="n.pages"):
        """anchors: [(offset, attachment id)]; notes: archives from footnote()."""
        tables = {F_PARA_TBL: table([(0, 1)]), F_ATTACHMENTS: table(anchors)}
        tables.update(extra_tables or {})
        path = write_pages(os.path.join(self.tmp.name, name), text, tables,
                           extra=[a for n in notes for a in n])
        return P.PagesDoc(path)

    def md(self, doc, **kw):
        return P.render_markdown(doc, doc.all_paragraphs(**kw))

    def test_reference_and_definition(self):
        doc = self.doc(TEXT, [(MARK, 1100)],
                       [footnote(1100, 1200, "\ufffc Note text.")])
        self.assertEqual(self.md(doc), "A wicked problem[^1] is ill-defined.\n\n"
                         "Second paragraph.\n\nThird.\n\n[^1]: Note text.\n")

    def test_numbered_in_reading_order(self):
        text = "One\x0e and two\x0e.\nThree\x0e.\n"
        anchors = [(text.index("\x0e"), 1100), (text.index("\x0e", 5), 1101),
                   (text.rindex("\x0e"), 1102)]
        doc = self.doc(text, anchors, [footnote(1100, 1200, "\ufffc First."),
                                       footnote(1101, 1201, "\ufffc Second."),
                                       footnote(1102, 1202, "\ufffc Third.")])
        self.assertEqual(self.md(doc), "One[^1] and two[^2].\n\nThree[^3].\n\n"
                         "[^1]: First.\n\n[^2]: Second.\n\n[^3]: Third.\n")

    def test_multi_paragraph_note(self):
        doc = self.doc(TEXT, [(MARK, 1100)],
                       [footnote(1100, 1200, "\ufffc First part.\nSecond part.")])
        self.assertTrue(self.md(doc).endswith(
            "\n\n[^1]: First part.\n\n    Second part.\n"))

    def test_reference_after_emphasis(self):
        doc = self.doc(TEXT, [(MARK, 1100)], [footnote(1100, 1200, "\ufffc N.")],
                       {F_CHAR_TBL: table([(2, 901), (MARK, None)])})
        self.assertTrue(self.md(doc).startswith("A wicked problem[^1] is"))

    def test_skip_drops_references_and_definitions(self):
        doc = self.doc(TEXT, [(MARK, 1100)], [footnote(1100, 1200, "\ufffc N.")])
        md = self.md(doc, notes="skip")
        self.assertNotIn("[^", md)
        self.assertTrue(md.startswith("A wicked problem is ill-defined."))

    def test_only_shows_just_the_definitions(self):
        doc = self.doc(TEXT, [(MARK, 1100)], [footnote(1100, 1200, "\ufffc N.")])
        self.assertEqual(self.md(doc, notes="only"), "[^1]: N.\n")

    def test_note_anchored_elsewhere_stays_a_blockquote(self):
        # an attachment on an ordinary character is not a footnote reference
        doc = self.doc(TEXT, [(2, 1100)], [footnote(1100, 1200, "\ufffc Aside.")])
        md = self.md(doc)
        self.assertIn("> Aside.", md)
        self.assertNotIn("[^", md)

    def test_deleted_reference_takes_its_note_with_it(self):
        dels = table([(MARK, 700), (MARK + 1, None)])
        doc = self.doc(TEXT, [(MARK, 1100)], [footnote(1100, 1200, "\ufffc N.")],
                       {F_DELETIONS: dels})
        accepted = self.md(doc)
        self.assertNotIn("[^", accepted)
        self.assertNotIn("N.", accepted)
        self.assertIn("[^1]: N.", self.md(doc, changes="reject"))

    def test_json_carries_the_numbers(self):
        doc = self.doc(TEXT, [(MARK, 1100)], [footnote(1100, 1200, "\ufffc N.")])
        paras = doc.all_paragraphs()
        self.assertEqual(paras[0]["ref_nos"], [1])
        shown = [p["footnote"] for p in paras if p["raw"].strip()]
        self.assertEqual(shown, [None, 1, None, None])   # note follows its paragraph

    def test_plain_text_is_unchanged(self):
        doc = self.doc(TEXT, [(MARK, 1100)], [footnote(1100, 1200, "\ufffc N.")])
        self.assertNotIn("[^", P.render_plain(doc, doc.all_paragraphs()))


KITCHEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples",
                       "kitchen-sink.pages")


@unittest.skipUnless(os.path.exists(KITCHEN), "kitchen-sink sample not present")
class KitchenSinkFootnote(unittest.TestCase):
    def test_sentence_is_not_split(self):
        md = P.render_markdown(None, P.PagesDoc(KITCHEN).all_paragraphs())
        self.assertIn("What about a footnote[^1]?\n", md)
        self.assertTrue(md.endswith("\n\n[^1]: What about it?\n"), md[-80:])
        self.assertNotIn("\n\n?\n", md)


if __name__ == "__main__":
    unittest.main()
