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


class ExtractLinks(unittest.TestCase):
    def run_reader(self, name):
        out = io.StringIO()
        with redirect_stdout(out):
            P.main(["--links", os.path.join(HERE, "samples", name)])
        return out.getvalue()

    def test_lists_text_and_url(self):
        out = self.run_reader("formatting.pages")
        self.assertIn("1 link(s)", out)
        self.assertIn("“aliquip”  http://google.de", out)

    def test_several_links(self):
        out = self.run_reader("sample-content.pages")
        self.assertIn("2 link(s)", out)
        self.assertIn("https://lennarthennigs.de/dtw/10", out)

    def test_none(self):
        self.assertEqual(self.run_reader("kitchen-sink.pages"), "no links\n")

    def test_same_as_t_links(self):
        out = io.StringIO()
        with redirect_stdout(out):
            P.main(["-t", "links", os.path.join(HERE, "samples", "formatting.pages")])
        self.assertEqual(out.getvalue(), self.run_reader("formatting.pages"))


class MessagesNotTracebacks(Base):
    """Each of these used to end in a traceback."""

    def exits_with(self, fn, *words):
        with self.assertRaises(SystemExit) as cm, redirect_stdout(io.StringIO()):
            fn()
        msg = str(cm.exception.code)
        for w in words:
            self.assertIn(w, msg)
        return msg

    def bad_packages(self):
        import zipfile
        nodoc = os.path.join(self.tmp.name, "nodoc.pages")
        with zipfile.ZipFile(nodoc, "w") as z:
            z.writestr("Metadata/x.txt", "hi")
        garbage = os.path.join(self.tmp.name, "garbage.pages")
        with zipfile.ZipFile(garbage, "w") as z:
            z.writestr("Index/Document.iwa", b"\x00\x01garbage")
        folder = os.path.join(self.tmp.name, "folder.pages")
        os.mkdir(folder)
        notzip = os.path.join(self.tmp.name, "notzip.pages")
        with open(notzip, "w") as fh:
            fh.write("hello")
        return {"missing": os.path.join(self.tmp.name, "nope.pages"), "nodoc": nodoc,
                "garbage": garbage, "folder": folder, "notzip": notzip}

    def test_reader_on_unusable_packages(self):
        for kind, path in self.bad_packages().items():
            with self.subTest(kind=kind):
                self.exits_with(lambda: P.PagesDoc(path), os.path.basename(path))

    def test_editor_on_unusable_packages(self):
        for kind, path in self.bad_packages().items():
            with self.subTest(kind=kind):
                self.exits_with(lambda: E.Document(path), os.path.basename(path))

    def test_the_command_line_of_both_tools(self):
        for kind, path in self.bad_packages().items():
            with self.subTest(kind=kind):
                self.exits_with(lambda: P.main([path]))
                self.exits_with(lambda: E.main(["find", "x", path]))

    def test_an_invalid_regex(self):
        msg, _ = cli("find", "--regex", "(", self.path)
        self.assertIn("regular expression", msg)

    def test_an_invalid_group_reference(self):
        msg, _ = cli("replace", "--regex", "-f", "(Body)", "-r", "\\2", self.path)
        self.assertIn("replacement", msg)

    def test_a_missing_find_file(self):
        msg, _ = cli("replace", "--find-file", "/nonexistent/f.txt", "-r", "x", self.path)
        self.assertIn("/nonexistent/f.txt", msg)

    def test_a_missing_replace_file(self):
        msg, _ = cli("replace", "-f", "Body", "--replace-file", "/nonexistent/r.txt", self.path)
        self.assertIn("/nonexistent/r.txt", msg)

    def test_a_missing_markdown_file(self):
        msg, _ = cli("import", "/nonexistent/i.md", "--after", "Body", self.path)
        self.assertIn("/nonexistent/i.md", msg)


if __name__ == "__main__":
    unittest.main()


class NoteNaming(unittest.TestCase):
    PATH = os.path.join(HERE, "samples", "formatting.pages")

    def reader(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            P.main([*args, self.PATH])
        return out.getvalue()

    def test_the_old_option_name_still_works(self):
        self.assertEqual(self.reader("--sidenotes", "only"), self.reader("--notes", "only"))
        self.assertNotEqual(self.reader("--notes", "skip"), self.reader("--notes", "only"))

    def test_json_paragraphs_say_which_note_they_belong_to(self):
        import json
        paras = json.loads(self.reader("-t", "json", "--notes", "only"))
        paras = paras["paragraphs"] if isinstance(paras, dict) else paras
        self.assertTrue(paras and all(p["note"] is not None and "sidenote" not in p for p in paras))


if __name__ == "__main__":
    unittest.main()
