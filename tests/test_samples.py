"""Checks against real documents written by Pages (tests/samples/).

Skipped when the sample is missing. See tests/samples/README.md for what each
sample must contain.
"""
import os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pages2md as P

SAMPLES = os.path.join(HERE, "samples")
EMOJI = os.path.join(SAMPLES, "emoji.pages")


def utf16_index(text, i):
    """The UTF-16 code-unit offset of Python (code point) index `i`."""
    return len(text[:i].encode("utf-16-le")) // 2


@unittest.skipUnless(os.path.exists(EMOJI), "tests/samples/emoji.pages not present")
class OffsetUnits(unittest.TestCase):
    """Review finding 1.6: are table indices code points or UTF-16 units?

    The sample puts two emoji (each one code point, two UTF-16 units) before
    a bold word, a heading and a comment, so the two conventions disagree by
    exactly 2 at each of them.
    """

    @classmethod
    def setUpClass(cls):
        cls.doc = P.PagesDoc(EMOJI)
        cls.raw = cls.doc._raw_text()
        f = cls.doc._body()
        cls.char = cls.doc._table(f, P.F_CHAR_TBL)
        cls.para = cls.doc._styles(f, P.F_PARA_TBL)

    def units_for(self, needle, indices):
        i = self.raw.find(needle)
        self.assertGreater(i, 0, f"sample text lacks {needle!r}")
        u = utf16_index(self.raw, i)
        self.assertNotEqual(i, u, "sample has no astral character before it")
        if i in indices:
            return "code points"
        if u in indices:
            return "UTF-16"
        self.fail(f"no table entry at {i} (code points) or {u} (UTF-16) "
                  f"for {needle!r}; entries: {sorted(indices)}")

    def test_character_styles(self):
        unit = self.units_for("Bold", {i for i, r in self.char if r})
        self.assertEqual(unit, "code points", "1.6 CONFIRMED: character "
                         "style indices are UTF-16 code units")

    def test_paragraph_styles(self):
        unit = self.units_for("Heading after emoji", {i for i, _r in self.para})
        self.assertEqual(unit, "code points", "1.6 CONFIRMED: paragraph "
                         "style indices are UTF-16 code units")

    def test_comment_ranges(self):
        starts = {c["start"] for c in self.doc.comments()}
        unit = self.units_for("commented", starts)
        self.assertEqual(unit, "code points", "1.6 CONFIRMED: comment "
                         "ranges are UTF-16 code units")


if __name__ == "__main__":
    unittest.main()
