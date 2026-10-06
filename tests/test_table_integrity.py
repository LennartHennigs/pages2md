"""Attribute tables stay well-formed: sorted, one entry per index, inside the text.

Replacing a whole annotated word (a bold run, a comment anchor, a language run) with
nothing collapsed the run to zero width and left two entries at one index. Pages never
writes that, the reader sorted `(index, ref)` tuples and crashed comparing None with an
int, and the post-write check re-read with the editor only, so it said "re-read OK".
Found by random edits on the real samples.
"""
import io, os, random, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fixture import (write_pages, table, rows, tables_of, char_style, T_CHAR_STYLE,
                     F_PARA_TBL, F_CHAR_TBL, F_INSERTIONS, F_DELETIONS)
import pages_edit as E
import pages2md as P

BOLD, S1, S2, CHANGE = 900, 1, 2, 700
SAMPLES = os.path.join(HERE, "samples")


def run_cli(*args):
    with mock.patch.object(E, "pages_has_open", return_value=False), \
            redirect_stdout(io.StringIO()):
        E.main(list(args))


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doc(self, text, **fields):
        tables = {F_PARA_TBL: table([(0, S1)])}
        tables.update(fields)
        path = write_pages(os.path.join(self.tmp.name, "t.pages"), text, tables,
                           extra=[(BOLD, T_CHAR_STYLE, char_style(bold=True))])
        return E.Document(path)

    def indexes(self, doc, field):
        return [i for i, _r in rows(doc, field)]


class CollapsedRunsLeaveOneEntryPerIndex(Case):
    TEXT = "aa Bold word\n"                       # "Bold" is 3..7

    def test_a_deleted_bold_word(self):
        doc = self.with_table(F_CHAR_TBL, [(0, None), (3, BOLD), (7, None)])
        doc.apply([(3, 7, "")])
        self.assertEqual(self.indexes(doc, F_CHAR_TBL), sorted(set(self.indexes(doc, F_CHAR_TBL))))
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (3, None)])

    def with_table(self, field, entries):
        tables = {F_PARA_TBL: table([(0, S1)]), field: table(entries)}
        path = write_pages(os.path.join(self.tmp.name, "w.pages"), self.TEXT, tables,
                           extra=[(BOLD, T_CHAR_STYLE, char_style(bold=True))])
        return E.Document(path)

    def test_a_shrunk_bold_word_is_unchanged_behaviour(self):
        doc = self.with_table(F_CHAR_TBL, [(0, None), (3, BOLD), (7, None)])
        doc.apply([(3, 7, "B")])
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (3, BOLD), (4, None)])

    def test_the_later_entry_wins_where_a_run_starts_after_a_deleted_one(self):
        doc = self.with_table(F_CHAR_TBL, [(0, None), (2, None), (3, BOLD), (7, None)])
        doc.apply([(2, 3, "")])                  # delete the space before the run
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (2, BOLD), (6, None)])

    def test_a_tracked_change_fully_replaced_disappears(self):
        doc = self.with_table(F_DELETIONS, [(0, None), (3, CHANGE), (7, None)])
        doc.apply([(3, 7, "")])
        self.assertEqual(E.spans_of(tables_of(doc)[F_DELETIONS]), [])
        self.assertEqual(self.indexes(doc, F_DELETIONS), sorted(set(self.indexes(doc, F_DELETIONS))))

    def test_a_comment_run_over_deleted_text_collapses_cleanly(self):
        doc = self.with_table(E.F_COMMENTS_RUN, [(0, None), (3, 1234), (7, None)])
        doc.apply([(3, 7, "")])
        idx = self.indexes(doc, E.F_COMMENTS_RUN)
        self.assertEqual(idx, sorted(set(idx)))

    def test_paragraph_style_of_a_deleted_paragraph_gives_way_to_the_survivor(self):
        text = "A\nBBBB\nC\n"
        path = write_pages(os.path.join(self.tmp.name, "p.pages"), text,
                           {F_PARA_TBL: table([(0, S1), (2, S2), (7, S1)])})
        doc = E.Document(path)
        doc.apply([(2, 7, "")])                  # B's whole paragraph, newline included
        self.assertEqual(rows(doc, F_PARA_TBL), [(0, S1), (2, S1)])

    def test_edits_that_do_not_collapse_anything_leave_tables_byte_identical(self):
        doc = self.with_table(F_CHAR_TBL, [(0, None), (3, BOLD), (7, None)])
        before = tables_of(doc)[F_CHAR_TBL]
        doc.apply([(10, 11, "x")])               # after every entry
        self.assertEqual(tables_of(doc)[F_CHAR_TBL], before)


class TheReaderToleratesWhatItIsGiven(Case):
    def test_duplicate_entries_do_not_crash_the_reader(self):
        path = write_pages(os.path.join(self.tmp.name, "d.pages"), "aa Bold word\n",
                           {F_PARA_TBL: table([(0, S1)]),
                            F_CHAR_TBL: table([(0, None), (3, BOLD), (3, None)])},
                           extra=[(BOLD, T_CHAR_STYLE, char_style(bold=True))])
        para = P.PagesDoc(path).paragraphs()[0]          # used to raise TypeError
        self.assertEqual(para["runs"], [])               # the later entry (null) wins

    def test_the_last_of_two_entries_wins_in_a_style_lookup(self):
        path = write_pages(os.path.join(self.tmp.name, "d2.pages"), "aa Bold word\n",
                           {F_PARA_TBL: table([(0, S1)]),
                            F_CHAR_TBL: table([(0, None), (3, None), (3, BOLD), (7, None)])},
                           extra=[(BOLD, T_CHAR_STYLE, char_style(bold=True))])
        runs = P.PagesDoc(path).paragraphs()[0]["runs"]
        self.assertEqual([r[:3] for r in runs], [(3, 7, True)])


class SaveChecksWhatItWrites(Case):
    def corrupt(self, doc, field, entries):
        doc.select("body")
        doc._put_field(field, table(entries))

    def test_clean_documents_save(self):
        doc = self.doc("aa Bold word\n")
        doc.save(doc.path)                                # no exception

    def test_duplicate_indexes_are_refused_and_the_file_is_untouched(self):
        doc = self.doc("aa Bold word\n")
        with open(doc.path, "rb") as fh:
            original = fh.read()
        self.corrupt(doc, F_CHAR_TBL, [(0, None), (3, BOLD), (3, None)])
        with self.assertRaises(SystemExit) as cm:
            doc.save(doc.path)
        self.assertIn("duplicate", str(cm.exception))
        with open(doc.path, "rb") as fh:
            self.assertEqual(fh.read(), original)
        self.assertFalse(os.path.exists(doc.path + ".tmp"))

    def test_an_index_past_the_end_of_the_text_is_refused(self):
        doc = self.doc("short\n")
        self.corrupt(doc, F_CHAR_TBL, [(0, None), (99, BOLD)])
        with self.assertRaises(SystemExit) as cm:
            doc.save(doc.path)
        self.assertIn("past the end", str(cm.exception))

    def test_an_index_exactly_at_the_end_is_fine(self):
        doc = self.doc("short\n")                         # 6 characters
        self.corrupt(doc, F_CHAR_TBL, [(0, None), (6, None)])
        doc.save(doc.path)

    def test_unsorted_entries_are_refused(self):
        doc = self.doc("aa Bold word\n")
        doc.select("body")
        doc._put_field(F_CHAR_TBL, table([(5, None), (2, None)]))
        with self.assertRaises(SystemExit) as cm:
            doc.save(doc.path)
        self.assertIn("unsorted", str(cm.exception))

    def test_table_problems_names_the_storage_and_field(self):
        doc = self.doc("aa Bold word\n")
        self.corrupt(doc, F_CHAR_TBL, [(0, None), (3, BOLD), (3, None)])
        (problem,) = E.table_problems(doc)
        self.assertIn("body", problem)
        self.assertIn(f"field {F_CHAR_TBL}", problem)

    def test_checking_does_not_move_the_selected_storage(self):
        doc = self.doc("aa Bold word\n")
        before = doc.slot
        E.table_problems(doc)
        self.assertEqual(doc.slot, before)

    def test_the_real_samples_are_clean(self):
        for name in os.listdir(SAMPLES):
            if name.endswith(".pages"):
                with self.subTest(sample=name):
                    self.assertEqual(E.table_problems(E.Document(os.path.join(SAMPLES, name))), [])


class VerifyReadsWithTheReaderToo(Case):
    def test_a_file_only_the_editor_can_read_fails_verification(self):
        path = write_pages(os.path.join(self.tmp.name, "v.pages"), "aa Bold word\n",
                           {F_PARA_TBL: table([(0, S1)])})
        out = io.StringIO()
        with mock.patch.object(P.PagesDoc, "all_paragraphs", side_effect=TypeError("boom")), \
                redirect_stdout(out):
            result = E.verify(path, [(0, 1, "a", "a")])
        self.assertEqual(result, "?")
        self.assertIn("VERIFY FAILED", out.getvalue())


@unittest.skipUnless(os.path.exists(os.path.join(SAMPLES, "emoji.pages")), "samples not present")
class TheReportedFailureThroughTheCli(Case):
    def test_deleting_a_bold_word_leaves_a_readable_document(self):
        path = os.path.join(self.tmp.name, "e.pages")
        shutil.copy(os.path.join(SAMPLES, "emoji.pages"), path)
        run_cli("replace", "-f", "Bold ", "-r", "", "--write", "--no-backup", path)
        md = P.render_markdown(None, P.PagesDoc(path).all_paragraphs())   # used to crash
        self.assertTrue(md.startswith("😀😀 word here."), md)
        self.assertEqual(E.table_problems(E.Document(path)), [])
        (c,) = P.PagesDoc(path).comments()
        self.assertEqual(c["quote"], "Commented")


@unittest.skipUnless(os.path.exists(os.path.join(SAMPLES, "formatting.pages")), "samples not present")
class RandomReplacementsKeepEveryTableWellFormed(Case):
    """A seeded sample of what the fuzzer did: one replacement of a random word, any storage."""
    REPLACEMENTS = ["x", "", "a much longer replacement", "😀", "é", "**", "[x]"]

    def test_samples(self):
        n = 0
        for name in ("emoji", "formatting", "kitchen-sink", "sample-content"):
            for seed in range(8):
                rng = random.Random(seed)
                path = os.path.join(self.tmp.name, f"{name}-{seed}.pages")
                shutil.copy(os.path.join(SAMPLES, name + ".pages"), path)
                doc = E.Document(path)
                handle = rng.choice(list(doc.slots))
                doc.select(handle)
                raw = doc.text()[0]
                words, pos = [], 0
                for w in raw.split():
                    i = raw.index(w, pos)
                    pos = i + len(w)
                    if any(c.isalpha() for c in w) and not any(c in E.BREAKS for c in w):
                        words.append((i, pos))
                a, b = rng.choice(words)
                doc.apply([(a, b, E.u16(rng.choice(self.REPLACEMENTS)))])
                doc.select("body")
                doc.save(path)                     # refuses an ill-formed result
                with self.subTest(sample=name, seed=seed):
                    self.assertEqual(E.table_problems(E.Document(path)), [])
                    P.render_markdown(None, P.PagesDoc(path).all_paragraphs())
                n += 1
        self.assertEqual(n, 32)


if __name__ == "__main__":
    unittest.main()
