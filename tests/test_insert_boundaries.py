"""Inserting before a paragraph that is led by a break character.

Pages leads some paragraphs with a page break (\\x04), an object anchor (\\x05) or a
section break (\\x0e): the character sits right before the text, and the paragraph's own
style entry is on the text. `insert_paragraph` only treated "\\n" as "already at a
paragraph start", so `insert --before "What It Is"` in a real document put an extra "\\n"
after the anchor and left an empty paragraph behind. Found by random insertions on the
real sample; the same goes for `import --before`.
"""
import io, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fixture import write_pages, table, rows, F_PARA_TBL, F_ATTACHMENTS
import pages_edit as E
import pages2md as P

S1, S2, S3 = 1, 2, 3
SAMPLE = os.path.join(HERE, "samples", "sample-content.pages")


def starts(doc):
    return doc.paragraph_starts(0, len(doc.text()[0]))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doc(self, text, paragraph_styles, attachments=None):
        tables = {F_PARA_TBL: table(paragraph_styles)}
        if attachments:
            tables[F_ATTACHMENTS] = table(attachments)
        return E.Document(write_pages(os.path.join(self.tmp.name, "t.pages"), text, tables))


class AtAParagraphLedByABreak(Base):

    def test_each_break_character_counts_as_a_paragraph_start(self):
        for char in ("\x04", "\x05", "\x0e"):
            with self.subTest(break_char=repr(char)):
                doc = self.doc(f"A\n{char}B\nC\n", [(0, S1), (3, S2)])
                before = len(starts(doc))
                doc.insert_paragraph(3, "NEW", S1)                 # before B, after the break
                self.assertEqual(doc.text()[0], f"A\n{char}NEW\nB\nC\n")
                self.assertEqual(len(starts(doc)), before + 1)     # one paragraph, not two

    def test_the_following_paragraph_keeps_its_style(self):
        doc = self.doc("A\n\x05B\nC\n", [(0, S1), (3, S2)])
        doc.insert_paragraph(3, "NEW", S3)
        self.assertEqual(rows(doc, F_PARA_TBL), [(0, S1), (3, S3), (7, S2)])

    def test_the_break_character_stays_where_it_was(self):
        doc = self.doc("A\n\x05B\n", [(0, S1), (3, S2)])
        doc.insert_paragraph(3, "NEW", S1)
        self.assertEqual(doc.text()[0].index("\x05"), 2)

    def test_after_a_newline_nothing_changes(self):
        doc = self.doc("A\nB\n", [(0, S1), (2, S2)])
        doc.insert_paragraph(2, "NEW", S1)
        self.assertEqual(doc.text()[0], "A\nNEW\nB\n")

    def test_at_the_very_start(self):
        doc = self.doc("A\nB\n", [(0, S1)])
        doc.insert_paragraph(0, "NEW", S1)
        self.assertEqual(doc.text()[0], "NEW\nA\nB\n")

    def test_in_the_middle_of_a_paragraph_a_line_break_is_still_added(self):
        doc = self.doc("Some text\n", [(0, S1)])
        doc.insert_paragraph(4, "NEW", S1)
        self.assertEqual(doc.text()[0], "Some\nNEW\n text\n")

    def test_just_after_a_footnote_reference_is_mid_sentence_so_a_break_is_added(self):
        doc = self.doc("See\x0e more.\nNext\n", [(0, S1)], attachments=[(3, 999)])
        doc.insert_paragraph(4, "NEW", S1)
        self.assertEqual(doc.text()[0], "See\x0e\nNEW\n more.\nNext\n")

    def test_at_the_end_after_a_paragraph_without_a_final_newline(self):
        doc = self.doc("A\nB", [(0, S1)])
        doc.insert_paragraph(3, "NEW", S1)
        self.assertEqual(doc.text()[0], "A\nB\nNEW\n")


class AfterAParagraphThatABreakEnds(Base):
    """`insert --after X` where a page break, section break or anchor ends X: the new
    paragraph goes between X and the break, so it stays on X's page; the break now ends
    the new paragraph. It used to go after the break, onto the next page."""

    def insert_after(self, doc, offset, text, style):
        _ps, pe = doc.paragraph_bounds(offset)
        return doc.insert_paragraph(E.after_paragraph(doc.text()[0], pe), text, style)

    def test_each_break_character(self):
        for char in ("\x04", "\x05", "\x0e"):
            with self.subTest(break_char=repr(char)):
                doc = self.doc(f"A{char}B\nC\n", [(0, S1), (2, S2)])
                before = len(starts(doc))
                self.insert_after(doc, 0, "NEW", S3)
                self.assertEqual(doc.text()[0], f"A\nNEW{char}B\nC\n")
                self.assertEqual(len(starts(doc)), before + 1)

    def test_styles_stay_on_their_paragraphs(self):
        doc = self.doc("A\x04B\n", [(0, S1), (2, S2)])
        self.insert_after(doc, 0, "NEW", S3)
        self.assertEqual(rows(doc, F_PARA_TBL), [(0, S1), (2, S3), (5, S1), (6, S2)])

    def test_after_a_newline_and_then_a_break(self):
        doc = self.doc("A\n\x05B\n", [(0, S1), (3, S2)])
        self.insert_after(doc, 0, "NEW", S3)
        self.assertEqual(doc.text()[0], "A\nNEW\x05B\n")

    def test_after_a_footnote_reference_is_not_a_break(self):
        doc = self.doc("See\x0e more.\nNext\n", [(0, S1)], attachments=[(3, 999)])
        self.insert_after(doc, 0, "NEW", S1)
        self.assertEqual(doc.text()[0], "See\x0e more.\nNEW\nNext\n")


@unittest.skipUnless(os.path.exists(SAMPLE), "sample not present")
class OnTheRealGuide(unittest.TestCase):
    """sample-content.pages: several headings are led by \\x05 anchors."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "g.pages")
        shutil.copy(SAMPLE, self.path)

    def sig(self):
        return [(p["raw"], p["semantic"], p["list_kind"], p["list_level"], tuple(p["runs"]),
                 tuple(p["links"])) for p in P.PagesDoc(self.path).paragraphs("mark")]

    def run_cli(self, *args):
        with mock.patch.object(E, "pages_has_open", return_value=False), redirect_stdout(io.StringIO()):
            E.main([*args, "--write", "--no-backup", self.path])

    def assert_only_added(self, before, added):
        after = self.sig()
        self.assertEqual(len(after), len(before) + len(added), "extra paragraphs appeared")
        for j in range(len(after)):
            if [a[0] for a in after[j:j + len(added)]] == added:
                self.assertEqual(after[:j] + after[j + len(added):], before,
                                 "the other paragraphs changed")
                return after[j:j + len(added)]
        self.fail(f"inserted text {added} not found")

    def test_insert_before_a_heading_led_by_an_anchor(self):
        before = self.sig()
        self.run_cli("insert", "--before", "What It Is", "--text", "Inserted before.", "--style", "Body 1")
        (new,) = self.assert_only_added(before, ["Inserted before."])
        self.assertEqual(new[1], "Body 1")

    def test_insert_before_the_first_heading(self):
        before = self.sig()
        self.run_cli("insert", "--before", "Introduction", "--text", "Inserted before.", "--style", "Body 1")
        self.assert_only_added(before, ["Inserted before."])

    def test_the_heading_is_still_a_heading(self):
        self.run_cli("insert", "--before", "What It Is", "--text", "x", "--style", "Body 1")
        headings = [(l, t) for l, t, _o in P.outline(self.path)]
        self.assertIn((2, "What It Is"), headings)

    def test_insert_after_a_paragraph_an_anchor_ends(self):
        before = self.sig()
        self.run_cli("insert", "--after", "get started", "--text", "Inserted after.", "--style", "Body 1")
        self.assert_only_added(before, ["Inserted after."])
        raw = P.PagesDoc(self.path)._raw_text()
        at = raw.index("Inserted after.")
        self.assertEqual(raw[at - 20:at], "Let’s get started…\t\n")    # right after it,
        self.assertEqual(raw[at + 15], "\x05")                         # before the anchors

    def test_import_before_a_heading_led_by_an_anchor(self):
        before = self.sig()
        md = os.path.join(self.tmp.name, "i.md")
        with open(md, "w", encoding="utf-8") as fh:
            fh.write("## Imported heading\n\nOne paragraph.\n")
        self.run_cli("import", md, "--before", "What It Is")
        self.assert_only_added(before, ["Imported heading", "One paragraph."])

    def test_markdown_shows_exactly_the_new_paragraph(self):
        before = P.render_markdown(None, P.PagesDoc(self.path).all_paragraphs())
        self.run_cli("insert", "--before", "What It Is", "--text", "Inserted before.", "--style", "Body 1")
        after = P.render_markdown(None, P.PagesDoc(self.path).all_paragraphs())
        self.assertEqual(after, before.replace("## What It Is", "Inserted before.\n\n## What It Is"))


if __name__ == "__main__":
    unittest.main()
