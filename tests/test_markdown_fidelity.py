"""Markdown output: hyperlinks, ordered lists, tight lists, escaping."""
import os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fixture import (write_pages, table, char_style, hyperlink,
                     list_style, para_starts, para_levels, T_CHAR_STYLE, F_PARA_TBL,
                     F_LIST_TBL, F_CHAR_TBL, F_DELETIONS)
import pages2md as P

F_SMARTFIELD, F_PARA_STARTS, F_LEVELS = 11, 14, 6
LINK, LINK2, OTHER = 1500, 1501, 1502
BULLET, NUMBERED, LETTERED, NOLIST = 1600, 1601, 1602, 1603
LIST_STYLES = [(BULLET, P.T_LIST_STYLE, list_style("Bullet", 2)),
               (NUMBERED, P.T_LIST_STYLE, list_style("Numbered", 3)),
               (LETTERED, P.T_LIST_STYLE, list_style("Lettered", 3)),
               (NOLIST, P.T_LIST_STYLE, list_style("None", 0))]


def para(raw, **kw):
    """A paragraph dict as paragraphs() returns it, for renderer tests."""
    base = dict(raw=raw, runs=[], struck=[], semantic="Body 1", list=None,
                list_kind=None, list_start=False, list_level=0, links=[],
                sidenote=None,
                footnote=None, ref_nos=[], offset=0, style="Body")
    base.update(kw)
    return base


def render(*paras):
    return P.render_markdown(None, list(paras))


class Links(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.n = 0

    def md(self, text, links, extra=(), tables=None, **kw):
        self.n += 1
        t = {F_PARA_TBL: table([(0, 1)]), F_SMARTFIELD: table(links)}
        t.update(tables or {})
        path = write_pages(os.path.join(self.tmp.name, f"{self.n}.pages"), text, t,
                           extra=[(LINK, 2032, hyperlink("https://example.com/a")),
                                  (LINK2, 2032, hyperlink("https://example.com/a b(c)")),
                                  (OTHER, 2099, b"\x0a\x00"), *extra])
        doc = P.PagesDoc(path)
        return P.render_markdown(doc, doc.all_paragraphs(**kw))

    def test_a_link(self):
        self.assertEqual(self.md("See the docs now.\n", [(8, LINK), (12, None)]),
                         "See the [docs](https://example.com/a) now.\n")

    def test_link_to_the_end_of_the_text(self):
        self.assertEqual(self.md("Go here", [(3, LINK)]),
                         "Go [here](https://example.com/a)\n")

    def test_url_with_a_space_and_parentheses_is_bracketed(self):
        self.assertEqual(self.md("a link\n", [(2, LINK2), (6, None)]),
                         "a [link](<https://example.com/a b(c)>)\n")

    def test_other_smart_fields_are_ignored(self):
        self.assertEqual(self.md("Page 3 of 9\n", [(5, OTHER), (6, None)]),
                         "Page 3 of 9\n")

    def test_whitespace_stays_outside_the_brackets(self):
        self.assertEqual(self.md("a  link  b\n", [(2, LINK), (8, None)]),
                         "a  [link](https://example.com/a)  b\n")

    def test_link_after_an_emoji(self):
        # offsets are UTF-16 units: the emoji counts as two
        self.assertEqual(self.md("😀 see docs\n", [(7, LINK), (11, None)]),
                         "😀 see [docs](https://example.com/a)\n")

    def test_bold_link(self):
        styles = [(900, T_CHAR_STYLE, char_style(bold=True))]
        out = self.md("see docs now\n", [(4, LINK), (8, None)], styles,
                      {F_CHAR_TBL: table([(4, 900), (8, None)])})
        self.assertEqual(out, "see [**docs**](https://example.com/a) now\n")

    def test_bold_running_into_a_link_is_split_at_its_edge(self):
        styles = [(900, T_CHAR_STYLE, char_style(bold=True))]
        out = self.md("see docs now\n", [(4, LINK), (8, None)], styles,
                      {F_CHAR_TBL: table([(0, 900), (6, None)])})
        self.assertEqual(out, "**see** [**do**cs](https://example.com/a) now\n")

    def test_deleted_link_text_is_dropped_with_its_link(self):
        out = self.md("see docs now\n", [(4, LINK), (8, None)],
                      tables={F_DELETIONS: table([(4, 700), (8, None)])})
        self.assertEqual(out, "see  now\n")

    def test_json_exposes_the_links(self):
        self.md("See the docs now.\n", [(8, LINK), (12, None)])
        path = os.path.join(self.tmp.name, f"{self.n}.pages")
        p = P.PagesDoc(path).all_paragraphs()[0]
        self.assertEqual(p["links"], [(8, 12, "https://example.com/a")])

    def test_bold_wholly_inside_a_link_is_not_split(self):
        styles = [(900, T_CHAR_STYLE, char_style(bold=True))]
        out = self.md("go to the docs now\n", [(3, LINK), (14, None)], styles,
                      {F_CHAR_TBL: table([(6, 900), (9, None)])})
        self.assertEqual(out, "go [to **the** docs](https://example.com/a) now\n")

    def test_link_wholly_inside_bold_is_not_split(self):
        styles = [(900, T_CHAR_STYLE, char_style(bold=True))]
        out = self.md("go to the docs now\n", [(10, LINK), (14, None)], styles,
                      {F_CHAR_TBL: table([(0, 900), (18, None)])})
        self.assertEqual(out, "**go to the [docs](https://example.com/a) now**\n")


class ListCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.n = 0

    def md(self, text, styles, starts=None, levels=None):
        """styles: [(paragraph start offset, list style id)]; levels: [(offset, level)]."""
        self.n += 1
        tables = {F_PARA_TBL: table([(0, 1)]), F_LIST_TBL: table(styles)}
        if starts:
            tables[F_PARA_STARTS] = para_starts(starts)
        if levels:
            tables[F_LEVELS] = para_levels(levels)
        path = write_pages(os.path.join(self.tmp.name, f"{self.n}.pages"), text,
                           tables, extra=LIST_STYLES)
        doc = P.PagesDoc(path)
        return P.render_markdown(doc, doc.all_paragraphs())


class Lists(ListCase):
    def test_bullets_are_a_tight_list(self):
        self.assertEqual(self.md("a\nb\nc\n", [(0, BULLET)]),
                         "- a\n- b\n- c\n")

    def test_numbered_items_count_up(self):
        self.assertEqual(self.md("a\nb\nc\n", [(0, NUMBERED)]),
                         "1. a\n2. b\n3. c\n")

    def test_lettered_and_other_numbered_styles_are_ordered(self):
        self.assertEqual(self.md("a\nb\n", [(0, LETTERED)]), "1. a\n2. b\n")

    def test_a_plain_paragraph_ends_the_list_and_restarts_numbering(self):
        self.assertEqual(
            self.md("a\nb\ntext\nc\nd\n", [(0, NUMBERED), (4, NOLIST), (9, NUMBERED)]),
            "1. a\n2. b\n\ntext\n\n1. c\n2. d\n")

    def test_the_restart_flag_starts_over(self):
        self.assertEqual(self.md("a\nb\nc\n", [(0, NUMBERED)],
                                 starts=[(0, 1), (2, 0), (4, 1)]),
                         "1. a\n2. b\n1. c\n")

    def test_bullets_then_numbers_are_separate_lists(self):
        self.assertEqual(self.md("a\nb\n1\n2\n", [(0, BULLET), (4, NUMBERED)]),
                         "- a\n- b\n\n1. 1\n2. 2\n")

    def test_list_between_paragraphs(self):
        self.assertEqual(self.md("intro\na\nb\noutro\n",
                                 [(0, NOLIST), (6, BULLET), (10, NOLIST)]),
                         "intro\n\n- a\n- b\n\noutro\n")

    def test_json_names_the_kind(self):
        self.md("a\nb\n", [(0, NUMBERED)])
        path = os.path.join(self.tmp.name, f"{self.n}.pages")
        kinds = [(p["list"], p["list_kind"]) for p in P.PagesDoc(path).paragraphs()[:2]]
        self.assertEqual(kinds, [("Numbered", "numbered")] * 2)

    def test_none_style_is_not_a_list(self):
        self.assertEqual(self.md("a\nb\n", [(0, NOLIST)]), "a\n\nb\n")


class NestedLists(ListCase):
    """List levels live in field 6, run-length: an entry holds until the next."""

    def test_levels_hold_until_the_next_entry(self):
        # Pages' own: This(0) Is(1) A bulleted(1) list(2)
        out = self.md("This\nIs\nA bulleted\nlist\n", [(0, BULLET)],
                      levels=[(0, 0), (5, 1), (19, 2), (24, 0)])
        self.assertEqual(out, "- This\n  - Is\n  - A bulleted\n    - list\n")

    def test_level_json(self):
        self.md("a\nb\nc\n", [(0, BULLET)], levels=[(0, 0), (2, 1), (4, 2)])
        path = os.path.join(self.tmp.name, f"{self.n}.pages")
        self.assertEqual([p["list_level"] for p in P.PagesDoc(path).paragraphs()[:3]],
                         [0, 1, 2])

    def test_numbering_is_per_level(self):
        out = self.md("a\nb\nc\nd\ne\nf\n", [(0, NUMBERED)],
                      levels=[(0, 0), (2, 1), (6, 0)])
        self.assertEqual(out, "1. a\n   1. b\n   2. c\n2. d\n3. e\n4. f\n")

    def test_a_new_sublist_starts_at_one_again(self):
        out = self.md("a\nb\nc\nd\ne\n", [(0, NUMBERED)],
                      levels=[(0, 0), (2, 1), (4, 0), (6, 1), (8, 0)])
        self.assertEqual(out, "1. a\n   1. b\n2. c\n   1. d\n3. e\n")

    def test_bullets_under_a_numbered_item(self):
        out = self.md("one\nsub\ntwo\n", [(0, NUMBERED), (4, BULLET), (8, NUMBERED)],
                      levels=[(0, 0), (4, 1), (8, 0)])
        self.assertEqual(out, "1. one\n   - sub\n2. two\n")

    def test_wide_numbers_indent_their_children(self):
        text = "".join(f"i{k}\n" for k in range(1, 11)) + "kid\n"
        n = sum(len(f"i{k}\n") for k in range(1, 11))
        out = self.md(text, [(0, NUMBERED)], levels=[(0, 0), (n, 1)])
        self.assertTrue(out.endswith("10. i10\n    1. kid\n"), out)

    def test_a_skipped_level_is_clamped(self):
        out = self.md("a\nb\n", [(0, BULLET)], levels=[(0, 0), (2, 2)])
        self.assertEqual(out, "- a\n  - b\n")

    def test_a_level_with_no_list_style_is_ignored(self):
        out = self.md("a\nb\n", [(0, NOLIST)], levels=[(0, 0), (2, 1)])
        self.assertEqual(out, "a\n\nb\n")

    def test_plain_paragraph_between_resets_nesting(self):
        out = self.md("a\nb\ntext\nc\n", [(0, BULLET), (4, NOLIST), (9, BULLET)],
                      levels=[(0, 0), (2, 1), (9, 1)])
        self.assertEqual(out, "- a\n  - b\n\ntext\n\n- c\n")


class Formatting(unittest.TestCase):
    def md(self, raw, runs):
        return render(para(raw, runs=runs))

    def test_strikethrough(self):
        self.assertEqual(self.md("a gone b", [(2, 6, False, False, False, True)]),
                         "a ~~gone~~ b\n")

    def test_underline_alone_has_no_markup(self):
        self.assertEqual(self.md("a word b", [(2, 6, False, False, True, False)]),
                         "a word b\n")

    def test_bold_italic_underline_strike_all_at_once(self):
        self.assertEqual(self.md("xx", [(0, 2, True, True, True, True)]),
                         "~~***xx***~~\n")

    def test_formatting_strike_and_tracked_deletion_do_not_double_up(self):
        out = render(para("a gone b", runs=[(2, 6, False, False, False, True)],
                          struck=[(2, 6)]))
        self.assertEqual(out, "a ~~gone~~ b\n")

    def test_overlapping_strikes_merge(self):
        out = render(para("abcdef", runs=[(0, 4, False, False, False, True)],
                          struck=[(2, 6)]))
        self.assertEqual(out, "~~abcdef~~\n")

    def test_five_element_runs_still_work(self):
        self.assertEqual(self.md("a b c", [(2, 3, True, False, False)]),
                         "a **b** c\n")


class Escaping(unittest.TestCase):
    CASES = [
        ("1. not a list", "1\\. not a list"),
        ("2) also not", "2\\) also not"),
        ("# not a heading", "\\# not a heading"),
        ("- not a bullet", "\\- not a bullet"),
        ("+ nor this", "\\+ nor this"),
        ("> not a quote", "\\> not a quote"),
        ("===", "\\==="),
        ("---", "\\---"),
        ("2 * 3 * 4", "2 \\* 3 \\* 4"),
        ("snake_case stays", "snake_case stays"),
        ("an _emphasis_ lookalike", "an \\_emphasis\\_ lookalike"),
        ("use `code` here", "use \\`code\\` here"),
        ("a [bracket] pair", "a \\[bracket\\] pair"),
        ("back\\slash", "back\\\\slash"),
        ("<b>bold</b>", "\\<b>bold\\</b>"),
        ("a < b and 3 < 4", "a < b and 3 < 4"),
        ("fish &amp; chips", "fish \\&amp; chips"),
        ("A & B", "A & B"),
        ("a ~~gone~~ b", "a \\~\\~gone\\~\\~ b"),
        ("a ~ b", "a ~ b"),
        ("3.5 million and 1986 was good", "3.5 million and 1986 was good"),
        ("Plain text, with: punctuation! (and parens)",
         "Plain text, with: punctuation! (and parens)"),
        ("first - second 1. third",
         "first  \n\\- second  \n1\\. third"),
    ]

    def test_table(self):
        for raw, want in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(render(para(raw)), want + "\n")

    def test_escapes_work_inside_emphasis(self):
        self.assertEqual(render(para("*x*", runs=[(0, 3, True, False, False)])),
                         "**\\*x\\***\n")

    def test_escapes_in_a_list_item_and_a_heading(self):
        self.assertEqual(render(para("1. x", list="Bullet", list_kind="bullet")),
                         "- 1\\. x\n")
        self.assertEqual(render(para("# x", semantic="Heading 1")), "# \\# x\n")

    def test_footnote_markers_survive_escaping(self):
        out = render(para("a * b\x0e", ref_nos=[1]),
                     para("note", sidenote=1, footnote=1))
        self.assertEqual(out, "a \\* b[^1]\n\n[^1]: note\n")

    def test_strike_markers_are_not_escaped(self):
        self.assertEqual(render(para("a b c", struck=[(2, 3)])), "a ~~b~~ c\n")

    def test_plain_and_json_are_not_escaped(self):
        self.assertEqual(P.render_plain(None, [para("1. a * b")]), "1. a * b\n")


if __name__ == "__main__":
    unittest.main()
