"""Hostile or damaged input: loops, odd offsets, messages instead of tracebacks."""
import io, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pages_edit as E
import pages2md as P
from iwa_codec import tokenize, emit, write_varint

SAMPLE = os.path.join(HERE, "samples", "formatting.pages")


def cli(*args):
    out = io.StringIO()
    try:
        with mock.patch.object(E, "pages_has_open", return_value=False), redirect_stdout(out):
            E.main(list(args))
    except SystemExit as exc:
        return str(exc.code), out.getvalue()
    return None, out.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "d.pages")
        shutil.copy(SAMPLE, self.path)


class CyclicCommentChain(Base):
    """A comment whose `next` points back at the thread hangs a `while` loop."""

    def make_cycle(self):
        doc = E.Document(self.path)
        _a, _n, ref = doc.select("body").comment_table()[0]
        head = E.read_varint(E.parse_fields_of(
            doc.arcs[doc.by_id[ref][0]][1][doc.by_id[ref][1]][1])[1][0], 1)[0]
        chain = doc.thread_ids(head)
        self.assertEqual(len(chain), 2)
        ai, mi, _t = doc.by_id[chain[-1]]
        toks = [(n, w, v) for n, w, v in tokenize(doc.arcs[ai][1][mi][1]) if n != E.C_NEXT]
        toks.append((E.C_NEXT, 2, emit([(1, 0, write_varint(head))])))
        toks.sort(key=lambda t: t[0])
        doc.arcs[ai][1][mi][1] = emit(toks)
        doc.save(self.path)

    def test_the_reader_terminates(self):
        self.make_cycle()
        threads = [c for c in P.PagesDoc(self.path).comments()]
        self.assertEqual(len(threads), 2)

    def test_the_editor_terminates(self):
        self.make_cycle()
        doc = E.Document(self.path)
        _a, _n, ref = doc.select("body").comment_table()[0]
        head = E.read_varint(E.parse_fields_of(
            doc.arcs[doc.by_id[ref][0]][1][doc.by_id[ref][1]][1])[1][0], 1)[0]
        self.assertEqual(len(doc.thread_ids(head)), 2)

    def test_a_reply_to_a_looping_thread_terminates(self):
        self.make_cycle()
        msg, _ = cli("comment", "reply", "--at", "40", "--text", "x", self.path)
        self.assertIsNone(msg)


class ZeroWidthMatchAtTheEnd(unittest.TestCase):
    def test_maps_to_the_raw_end_when_tracked_deletions_exist(self):
        from fixture import write_pages, table, F_PARA_TBL, F_DELETIONS
        with tempfile.TemporaryDirectory() as tmp:
            path = write_pages(os.path.join(tmp, "t.pages"), "aa Bold word",
                               {F_PARA_TBL: table([(0, 1)]),
                                F_DELETIONS: table([(0, None), (3, 700), (7, None)])})
            doc = E.Document(path)
            doc.select("body")
            hits = E.matches(doc, "$", False, True, None)
            self.assertEqual([(h[0], h[1]) for h in hits], [(12, 12)])


class CommentOverALineBreak(unittest.TestCase):
    def test_the_message_is_about_comments_not_replacement(self):
        from fixture import write_pages, table, F_PARA_TBL
        with tempfile.TemporaryDirectory() as tmp:
            path = write_pages(os.path.join(tmp, "t.pages"), "one\u2028two\n",
                               {F_PARA_TBL: table([(0, 1)])})
            msg, _ = cli("comment", "add", "--on", "one two", "--text", "x", path)
            self.assertIn("comment", msg)
            self.assertNotIn("replacing", msg)
            self.assertNotIn("delete", msg)


if __name__ == "__main__":
    unittest.main()


class StyleNames(unittest.TestCase):
    STYLES = {"Body 1": 1, "Heading 1": 2, "Heading 2": 3, "Table Style 1": 4, "Table Style 2": 5}

    def test_exact_case_and_missing_number(self):
        f = E.find_style
        self.assertEqual(f(self.STYLES, "Body 1"), "Body 1")
        self.assertEqual(f(self.STYLES, "body 1"), "Body 1")
        self.assertEqual(f(self.STYLES, "Body"), "Body 1")

    def test_ambiguous_or_unknown_is_none(self):
        self.assertIsNone(E.find_style(self.STYLES, "Heading"))
        self.assertIsNone(E.find_style(self.STYLES, "Table Style"))
        self.assertIsNone(E.find_style(self.STYLES, "Nope"))

    def test_insert_accepts_body_on_a_document_with_body_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "d.pages")
            shutil.copy(SAMPLE, path)
            msg, out = cli("insert", "--after", "Love", "--text", "x", "--style", "Body", path)
            self.assertNotIn("unknown style", msg or "")


class WhereOnStructuralCommands(Base):
    def test_is_not_accepted_where_it_would_be_ignored(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                mock.patch("sys.stderr", io.StringIO()):
            E.main(["delete-paragraph", "--on", "Body", "--where", "notes", self.path])


if __name__ == "__main__":
    unittest.main()
