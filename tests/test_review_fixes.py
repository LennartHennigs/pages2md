"""Smaller correctness bugs from the code review (section 2), one class each."""
import io, json, os, shutil, sys, tempfile, unittest
from argparse import Namespace
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fixture import (write_pages, table, storage, T_STORAGE, F_PARA_TBL)
import pages_edit as E
import pages2md as P

KITCHEN = os.path.join(HERE, "samples", "kitchen-sink.pages")
BODY = "A long enough body paragraph.\nSecond paragraph.\n"
CAPTION = "Figure caption text"


def run(main, *args):
    out = io.StringIO()
    with mock.patch.object(E, "pages_has_open", return_value=False), \
            redirect_stdout(out):
        main(list(args))
    return out.getvalue()


class WithTmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def captioned(self, caption=CAPTION, name="t.pages"):
        """A document whose body is followed by an unanchored caption storage."""
        return write_pages(os.path.join(self.tmp.name, name), BODY,
                           {F_PARA_TBL: table([(0, 1)])},
                           extra=[(1300, T_STORAGE, storage(caption, {}))])


class FindInUnanchoredStorage(WithTmp):
    """`find` with a page index crashed on a caption: page_of(bounds, None)."""

    def test_page_of_has_no_page_for_no_anchor(self):
        self.assertIsNone(P.page_of([0, 10], None))
        self.assertEqual(P.page_of([0, 10], 12), 2)
        self.assertIsNone(P.page_of(None, 5))

    def test_find_in_a_caption_with_a_page_index(self):
        path = self.captioned()
        with open(os.path.join(self.tmp.name, P.INDEX_NAME), "w") as fh:
            json.dump({"document": "t.pages", "bounds": [0, 10],
                       "fingerprint": P.text_fingerprint(path)}, fh)
        out = run(E.main, "find", "Figure", path)
        self.assertIn("1 match(es)", out)
        self.assertIn("[other1]", out)
        self.assertNotIn("p.", out)           # a caption has no page of its own


class FingerprintsAgree(WithTmp):
    """The reader hashed body + notes, the editor every storage."""

    def test_same_value_with_a_caption(self):
        path = self.captioned()
        self.assertEqual(P.text_fingerprint(path),
                         E.fingerprint(E.Document(path)))

    def test_a_caption_edit_changes_it(self):
        a = P.text_fingerprint(self.captioned("One caption", "a.pages"))
        b = P.text_fingerprint(self.captioned("Other caption", "b.pages"))
        self.assertNotEqual(a, b)


class Emphasis(unittest.TestCase):
    def render(self, raw, runs, struck=()):
        para = dict(raw=raw, runs=runs, struck=list(struck), semantic="Body 1",
                    list=None, list_kind=None, note=None, footnote=None, ref_nos=[],
                    offset=0, style="Body")
        return P.render_markdown(None, [para])

    def test_bold_italic_keeps_both(self):
        self.assertEqual(self.render("hello", [(0, 5, True, True, False)]),
                         "***hello***\n")

    def test_adjacent_runs_merge(self):
        self.assertEqual(self.render("foobar", [(0, 3, True, False, False),
                                                (3, 6, True, False, False)]),
                         "**foobar**\n")

    def test_runs_split_by_text_do_not_merge(self):
        self.assertEqual(self.render("ab cd", [(0, 2, True, False, False),
                                               (3, 5, True, False, False)]),
                         "**ab** **cd**\n")

    def test_different_styles_stay_separate(self):
        self.assertEqual(self.render("foobar", [(0, 3, True, False, False),
                                                (3, 6, False, True, False)]),
                         "**foo***bar*\n")

    def test_whitespace_stays_outside_the_markers(self):
        self.assertEqual(self.render("a bold b", [(1, 7, True, False, False)]),
                         "a **bold** b\n")

    def test_marked_deletion_keeps_emphasis(self):
        out = self.render("Hello old world", [(0, 5, True, False, False)],
                          struck=[(6, 9)])
        self.assertEqual(out, "**Hello** ~~old~~ world\n")

    def test_emphasis_inside_a_struck_span(self):
        out = self.render("Hello old world", [(6, 9, False, True, False)],
                          struck=[(6, 9)])
        self.assertEqual(out, "Hello ~~*old*~~ world\n")

    def test_struck_span_inside_emphasis(self):
        out = self.render("one two three", [(0, 13, True, False, False)],
                          struck=[(4, 7)])
        self.assertEqual(out, "**one ~~two~~ three**\n")


class ImporterMarkup(unittest.TestCase):
    def test_snake_case_is_left_alone(self):
        self.assertEqual(E.strip_markup("call my_var_name now"),
                         ("call my_var_name now", []))

    def test_underscore_emphasis_still_works(self):
        self.assertEqual(E.strip_markup("an _italic_ word"),
                         ("an italic word", [(3, 9, False, True)]))

    def test_lone_asterisks_are_not_emphasis(self):
        self.assertEqual(E.strip_markup("2 * 3 * 4"), ("2 * 3 * 4", []))

    def test_bold_and_italic(self):
        self.assertEqual(E.strip_markup("**b** and *i*"),
                         ("b and i", [(0, 1, True, False), (6, 7, False, True)]))

    def test_underscores_inside_bold(self):
        self.assertEqual(E.strip_markup("**snake_case**"),
                         ("snake_case", [(0, 10, True, False)]))


class IndexScript(unittest.TestCase):
    """The document path went into AppleScript unescaped."""

    def test_quotes_and_backslashes_are_escaped(self):
        self.assertEqual(P.applescript_quote('a "b" \\c'), 'a \\"b\\" \\\\c')

    def test_script_contains_the_escaped_path(self):
        script = P.index_script('/tmp/my "draft" \\v2.pages')
        self.assertIn('open POSIX file "/tmp/my \\"draft\\" \\\\v2.pages"', script)

    def test_script_is_the_same_for_plain_paths(self):
        self.assertIn('open POSIX file "/tmp/a.pages"', P.index_script("/tmp/a.pages"))


class RevertIsAtomic(WithTmp):
    def setUp(self):
        super().setUp()
        self.path = write_pages(os.path.join(self.tmp.name, "t.pages"), BODY,
                                {F_PARA_TBL: table([(0, 1)])})
        E.save_config(self.path, {"vcs": True})
        E.vcs_snapshot(self.path, "Baseline")
        with open(self.path, "rb") as fh:
            self.v1 = fh.read()
        doc = E.Document(self.path)
        doc.apply([(0, 1, "THE")])
        doc.save(self.path)
        E.vcs_snapshot(self.path, "Edit")
        with open(self.path, "rb") as fh:
            self.v2 = fh.read()
        self.assertNotEqual(self.v1, self.v2)

    def revert(self):
        with mock.patch.object(E, "pages_has_open", return_value=False), \
                redirect_stdout(io.StringIO()):
            E.cmd_revert(Namespace(file=self.path, ref="HEAD~1"))

    def read(self):
        with open(self.path, "rb") as fh:
            return fh.read()

    def test_revert_restores_the_old_version(self):
        self.revert()
        self.assertEqual(self.read(), self.v1)

    def test_a_relative_ref_means_what_it_meant_before_the_safety_snapshot(self):
        """`revert` snapshots an externally edited file first; that commit must not shift HEAD~1."""
        doc = E.Document(self.path)
        doc.apply([(0, 3, "XYZ")])
        doc.save(self.path)                       # edited outside pages_edit: not in history
        self.revert()
        self.assertEqual(self.read(), self.v1)

    def test_an_unknown_ref_changes_nothing_and_adds_no_snapshot(self):
        root = E.vcs_root(self.path)
        before = E.git(root, "rev-list", "--count", "HEAD").stdout
        with mock.patch.object(E, "pages_has_open", return_value=False), \
                redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            E.cmd_revert(Namespace(file=self.path, ref="nope"))
        self.assertEqual(self.read(), self.v2)
        self.assertEqual(E.git(root, "rev-list", "--count", "HEAD").stdout, before)

    def test_failure_while_writing_leaves_the_document_alone(self):
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.revert()
        self.assertEqual(self.read(), self.v2)
        self.assertFalse(os.path.exists(self.path + ".tmp"))


@unittest.skipUnless(os.path.exists(KITCHEN), "kitchen-sink sample not present")
class CommentsInNotes(WithTmp):
    """locate_comment stopped at the first storage that had any comment, so a
    comment in a note was unreachable whenever the body had one too."""

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp.name, "k.pages")
        shutil.copy(KITCHEN, self.path)

    def threads(self):
        return {c["handle"]: [m["text"].strip() for m in c["thread"]]
                for c in P.PagesDoc(self.path).comments()}

    def test_baseline(self):
        self.assertEqual(self.threads(), {"body": ["Love this", "thx!"],
                                          "note1": ["footnote comment"]})

    def test_reply_to_the_note_comment(self):
        run(E.main, "comment", "reply", self.path, "--on", "about",
            "--text", "agreed", "--write", "--no-backup")
        self.assertEqual(self.threads()["note1"], ["footnote comment", "agreed"])
        self.assertEqual(self.threads()["body"], ["Love this", "thx!"])

    def test_reply_to_the_body_comment_still_works(self):
        run(E.main, "comment", "reply", self.path, "--on", "Body",
            "--text", "ok", "--write", "--no-backup")
        self.assertEqual(self.threads()["body"], ["Love this", "thx!", "ok"])

    def test_delete_the_note_comment_only(self):
        run(E.main, "comment", "delete", self.path, "--on", "about",
            "--write", "--no-backup")
        self.assertEqual(self.threads(), {"body": ["Love this", "thx!"]})

    def test_ambiguous_anchor_names_the_storages(self):
        # body and note1 both have a comment: --at 7 exists only in the note,
        # --at 33 only in the body, so each is unambiguous
        run(E.main, "comment", "delete", self.path, "--at", "7",
            "--write", "--no-backup")
        self.assertNotIn("note1", self.threads())

    def test_where_limits_the_search(self):
        with self.assertRaises(SystemExit):
            run(E.main, "comment", "delete", self.path, "--on", "about",
                "--where", "body", "--write", "--no-backup")

    def test_no_match_anywhere(self):
        with self.assertRaises(SystemExit) as cm:
            run(E.main, "comment", "delete", self.path, "--on", "nonexistent",
                "--write", "--no-backup")
        self.assertIn("nonexistent", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
