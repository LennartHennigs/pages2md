"""Attribute tables stay attached to the right text across structural edits.

Each class covers one finding from the code review; the docstrings say what
went wrong before the fix.
"""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixture import (write_pages, table, comment_table, rows, ranges, para_levels,
                     tables_of, F_PARA_TBL, F_LIST_TBL, F_CHAR_TBL,
                     F_INSERTIONS, F_DELETIONS, F_COMMENTS)
import pages_edit as E

BOLD, ITALIC = 900, 901
BULLET = 800
CHANGE, OTHER_CHANGE = 700, 701
COMMENT = 600

#         0    5    10   15
THREE = "AAAA\nBBBB\nCCCC\n"      # paragraphs at 0, 5, 10


class DocCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def doc(self, text, **tables):
        """A Document over `text` with tables given as field=encoded bytes."""
        fields = {F_PARA_TBL: table([(0, 1)])}
        names = {"para": F_PARA_TBL, "lists": F_LIST_TBL, "char": F_CHAR_TBL,
                 "ins": F_INSERTIONS, "dels": F_DELETIONS,
                 "comments": F_COMMENTS, "comment_runs": E.F_COMMENTS_RUN}
        fields.update({names[k]: v for k, v in tables.items()})
        path = write_pages(os.path.join(self.tmp.name, "t.pages"), text, fields)
        return E.Document(path)

    def roundtrip(self, doc):
        """Save and re-open, so every assertion also covers the write path."""
        doc.save(doc.path)
        return E.Document(doc.path)

    def value_at(self, doc, field, index):
        return E.char_value_at(tables_of(doc)[field], index)


class DeleteParagraphKeepsRuns(DocCase):
    """1.1: dropping a run's terminator inside the deleted paragraph made the
    run bleed into the following text."""

    def test_run_ending_inside_deleted_paragraph_does_not_bleed(self):
        # bold from 2 ("AA") to 7 ("BB"); deleting B must leave C plain
        doc = self.doc(THREE, char=table([(2, BOLD), (7, None)]))
        doc.delete_paragraph(5)
        doc = self.roundtrip(doc)
        self.assertEqual(doc.text()[0], "AAAA\nCCCC\n")
        self.assertEqual(self.value_at(doc, F_CHAR_TBL, 2), BOLD)
        for i in range(5, 10):
            self.assertIsNone(self.value_at(doc, F_CHAR_TBL, i), i)

    def test_run_starting_inside_deleted_paragraph_keeps_its_tail(self):
        # bold from 7 ("BB") to 12 ("CC"); after deleting B, "CC" stays bold
        doc = self.doc(THREE, char=table([(7, BOLD), (12, None)]))
        doc.delete_paragraph(5)
        self.assertEqual(rows(doc, F_CHAR_TBL), [(5, BOLD), (7, None)])

    def test_tracked_deletion_does_not_swallow_next_paragraph(self):
        doc = self.doc(THREE, dels=table([(2, CHANGE), (7, None)]))
        doc.delete_paragraph(5)
        self.assertEqual(E.spans_of(tables_of(doc)[F_DELETIONS]),
                         [(2, 5, CHANGE)])
        # and the accepted view therefore still shows C (the deleted span
        # took A's newline with it, which is what it covered before)
        self.assertEqual(doc.accepted_map()[0], "AACCCC\n")

    def test_run_length_comment_does_not_spread(self):
        # body comments keyed run-length (field 23), as Pages 15.4 writes them
        doc = self.doc(THREE, comment_runs=table([(0, None), (2, COMMENT),
                                                  (7, None)]))
        doc.delete_paragraph(5)
        self.assertEqual(doc.comment_table(), [(2, 3, COMMENT)])

    def test_following_bullet_keeps_its_list_style(self):
        # B and C are bullets; C inherits through a null "no change" entry
        doc = self.doc(THREE, lists=table([(5, BULLET), (10, None)]))
        doc.delete_paragraph(5)
        self.assertEqual(E.effective_style_at(tables_of(doc)[F_LIST_TBL], 5),
                         BULLET)

    def test_untouched_runs_elsewhere_are_unchanged(self):
        doc = self.doc(THREE, char=table([(0, BOLD), (2, None),
                                          (12, ITALIC), (13, None)]))
        doc.delete_paragraph(5)
        self.assertEqual(rows(doc, F_CHAR_TBL),
                         [(0, BOLD), (2, None), (7, ITALIC), (8, None)])

    def test_deleting_last_paragraph_writes_nothing_past_the_end(self):
        doc = self.doc(THREE, char=table([(7, BOLD), (12, None)]))
        doc.delete_paragraph(10)
        self.assertEqual(doc.text()[0], "AAAA\nBBBB\n")
        self.assertTrue(all(i <= 10 for i, _r in rows(doc, F_CHAR_TBL)))

    def test_empty_slot_before_a_page_break_is_a_no_op(self):
        # PARA_SPLIT yields an empty "paragraph" at the \x04; deleting it
        # used to write a style entry onto the break character itself
        text = "AAAA\n\x04BBBB\n"
        doc = self.doc(text, para=table([(0, 1), (6, 2)]))
        before = doc._msg
        self.assertEqual(doc.delete_paragraph(5), (5, 0))
        self.assertEqual(doc._msg, before)

    def test_clear_range_counts_only_real_paragraphs(self):
        text = "AAAA\n\x04BBBB\nCCCC\n"
        doc = self.doc(text)
        self.assertEqual(doc.clear_range(5, 11), 1)     # just "BBBB"
        self.assertEqual(doc.text()[0], "AAAA\n\x04CCCC\n")


def levels(doc):
    """[(index, level)] of the list-level table (field 6) of the selected storage."""
    val = tables_of(doc)[6]
    return [(i, E.read_varint(E.parse_fields_of(sub)[2][0], 0)[0])
            for i, _r, sub in E.entry_rows(val)]


class DeleteParagraphKeepsListLevels(DocCase):
    """The list-level table is run-length like the style tables: an entry
    holds until the next. Dropping the entry of a deleted item demoted the
    items after it that relied on it."""

    # a(0) b(1) c(1) d(0)  --  b and c share one entry
    TEXT = "a\nb\nc\nd\n"

    def _doc(self, rows_):
        path = write_pages(os.path.join(self.tmp.name, "lv.pages"), self.TEXT,
                           {F_PARA_TBL: table([(0, 1)]), 6: para_levels(rows_)})
        return E.Document(path)

    def test_the_item_after_a_deleted_one_keeps_its_level(self):
        doc = self._doc([(0, 0), (2, 1), (6, 0)])
        doc.delete_paragraph(2)                      # b
        self.assertEqual(doc.text()[0], "a\nc\nd\n")
        self.assertEqual(levels(doc), [(0, 0), (2, 1), (4, 0)])

    def test_deleting_the_last_item_of_a_run_changes_nothing_else(self):
        doc = self._doc([(0, 0), (2, 1), (6, 0)])
        doc.delete_paragraph(4)                      # c
        self.assertEqual(levels(doc), [(0, 0), (2, 1), (4, 0)])

    def test_deleting_the_first_paragraph(self):
        doc = self._doc([(0, 1), (4, 0)])            # a, b level 1; c, d level 0
        doc.delete_paragraph(0)
        self.assertEqual(levels(doc), [(0, 1), (2, 0)])

    def test_an_entry_at_the_next_paragraph_is_left_alone(self):
        doc = self._doc([(0, 0), (2, 2), (4, 1), (6, 0)])
        doc.delete_paragraph(2)                      # b, level 2; c has its own entry
        self.assertEqual(levels(doc), [(0, 0), (2, 1), (4, 0)])

    def test_the_level_after_the_end_is_not_invented(self):
        doc = self._doc([(0, 0), (6, 2)])            # d is level 2, last paragraph
        doc.delete_paragraph(6)
        self.assertEqual(levels(doc), [(0, 0)])

    def test_survives_a_save_and_a_reload(self):
        doc = self._doc([(0, 0), (2, 1), (6, 0)])
        doc.delete_paragraph(2)
        doc.save(doc.path)
        self.assertEqual(levels(E.Document(doc.path)), [(0, 0), (2, 1), (4, 0)])

    def test_insert_after_an_item_is_its_sibling_and_the_next_keeps_its_level(self):
        doc = self._doc([(0, 0), (2, 1), (4, 2), (6, 0)])     # a b c(2) d
        doc.insert_paragraph(2, "NEW")                         # before b
        # NEW inherits a's level (0); b, c and d keep theirs, shifted by 4
        self.assertEqual(levels(doc), [(0, 0), (6, 1), (8, 2), (10, 0)])


class InsertParagraphIsolatesNewText(DocCase):
    """1.2: the entry at the insertion point moved forward, so a new paragraph
    took on the formatting or tracked change that preceded it."""

    def test_new_paragraph_after_bold_paragraph_is_plain(self):
        doc = self.doc("AAAA\nCCCC\n", char=table([(0, BOLD), (5, None)]))
        doc.insert_paragraph(5, "NEW")
        doc = self.roundtrip(doc)
        self.assertEqual(doc.text()[0], "AAAA\nNEW\nCCCC\n")
        self.assertEqual(self.value_at(doc, F_CHAR_TBL, 0), BOLD)
        for i in range(5, 14):
            self.assertIsNone(self.value_at(doc, F_CHAR_TBL, i), i)

    def test_run_through_insertion_point_resumes_after_it(self):
        doc = self.doc("AAAA\nCCCC\n", char=table([(0, BOLD)]))
        doc.insert_paragraph(5, "NEW")
        self.assertEqual(rows(doc, F_CHAR_TBL),
                         [(0, BOLD), (5, None), (9, BOLD)])

    def test_new_paragraph_is_not_part_of_a_pending_insertion(self):
        doc = self.doc("AAAA\nCCCC\n", ins=table([(0, CHANGE), (5, None)]))
        doc.insert_paragraph(5, "NEW")
        self.assertEqual(E.spans_of(tables_of(doc)[F_INSERTIONS]),
                         [(0, 5, CHANGE)])

    def test_direct_zero_width_edit_does_not_join_a_tracked_deletion(self):
        doc = self.doc("gone kept\n", dels=table([(0, CHANGE), (5, None)]))
        doc.apply([(5, 5, "new ")])
        self.assertEqual(doc.text()[0], "gone new kept\n")
        self.assertEqual(E.spans_of(tables_of(doc)[F_DELETIONS]),
                         [(0, 5, CHANGE)])
        self.assertEqual(doc.accepted_map()[0], "new kept\n")

    def test_tracked_insertion_right_after_a_deletion(self):
        # used to exit with "sits inside an existing tracked change"
        doc = self.doc("gone kept\n", dels=table([(0, CHANGE), (5, None)]),
                       ins=table([(0, None)]))
        doc.apply_tracked([(5, 5, "new ")])
        dels = E.spans_of(tables_of(doc)[F_DELETIONS])
        ins = E.spans_of(tables_of(doc)[F_INSERTIONS])
        self.assertEqual(dels, [(0, 5, CHANGE)])
        self.assertEqual([(a, b) for a, b, _r in ins], [(5, 9)])


class TrackedEditsWithAnEmptySide(DocCase):
    """1.3: a zero-length span wrote an opener with no closer, so the change
    ran on to the next entry and marked unrelated text."""

    TEXT = "Hello world\nmore text\n"     # "more" at 12-16

    def test_tracked_pure_deletion_marks_no_insertion(self):
        doc = self.doc(self.TEXT, ins=table([(12, OTHER_CHANGE), (16, None)]),
                       dels=table([(0, None)]))
        doc.apply_tracked([(6, 11, "")])
        doc = self.roundtrip(doc)
        self.assertEqual(doc.text()[0], self.TEXT)
        self.assertEqual(E.spans_of(tables_of(doc)[F_INSERTIONS]),
                         [(12, 16, OTHER_CHANGE)])
        dels = E.spans_of(tables_of(doc)[F_DELETIONS])
        self.assertEqual([(a, b) for a, b, _r in dels], [(6, 11)])
        self.assertEqual(doc.accepted_map()[0], "Hello \nmore text\n")

    def test_tracked_pure_insertion_marks_no_deletion(self):
        doc = self.doc(self.TEXT, dels=table([(12, OTHER_CHANGE), (16, None)]),
                       ins=table([(0, None)]))
        doc.apply_tracked([(6, 6, "big ")])
        self.assertEqual(E.spans_of(tables_of(doc)[F_DELETIONS]),
                         [(16, 20, OTHER_CHANGE)])
        ins = E.spans_of(tables_of(doc)[F_INSERTIONS])
        self.assertEqual([(a, b) for a, b, _r in ins], [(6, 10)])

    def test_missing_table_is_not_created_for_an_empty_span(self):
        doc = self.doc(self.TEXT)                     # no change tables at all
        doc.apply_tracked([(6, 11, "")])
        self.assertNotIn(F_INSERTIONS, tables_of(doc))
        self.assertIn(F_DELETIONS, tables_of(doc))

    def test_no_change_archive_is_minted_for_an_empty_side(self):
        doc = self.doc(self.TEXT)
        before = len(doc.arcs)
        doc.apply_tracked([(6, 11, "")])
        self.assertEqual(len(doc.arcs), before + 1)   # the deletion only

    def test_put_span_ignores_empty_spans(self):
        t = table([(0, None), (50, CHANGE), (60, None)])
        self.assertEqual(E.put_span(t, 20, 20, 999), t)

    def test_put_span_refuses_a_partial_overlap(self):
        t = table([(10, CHANGE), (20, None)])
        with self.assertRaises(SystemExit):
            E.put_span(t, 15, 25, 999)
        with self.assertRaises(SystemExit):
            E.put_span(t, 5, 12, 999)

    def test_put_span_still_refuses_a_change_within_a_change(self):
        with self.assertRaises(SystemExit):
            E.put_span(table([(10, CHANGE), (20, None)]), 12, 15, 999)

    def test_put_span_accepts_adjacent_spans(self):
        t = E.put_span(table([(10, CHANGE), (20, None)]), 20, 25, 999)
        self.assertEqual(E.spans_of(t), [(10, 20, CHANGE), (20, 25, 999)])


class CommentRangesAreClamped(DocCase):
    """1.4: a comment ending inside a shrunk edit kept its old end, so it
    quoted text past the edit."""

    def shifted(self, a, n, start, end, delta):
        val, _moved = E.shift_table(comment_table([(a, n, COMMENT)]),
                                    start, end, delta)
        return [(i, ln) for i, ln, _r in self.decode(val)]

    @staticmethod
    def decode(val):
        out = []
        for sub in E.parse_fields_of(val).get(1, []):
            e = E.parse_fields_of(sub)
            r = E.parse_fields_of(e[1][0])
            out.append((E.read_varint(r[1][0], 0)[0],
                        E.read_varint(r[2][0], 0)[0], E.ref_of(e[2][0])))
        return out

    def test_end_inside_shrunk_edit_is_clamped(self):
        # [5,15); replace [10,20) with 2 chars -> the edit now ends at 12
        self.assertEqual(self.shifted(5, 10, 10, 20, -8), [(5, 7)])

    def test_start_inside_shrunk_edit_is_clamped(self):
        # [15,25); replace [10,20) with 2 chars -> starts at 12, ends at 17
        self.assertEqual(self.shifted(15, 10, 10, 20, -8), [(12, 5)])

    def test_range_after_edit_moves(self):
        self.assertEqual(self.shifted(30, 4, 10, 20, -8), [(22, 4)])

    def test_range_before_edit_is_untouched(self):
        self.assertEqual(self.shifted(0, 5, 10, 20, -8), [(0, 5)])

    def test_range_across_edit_shrinks(self):
        self.assertEqual(self.shifted(5, 20, 10, 20, -8), [(5, 12)])

    def test_growing_edit_keeps_inner_points(self):
        # an end inside a growing edit stays put rather than being pushed out
        self.assertEqual(self.shifted(5, 10, 10, 20, +5), [(5, 10)])

    def test_through_apply_on_a_document(self):
        text = "0123456789abcdefghijKLMN\n"
        doc = self.doc(text, comments=comment_table([(5, 10, COMMENT)]))
        doc.apply([(10, 20, "xy")])
        doc = self.roundtrip(doc)
        a, n, ref = ranges(doc)[0]
        self.assertEqual((a, n, ref), (5, 7, COMMENT))
        self.assertEqual(doc.text()[0][a:a + n], "56789xy")

    def test_comment_ending_inside_a_deleted_paragraph_is_clamped(self):
        doc = self.doc(THREE, comments=comment_table([(2, 5, COMMENT)]))
        doc.delete_paragraph(5)
        self.assertEqual(ranges(doc), [(2, 3, COMMENT)])


class MinimalEdit(DocCase):
    """Direct replacement only rewrites what changes, so runs inside the
    shared part stay on their characters."""

    def test_shared_ends_are_trimmed(self):
        self.assertEqual(E.minimal_edit("xx Bold!", 0, 7, "x Bold"), (1, 2, ""))
        self.assertEqual(E.minimal_edit("colour", 0, 6, "color"), (4, 5, ""))
        self.assertEqual(E.minimal_edit("abc", 0, 3, "xyz"), (0, 3, "xyz"))
        self.assertEqual(E.minimal_edit("same", 0, 4, "same"), (4, 4, ""))

    def test_never_splits_a_surrogate_pair(self):
        # 😀 and 😃 share their first UTF-16 half
        old, new = E.u16("a😀b"), E.u16("a😃b")
        start, end, rep = E.minimal_edit(old, 0, len(old), new)
        self.assertEqual((start, end), (1, 3))
        self.assertEqual(E.from_u16(rep), "😃")

    def test_bold_stays_on_its_word(self):
        doc = self.doc("xx Bold.\n", char=table([(0, None), (3, BOLD), (7, None)]))
        doc.apply([(0, 7, "x Bold")])
        self.assertEqual(doc.text()[0], "x Bold.\n")
        self.assertEqual(rows(doc, F_CHAR_TBL), [(0, None), (2, BOLD), (6, None)])


if __name__ == "__main__":
    unittest.main()
