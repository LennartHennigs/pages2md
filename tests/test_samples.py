"""Checks against real documents written by Pages 15.4 (tests/samples/).

Skipped when a sample is missing. tests/samples/README.md says what each one
contains. Edits always run on a temporary copy.
"""
import io, os, shutil, sys, tempfile, unittest, zipfile
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import iwa_codec as C
import pages2md as P
import pages_edit as E

SAMPLES = os.path.join(HERE, "samples")
EMOJI = os.path.join(SAMPLES, "emoji.pages")
KITCHEN = os.path.join(SAMPLES, "kitchen-sink.pages")
GUIDE = os.path.join(SAMPLES, "sample-content.pages")
FORMATTING = os.path.join(SAMPLES, "formatting.pages")


def utf16_index(text, i):
    """The UTF-16 code-unit offset of Python (code point) index `i`."""
    return len(text[:i].encode("utf-16-le")) // 2


def markdown(path, *args):
    out = io.StringIO()
    with redirect_stdout(out):
        P.main([*args, path])
    return out.getvalue()


class CopyCase(unittest.TestCase):
    SAMPLE = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, os.path.basename(self.SAMPLE))
        shutil.copy(self.SAMPLE, self.path)

    def edit(self, *args):
        """Run pages_edit on the copy, writing, with Pages assumed closed."""
        out = io.StringIO()
        with mock.patch.object(E, "pages_has_open", return_value=False), \
                redirect_stdout(out):
            E.main([*args, "--write", "--no-backup", self.path])
        return out.getvalue()


@unittest.skipUnless(os.path.exists(EMOJI), "tests/samples/emoji.pages not present")
class OffsetUnits(unittest.TestCase):
    """Review finding 1.6, settled: Pages' tables count UTF-16 code units.

    Two emoji (one code point, two UTF-16 units each) sit before a bold word,
    a heading and a comment, so the conventions disagree at each of them.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = P.PagesDoc(EMOJI)
        cls.raw = cls.doc._raw_text()
        f = cls.doc._body()
        cls.char = {i for i, r in cls.doc._table(f, P.F_CHAR_TBL) if r}
        cls.para = {i for i, _r in cls.doc._styles(f, P.F_PARA_TBL)}

    def assert_utf16(self, needle, indices):
        i = self.raw.find(needle)
        self.assertGreater(i, 0, f"sample text lacks {needle!r}")
        u = utf16_index(self.raw, i)
        self.assertNotEqual(i, u, "sample has no astral character before it")
        self.assertIn(u, indices)
        self.assertNotIn(i, indices)

    def test_character_styles_count_utf16(self):
        self.assert_utf16("Bold", self.char)

    def test_paragraph_styles_count_utf16(self):
        self.assert_utf16("Heading after emoji", self.para)

    def test_comment_ranges_count_utf16(self):
        self.assert_utf16("Commented", {c["start"] for c in self.doc.comments()})


@unittest.skipUnless(os.path.exists(EMOJI), "tests/samples/emoji.pages not present")
class ReadingAfterEmoji(unittest.TestCase):
    def test_markdown(self):
        self.assertEqual(markdown(EMOJI),
                         "😀😀 **Bold** word here.\n\n"
                         "# Heading after emoji\n\n"
                         "😀😀 Commented word here.\n")

    def test_comment_quotes_the_commented_word(self):
        (c,) = P.PagesDoc(EMOJI).comments()
        self.assertEqual(c["quote"], "Commented")

    def test_runs_index_the_returned_text(self):
        para = P.PagesDoc(EMOJI).paragraphs()[0]
        (a, b, bold, *_), = para["runs"]
        self.assertEqual((para["raw"][a:b], bold), ("Bold", True))

    def test_offsets_are_in_pages_units(self):
        offsets = [p["offset"] for p in P.PagesDoc(EMOJI).paragraphs()]
        self.assertEqual(offsets, [0, 23, 43])

    def test_fingerprints_agree(self):
        self.assertEqual(P.text_fingerprint(EMOJI), E.fingerprint(E.Document(EMOJI)))


@unittest.skipUnless(os.path.exists(EMOJI), "tests/samples/emoji.pages not present")
class EditingAfterEmoji(CopyCase):
    SAMPLE = EMOJI

    def test_replace_keeps_bold_on_the_new_word(self):
        self.edit("replace", "-f", "Bold", "-r", "Strong")
        self.assertTrue(markdown(self.path).startswith("😀😀 **Strong** word here."))

    def test_replace_keeps_comment_on_its_word(self):
        self.edit("replace", "-f", "word here.", "-r", "words.", "--all")
        (c,) = P.PagesDoc(self.path).comments()
        self.assertEqual(c["quote"], "Commented")
        self.assertIn("😀😀 **Bold** words.", markdown(self.path))

    def test_replace_an_emoji(self):
        self.edit("replace", "-f", "😀😀 Bold", "-r", "🎉 Bold")
        md = markdown(self.path)
        self.assertTrue(md.startswith("🎉 **Bold** word here."), md)
        (c,) = P.PagesDoc(self.path).comments()
        self.assertEqual(c["quote"], "Commented")

    def test_insert_with_emoji_keeps_following_styles(self):
        self.edit("insert", "--after", "Bold word", "--text", "New 🚀 paragraph",
                  "--style", "Body 1")
        self.assertEqual(markdown(self.path),
                         "😀😀 **Bold** word here.\n\nNew 🚀 paragraph\n\n"
                         "# Heading after emoji\n\n😀😀 Commented word here.\n")

    def test_format_after_emoji(self):
        self.edit("format", "--on", "Commented", "--bold")
        self.assertIn("😀😀 **Commented** word here.", markdown(self.path))

    def test_match_splitting_an_emoji_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.edit("replace", "-f", ".(?= Bold)", "-r", "x", "--regex")
        self.assertIn("emoji", str(cm.exception))

    def test_find_shows_real_characters(self):
        out = io.StringIO()
        with redirect_stdout(out):
            E.main(["find", "Bold", self.path])
        self.assertIn("😀😀 [Bold] word", out.getvalue())


@unittest.skipUnless(os.path.exists(KITCHEN), "tests/samples/kitchen-sink.pages not present")
class KitchenSink(unittest.TestCase):
    def test_codec_round_trips_every_iwa(self):
        with zipfile.ZipFile(KITCHEN) as z:
            for n in (n for n in z.namelist() if n.endswith(".iwa")):
                p = C.iwa_decode(z.read(n))
                self.assertEqual(C.pack_archives(C.archives(p)), p, n)
                self.assertEqual(C.iwa_decode(C.iwa_encode(p)), p, n)

    def test_every_format_renders(self):
        for fmt in sorted(P.RENDERERS):
            for changes in ("accept", "reject", "mark"):
                self.assertTrue(markdown(KITCHEN, "-t", fmt, "--changes", changes))

    def test_headings_and_lists(self):
        md = markdown(KITCHEN)
        for line in ("# Heading 1", "## Heading 2", "### Heading 3",
                     "- This\n- Is\n- A bulleted\n- list",
                     "1. This\n2. Is\n3. A numbered one"):
            self.assertIn(line + "\n", md)

    def test_tracked_changes(self):
        self.assertIn("This is an insert", markdown(KITCHEN))
        self.assertNotIn("This is an insert", markdown(KITCHEN, "--changes", "reject"))
        self.assertIn("## Heading~~ 2~~", markdown(KITCHEN, "--changes", "mark"))

    def test_comments_in_body_and_note(self):
        # Pages stores this comment's text with a trailing newline
        rows = [(c["handle"], c["quote"], [m["text"].strip() for m in c["thread"]])
                for c in P.PagesDoc(KITCHEN).comments()]
        self.assertEqual(rows, [("body", "Body", ["Love this", "thx!"]),
                                ("note1", "about", ["footnote comment"])])

    def test_fingerprints_agree(self):
        self.assertEqual(P.text_fingerprint(KITCHEN),
                         E.fingerprint(E.Document(KITCHEN)))


@unittest.skipUnless(os.path.exists(GUIDE), "sample-content.pages not present")
class RealGuide(CopyCase):
    """A real, German-localised guide saved by Pages 14.5, with footnotes."""
    SAMPLE = GUIDE

    def test_footnoted_sentences_are_whole(self):
        md = markdown(GUIDE)
        self.assertIn("works particularly well for *wicked problems*[^1] – "
                      "ill-defined and ambiguous challenges that resist "
                      "straightforward, clear-cut approaches.\n\n", md)
        self.assertNotIn("\n\n– ill-defined", md)      # the old split
        self.assertIn("validated with users[^2] .\n", md)
        self.assertIn("\n\n[^1]: For more details see: Camillus: "
                      "[Strategy as a Wicked Problem](https://lennarthennigs.de/dtw/01)"
                      " and Kolko: [Wicked Problems, Problems Worth Solving]"
                      "(https://lennarthennigs.de/dtw/10)\n", md)
        # footnote 2 has a manual line break inside it: a hard break, indented
        self.assertIn("\n\n[^2]: Because Design Thinking", md)
        self.assertIn("'humans'.", md)
        self.assertIn("  \n    Also, users and customers", md)

    def test_styles_resolve_through_localised_names(self):
        # German style names ("Text", "Überschrift", "Fußnote") map to the
        # English identifiers, in the body and in the footnotes
        sem = {}
        for p in P.PagesDoc(GUIDE).all_paragraphs():
            if p["raw"].strip():
                sem[p["semantic"]] = sem.get(p["semantic"], 0) + 1
        self.assertEqual(sem, {"Body 1": 11, "Heading 1": 2, "Heading 2": 1,
                               "Footnote Text": 2,
                               "paragraph-style-default": 1})

    def test_bold_and_italic_lead_ins(self):
        md = markdown(GUIDE)
        self.assertIn("**Scoping a request**. Start with *Design Thinking Workshops*", md)

    def test_comments_and_author(self):
        rows = [(c["quote"], c["thread"][0]["author"])
                for c in P.PagesDoc(GUIDE).comments()]
        self.assertEqual(rows, [("Table of Contents", "Sample Author"),
                                ("Introduction", "Sample Author")])

    def test_both_footnote_storages_are_editable_handles(self):
        self.assertEqual(list(E.Document(GUIDE).slots), ["body", "note1", "note2"])

    def test_edit_after_a_footnote_leaves_everything_else_alone(self):
        before = markdown(self.path)
        self.edit("replace", "-f", "human-centric", "-r", "people-centric")
        after = markdown(self.path)
        self.assertEqual(after, before.replace("human-centric", "people-centric"))

    def test_insert_format_delete_round_trip(self):
        before = markdown(self.path)
        self.edit("insert", "--after", "get started", "--text", "A new paragraph.",
                  "--style", "Body 1")
        self.assertIn("\nA new paragraph.\n", markdown(self.path))
        self.edit("format", "--on", "new paragraph", "--italic")
        self.assertIn("A *new paragraph*.", markdown(self.path))
        self.edit("delete-paragraph", "--on", "A new paragraph")
        self.assertEqual(markdown(self.path), before)

    def test_delete_a_footnoted_paragraph_is_refused_and_writes_nothing(self):
        before = markdown(self.path)
        with self.assertRaises(SystemExit) as cm:
            self.edit("delete-paragraph", "--on", "driven by empathy")
        self.assertIn("footnote reference", str(cm.exception))
        self.assertEqual(markdown(self.path), before)


@unittest.skipUnless(os.path.exists(FORMATTING), "formatting.pages not present")
class Formatting(CopyCase):
    """Pages 15.4: a Title, character formatting, a link, nested lists."""
    SAMPLE = FORMATTING

    @classmethod
    def setUpClass(cls):
        cls.md = markdown(FORMATTING)

    def test_title(self):
        self.assertTrue(self.md.startswith("# Titel\n\n# Heading 1\n"))

    def test_nested_list_levels(self):
        # This(0) Is(1) A bulleted(1) list(2), as drawn in the document preview
        self.assertIn("\n- This\n  - Is\n  - A bulleted\n    - list\n", self.md)

    def test_flat_lists_are_unchanged(self):
        self.assertIn("\n- This\n- Is\n- A bulleted\n- list\n", self.md)
        self.assertIn("\n1. This\n2. Is\n3. A numbered one\n", self.md)

    def test_character_formatting(self):
        # italic, bold italic struck through, bold italic underlined; plain
        # underline ("Lorem", "amet") has no Markdown form
        self.assertIn("Lorem ipsum *dolor* sit amet, ~~***consectetur***~~ "
                      "***adipiscing*** elit", self.md)

    def test_underline_is_in_the_runs_not_the_markdown(self):
        para = next(p for p in P.PagesDoc(FORMATTING).paragraphs()
                    if p["raw"].startswith("Lorem ipsum"))
        by_text = {para["raw"][a:b]: (bold, italic, under, strike)
                   for a, b, bold, italic, under, strike in para["runs"]}
        self.assertEqual(by_text["Lorem"], (False, False, True, False))
        self.assertEqual(by_text["dolor"], (False, True, False, False))
        self.assertEqual(by_text["consectetur"], (True, True, True, True))
        self.assertEqual(by_text["adipiscing"], (True, True, True, False))

    def test_footnote_reference_is_not_underlined(self):
        # its style sets property 10, which is not underline
        para = next(p for p in P.PagesDoc(FORMATTING).paragraphs()
                    if "footnote" in p["raw"])
        self.assertEqual(para["runs"], [])

    def test_body_link(self):
        self.assertIn("ut [aliquip](http://google.de) ex ea", self.md)

    def test_levels_in_json(self):
        levels = [p["list_level"] for p in P.PagesDoc(FORMATTING).paragraphs()
                  if p["list_kind"]]
        # bullets (4), numbers (3), the nested list (4), then three bullets
        self.assertEqual(levels, [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 2, 0, 0, 0])

    def test_deleting_a_nested_item_keeps_the_levels_of_the_rest(self):
        doc = E.Document(self.path)
        raw = doc.text()[0]
        doc.delete_paragraph(raw.rindex("Is \nA bulleted\nlist"))   # level 1
        doc.save(self.path)
        self.assertIn("\n- This\n  - A bulleted\n    - list\n", markdown(self.path))

    def test_edit_text_in_a_nested_item_keeps_the_levels(self):
        before = markdown(self.path)
        self.edit("replace", "-f", "A bulleted", "-r", "Edited", "--occurrence", "2")
        after = markdown(self.path)
        self.assertEqual(after, before.replace("  - A bulleted\n    - list",
                                               "  - Edited\n    - list"))


if __name__ == "__main__":
    unittest.main()
