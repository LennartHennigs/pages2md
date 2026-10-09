"""Commands that had no tests: sections (`--in`), page ranges and the page index, plans,
config and history. All on copies of `formatting.pages`; Pages and `osascript` are faked.
"""
import io, json, os, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pages_edit as E
import pages2md as P

SAMPLE = os.path.join(HERE, "samples", "formatting.pages")


def edit_cli(*args):
    """Run pages_edit; -> (exit message or None, stdout)."""
    out = io.StringIO()
    try:
        with mock.patch.object(E, "pages_has_open", return_value=False), redirect_stdout(out):
            E.main(list(args))
    except SystemExit as exc:
        return str(exc.code), out.getvalue()
    return None, out.getvalue()


def read_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        P.main(list(args))
    return out.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "d.pages")
        shutil.copy(SAMPLE, self.path)
        self.raw = P.PagesDoc(self.path)._raw_text()

    def md(self):
        return P.render_markdown(None, P.PagesDoc(self.path).all_paragraphs())

    def make_index(self, cuts):
        """Build the page index as Pages would: three pages cut at `cuts`."""
        edges = [0, *cuts, len(self.raw)]
        pages = "<<<PG>>>".join(self.raw[a:b] for a, b in zip(edges, edges[1:]))
        fake = mock.Mock(returncode=0, stdout=pages, stderr="")
        with mock.patch.object(P.subprocess, "run", return_value=fake), \
                mock.patch.object(P, "pages_has_open", return_value=False):
            return P.build_index(self.path, self.raw)


class Sections(Case):
    def test_a_section_runs_to_the_next_heading_of_the_same_or_higher_level(self):
        # outline: Heading 1 @7, Heading 2 @18, Heading 3 @29, Heading 2 @76, Headline @849
        self.assertEqual(P.section_range(self.path, "Heading 3", len(self.raw)), ("Heading 3", 29, 76))
        self.assertEqual(P.section_range(self.path, "Heading 1", len(self.raw)), ("Heading 1", 7, 849))

    def test_a_title_ends_at_the_next_heading(self):
        self.assertEqual(P.section_range(self.path, "Titel", len(self.raw)), ("Titel", 0, 7))

    def test_an_unknown_heading_says_so(self):
        with self.assertRaises(SystemExit) as cm:
            P.section_range(self.path, "no such heading", len(self.raw))
        self.assertIn("no heading matches", str(cm.exception))

    def test_a_name_that_matches_two_headings_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            P.section_range(self.path, "Heading 2", len(self.raw))
        self.assertIn("matches 2 headings", str(cm.exception))

    def test_the_match_ignores_case_and_takes_a_substring(self):
        self.assertEqual(P.section_range(self.path, "heading 3", len(self.raw))[0], "Heading 3")
        self.assertEqual(P.section_range(self.path, "ding 3", len(self.raw))[0], "Heading 3")

    def test_reading_one_section(self):
        out = read_cli("--in", "Heading 3", self.path)
        self.assertIn("Heading 3", out)
        self.assertNotIn("Headline", out)

    def test_an_edit_scope_confines_the_match(self):
        everywhere, _ = edit_cli("find", "Lorem", self.path)
        msg, out = edit_cli("find", "Lorem", "--in", "Heading 3", self.path)
        self.assertIsNone(everywhere)
        self.assertIsNone(msg)
        self.assertIn("scope:", out)

    def test_in_and_page_together_are_refused(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err), self.assertRaises(SystemExit) as cm:
            P.main(["--in", "Heading 3", "--page", "1", self.path])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("not both", err.getvalue())


class PageIndex(Case):
    def test_building_the_index_aligns_pages_to_the_text(self):
        bounds, pages, aligned = self.make_index([849, 2047])
        self.assertEqual((bounds, pages), ([0, 847, 2045], 3))
        self.assertGreaterEqual(aligned, len(self.raw) - 2)     # a trailing newline is noise

    def test_the_index_is_cached_next_to_the_document_and_named_after_it(self):
        self.make_index([849, 2047])
        with open(P.index_path(self.path), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual((data["document"], data["pages"], data["bounds"]),
                         ("d.pages", 3, [0, 847, 2045]))
        self.assertEqual(data["fingerprint"], P.text_fingerprint(self.path))
        self.assertEqual(P.load_index(self.path), [0, 847, 2045])

    def test_page_of(self):
        self.assertEqual([P.page_of([0, 847, 2045], o) for o in (0, 846, 847, 2044, 2045, 5000)],
                         [1, 1, 2, 2, 3, 3])
        self.assertIsNone(P.page_of(None, 5))
        self.assertIsNone(P.page_of([0, 10], None))

    def test_page_ranges(self):
        self.make_index([849, 2047])
        n = len(self.raw)
        self.assertEqual(P.page_range(self.path, "2", n), (847, 2045))
        self.assertEqual(P.page_range(self.path, "2-3", n), (847, n))
        self.assertEqual(P.page_range(self.path, " 1 - 2 ", n), (0, 2045))

    def test_bad_page_specs(self):
        self.make_index([849, 2047])
        for spec, word in (("0", "out of range"), ("4", "out of range"), ("3-2", "out of range"),
                           ("two", "N or N-M")):
            with self.subTest(spec=spec), self.assertRaises(SystemExit) as cm:
                P.page_range(self.path, spec, len(self.raw))
            self.assertIn(word, str(cm.exception))

    def test_without_an_index_page_says_to_build_one(self):
        with self.assertRaises(SystemExit) as cm:
            P.page_range(self.path, "1", len(self.raw))
        self.assertIn("no page index", str(cm.exception))

    def test_reading_one_page(self):
        self.make_index([849, 2047])
        out = read_cli("--page", "2", self.path)
        self.assertIn("Headline", out)
        self.assertNotIn("Heading 3", out)

    def test_an_index_built_for_another_document_is_not_used(self):
        self.make_index([849, 2047])
        other = os.path.join(self.tmp.name, "other.pages")
        shutil.copy(SAMPLE, other)
        load_index = P.load_index
        load_index._warned = False
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertIsNone(load_index(other))
        self.assertIn("was built for", err.getvalue())

    def test_an_unreadable_index_is_ignored_with_a_warning(self):
        with open(P.index_path(self.path), "w") as fh:
            fh.write("{not json")
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            self.assertIsNone(P.load_index(self.path))
        self.assertIn("unreadable", err.getvalue())

    def test_pages_failing_is_reported(self):
        fake = mock.Mock(returncode=1, stdout="", stderr="no Pages")
        with mock.patch.object(P.subprocess, "run", return_value=fake), \
                mock.patch.object(P, "pages_has_open", return_value=False), \
                self.assertRaises(SystemExit) as cm:
            P.build_index(self.path, self.raw)
        self.assertIn("no Pages", str(cm.exception))

    def test_an_open_document_is_not_indexed(self):
        with mock.patch.object(P, "pages_has_open", return_value=True), \
                self.assertRaises(SystemExit) as cm:
            P.build_index(self.path, self.raw)
        self.assertIn("open", str(cm.exception))

    def test_the_index_command_and_the_outline_with_pages(self):
        fake = mock.Mock(returncode=0, stdout="<<<PG>>>".join(
            [self.raw[:849], self.raw[849:2047], self.raw[2047:]]), stderr="")
        with mock.patch.object(P.subprocess, "run", return_value=fake), \
                mock.patch.object(E, "build_index", P.build_index):
            msg, out = edit_cli("index", self.path)
        self.assertIsNone(msg, out)
        self.assertIn("indexed 3 pages", out)
        msg, out = edit_cli("outline", self.path)
        self.assertIn("p.2", out)
        self.assertIn("headings", out)

    def test_an_edit_scoped_to_a_page(self):
        self.make_index([849, 2047])
        _m, on_page_1 = edit_cli("find", "Headline", "--page", "1", self.path)
        _m, on_page_2 = edit_cli("find", "Headline", "--page", "2", self.path)
        self.assertIn("0 match", on_page_1)
        self.assertIn("1 match", on_page_2)

    def test_a_page_scope_in_a_plan(self):
        self.make_index([849, 2047])
        plan = os.path.join(self.tmp.name, "p.json")
        with open(plan, "w") as fh:
            json.dump([{"find": "Headline", "replace": "Kopf", "page": 2, "all": True}], fh)
        msg, out = edit_cli("plan", plan, self.path)
        self.assertIsNone(msg, out)
        self.assertIn("page 2", out)


class Plans(Case):
    def plan(self, entries, **top):
        path = os.path.join(self.tmp.name, "plan.json")
        with open(path, "w") as fh:
            json.dump({"edits": entries, **top} if top else entries, fh)
        return path

    def test_a_dry_run_changes_nothing(self):
        before = open(self.path, "rb").read()
        msg, out = edit_cli("plan", self.plan([{"find": "Titel", "replace": "Title"}]), self.path)
        self.assertIsNone(msg)
        self.assertIn("dry run", out)
        self.assertEqual(open(self.path, "rb").read(), before)

    def test_write_applies_every_entry_against_one_snapshot(self):
        plan = self.plan([{"find": "Titel", "replace": "Title"},
                          {"find": "Heading 3", "replace": "Third"}])
        msg, out = edit_cli("plan", plan, "--write", self.path)
        self.assertIsNone(msg, out)
        self.assertIn("2 replacement(s)", out)
        md = self.md()
        self.assertIn("Title", md)
        self.assertIn("Third", md)
        self.assertNotIn("Heading 3", md)
        self.assertTrue(os.path.exists(self.path + ".bak"))

    def test_an_edit_can_be_scoped_to_a_section(self):
        lo, hi = P.section_range(self.path, "Heading 3", len(self.raw))[1:]
        plan = self.plan([{"find": "Is", "replace": "IS", "in": "Heading 3", "all": True}])
        msg, out = edit_cli("plan", plan, "--write", "--no-backup", self.path)
        self.assertIsNone(msg, out)
        raw = P.PagesDoc(self.path)._raw_text()
        self.assertIn("IS", raw[lo:hi])
        self.assertNotIn("IS", raw[:lo] + raw[hi:])      # untouched elsewhere

    def test_a_fingerprint_pins_the_plan_to_the_text(self):
        fp = E.fingerprint(E.Document(self.path))
        ok = self.plan([{"find": "Titel", "replace": "Title"}], fingerprint=fp)
        msg, _ = edit_cli("plan", ok, self.path)
        self.assertIsNone(msg)
        stale = self.plan([{"find": "Titel", "replace": "Title"}], fingerprint="0" * 16)
        msg, _ = edit_cli("plan", stale, self.path)
        self.assertIn("has changed since", msg)

    def test_overlapping_edits_are_refused(self):
        plan = self.plan([{"find": "Heading 3", "replace": "x"}, {"find": "ing 3", "replace": "y"}])
        msg, _ = edit_cli("plan", plan, self.path)
        self.assertIn("overlap", msg)

    def test_unknown_keys_and_empty_plans_are_refused(self):
        msg, _ = edit_cli("plan", self.plan([{"find": "a", "replace": "b", "bogus": 1}]), self.path)
        self.assertIn("unknown key", msg)
        msg, _ = edit_cli("plan", self.plan([]), self.path)
        self.assertIn("no edits", msg)

    def test_a_plan_that_is_not_json(self):
        path = os.path.join(self.tmp.name, "bad.json")
        with open(path, "w") as fh:
            fh.write("[{")
        msg, _ = edit_cli("plan", path, self.path)
        self.assertIn("not valid JSON", msg)

    def test_regex_and_occurrence_and_tracked(self):
        plan = self.plan([{"find": r"Head\w+ 2", "replace": "Second", "regex": True, "occurrence": 2}])
        msg, out = edit_cli("plan", plan, "--write", "--no-backup", "--track", self.path)
        self.assertIsNone(msg, out)
        self.assertIn("tracked change", out)


class ConfigAndHistory(Case):
    def test_config_round_trip(self):
        msg, out = edit_cli("config", "--track", "on", self.path)
        self.assertIsNone(msg)
        self.assertTrue(json.loads(out[out.index("{"):])["track"])
        self.assertTrue(E.load_config(self.path)["track"])
        edit_cli("config", "--track", "off", self.path)
        self.assertFalse(E.load_config(self.path)["track"])

    def test_without_a_config_file_the_defaults_apply(self):
        self.assertEqual(E.load_config(self.path), {"vcs": False})

    def test_tracking_on_makes_replace_tracked(self):
        edit_cli("config", "--track", "on", self.path)
        msg, out = edit_cli("replace", "-f", "Titel", "-r", "Title", self.path)
        self.assertIn("tracked change", out)

    def test_history_needs_history(self):
        msg, _ = edit_cli("history", self.path)
        self.assertIn("no history yet", msg)
        msg, _ = edit_cli("revert", "HEAD", self.path)
        self.assertIn("no history yet", msg)

    def test_edits_are_committed_and_listed(self):
        edit_cli("config", "--vcs", "on", self.path)
        msg, out = edit_cli("replace", "-f", "Titel", "-r", "Title", "--write", self.path)
        self.assertIsNone(msg, out)
        self.assertIn("committed:", out)
        self.assertFalse(os.path.exists(self.path + ".bak"))      # history replaces the .bak
        msg, out = edit_cli("history", self.path)
        self.assertIn("Replace", out) if "Replace" in out else self.assertIn("Titel", out)
        self.assertIn("Baseline", out)

    def test_revert_goes_back_and_is_itself_recorded(self):
        edit_cli("config", "--vcs", "on", self.path)
        original = open(self.path, "rb").read()
        edit_cli("replace", "-f", "Titel", "-r", "Title", "--write", self.path)
        self.assertNotEqual(open(self.path, "rb").read(), original)
        msg, out = edit_cli("revert", "HEAD~1", self.path)
        self.assertIsNone(msg, out)
        self.assertEqual(open(self.path, "rb").read(), original)
        _m, history = edit_cli("history", self.path)
        self.assertIn("Revert to HEAD~1", history)

    def test_turning_history_off_keeps_it(self):
        edit_cli("config", "--vcs", "on", self.path)
        msg, out = edit_cli("config", "--vcs", "off", self.path)
        self.assertIn("existing history is kept", out)
        self.assertTrue(os.path.isdir(E.vcs_root(self.path)))

    def test_the_fingerprint_command_and_the_expect_guard(self):
        msg, out = edit_cli("fingerprint", self.path)
        fp = out.strip()
        self.assertEqual(fp, E.fingerprint(E.Document(self.path)))
        msg, _ = edit_cli("replace", "-f", "Titel", "-r", "T", "--expect", fp, self.path)
        self.assertIsNone(msg)
        msg, _ = edit_cli("replace", "-f", "Titel", "-r", "T", "--expect", "bad", self.path)
        self.assertIn("fingerprint", msg)


if __name__ == "__main__":
    unittest.main()
