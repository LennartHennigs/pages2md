"""An empty search text matches at every character, so `replace -f "" --all --write`
wrote the replacement 3638 times into a 3622-character document ("Titel" became
"XTXiXtXeXlX"). It is refused, with the same message wherever a search text is taken.
Zero-length *patterns* that mean something (`^`, `$`, `\\b`) keep working.
"""
import io, json, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pages_edit as E
import pages2md as P

SAMPLE = os.path.join(HERE, "samples", "formatting.pages")


def cli(*args):
    """Run pages_edit; -> (exit message or None, stdout)."""
    out = io.StringIO()
    try:
        with mock.patch.object(E, "pages_has_open", return_value=False), redirect_stdout(out):
            E.main(list(args))
    except SystemExit as exc:
        return str(exc.code), out.getvalue()
    return None, out.getvalue()


@unittest.skipUnless(os.path.exists(SAMPLE), "sample not present")
class EmptySearchText(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "d.pages")
        shutil.copy(SAMPLE, self.path)
        with open(self.path, "rb") as fh:
            self.original = fh.read()

    def untouched(self):
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), self.original)
        self.assertFalse(os.path.exists(self.path + ".bak"))

    def test_replace_all_with_nothing_to_find_is_refused_and_writes_nothing(self):
        msg, _ = cli("replace", "-f", "", "-r", "X", "--all", "--write", self.path)
        self.assertIn("empty search text", msg)
        self.untouched()

    def test_the_message_says_what_to_do(self):
        msg, _ = cli("replace", "-f", "", "-r", "X", self.path)
        self.assertIn("matches at every character", msg)
        self.assertIn("--regex", msg)               # zero-width patterns are still possible

    def test_find_refuses_it_too(self):
        msg, out = cli("find", "", self.path)
        self.assertIn("empty search text", msg)
        self.assertNotIn("match(es)", out)

    def test_an_empty_find_file_is_empty_search_text(self):
        empty = os.path.join(self.tmp.name, "empty.txt")
        open(empty, "w").close()
        msg, _ = cli("replace", "--find-file", empty, "-r", "X", "--write", self.path)
        self.assertIn("empty search text", msg)
        self.untouched()

    def test_an_empty_regex_is_refused_as_well(self):
        msg, _ = cli("replace", "--regex", "-f", "", "-r", "X", "--all", "--write", self.path)
        self.assertIn("empty search text", msg)
        self.untouched()

    def test_structural_commands_use_the_same_message(self):
        for args in (["delete-paragraph", "--on", ""], ["retag", "--on", "", "--style", "Body 1"],
                     ["insert", "--after", "", "--text", "x"], ["format", "--on", "", "--bold"],
                     ["comment", "add", "--on", "", "--text", "x"]):
            with self.subTest(command=args[0]):
                msg, _ = cli(*args, "--write", self.path)
                self.assertIn("empty search text", msg)
                self.untouched()

    def test_comment_reply_and_delete_refuse_it(self):
        for args in (["comment", "reply", "--on", "", "--text", "x"], ["comment", "delete", "--on", ""]):
            with self.subTest(command=args[1]):
                msg, _ = cli(*args, "--write", self.path)
                self.assertIn("empty search text", msg)
                self.untouched()

    def test_a_plan_entry_names_which_entry(self):
        plan = os.path.join(self.tmp.name, "p.json")
        with open(plan, "w") as fh:
            json.dump([{"find": "Body", "replace": "B"}, {"find": "", "replace": "X", "all": True}], fh)
        msg, _ = cli("plan", plan, "--write", self.path)
        self.assertIn("edit 2", msg)
        self.assertIn("empty search text", msg)
        self.untouched()

    def test_a_plan_entry_must_hold_strings(self):
        plan = os.path.join(self.tmp.name, "p.json")
        for entry, word in (({"find": 7, "replace": "x"}, "find"), ({"find": "a", "replace": None}, "replace"),
                            ({"find": ["a"], "replace": "x"}, "find")):
            with self.subTest(entry=entry):
                with open(plan, "w") as fh:
                    json.dump([entry], fh)
                msg, _ = cli("plan", plan, self.path)
                self.assertIn("edit 1", msg)
                self.assertIn(f"`{word}` must be a string", msg)

    def test_a_single_space_is_text(self):
        msg, out = cli("find", " ", self.path)
        self.assertIsNone(msg)
        self.assertIn("match(es)", out)

    def test_zero_width_patterns_that_mean_something_still_work(self):
        for pattern in ("^", "$", r"\b", r"(?=Body)"):
            with self.subTest(pattern=pattern):
                msg, out = cli("replace", "--regex", "-f", pattern, "-r", "> ", "--occurrence", "1",
                               self.path)
                self.assertIsNone(msg)
                self.assertIn("1 replacement(s)", out)

    def test_inserting_at_the_start_of_a_paragraph_with_a_regex_works_end_to_end(self):
        msg, _ = cli("replace", "--regex", "-f", "^Body$", "-r", "Body: ", "--write", "--no-backup", self.path)
        self.assertIsNone(msg)
        self.assertIn("\nBody:\n", P.render_markdown(None, P.PagesDoc(self.path).all_paragraphs()))


class FootnoteMarksAreNotLineBreaks(unittest.TestCase):
    """The search view turned every \\x0e into a newline, so ^ and $ also matched at a
    footnote reference in the middle of a sentence (CLAUDE.md rule 5)."""

    def setUp(self):
        from fixture import write_pages, table, F_PARA_TBL, F_ATTACHMENTS
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = write_pages(os.path.join(self.tmp.name, "f.pages"),
                                "Claim\x0e continues.\nNext\x0eSection\n",
                                {F_PARA_TBL: table([(0, 1)]), F_ATTACHMENTS: table([(5, 999)])})

    def starts_and_ends(self, pattern):
        doc = E.Document(self.path)
        doc.select("body")
        return [m[0] for m in E.matches(doc, pattern, False, True, None)]

    def test_line_starts_skip_the_footnote_but_not_a_section_break(self):
        self.assertEqual(self.starts_and_ends("^"), [0, 18, 23, 31])   # 23: after a section break

    def test_line_ends_too(self):
        self.assertEqual(self.starts_and_ends("$"), [17, 22, 30, 31])

    def test_a_replacement_through_the_cli(self):
        msg, _ = cli("replace", "--regex", "-f", "^ continues", "-r", "x", "--all", self.path)
        self.assertIn("no match", msg)


if __name__ == "__main__":
    unittest.main()
