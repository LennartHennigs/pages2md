"""Build minimal, real .pages packages for tests.

Only Index/Document.iwa is written: one text storage plus whatever extra
archives a test needs (tracked-change templates, comment archives). The file
goes through the same codec the tools use, so `Document(path)` and
`PagesDoc(path)` open it exactly as they would a document Pages wrote.
"""
import os, struct, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from iwa_codec import emit, write_varint, pack_archives, iwa_encode, read_varint
import pages_edit as E
from pages2md import (T_STORAGE, F_TEXT, F_PARA_TBL, F_LIST_TBL, F_CHAR_TBL,
                      F_INSERTIONS, F_DELETIONS, F_COMMENTS)

BODY_ID = 100
DEL_TEMPLATE_ID, INS_TEMPLATE_ID = 200, 201


def table(rows):
    """An index-keyed attribute table from [(char index, ref or None)]."""
    return emit([(1, 2, E.entry_bytes(i, r)) for i, r in rows])


def comment_table(rows):
    """A range-keyed comment table from [(start, length, ref)]."""
    return emit([(1, 2, E.comment_anchor(a, n, r)) for a, n, r in rows])


def storage(text, tables):
    """A TSWP storage message: the text plus {field: encoded table}."""
    toks = [(F_TEXT, 2, text.encode("utf-8"))]
    toks += [(f, 2, v) for f, v in tables.items()]
    toks.sort(key=lambda t: t[0])
    return emit(toks)


def change(kind):
    """A tracked-change archive: kind, timestamp, UUID -- what new_change clones."""
    return emit([(1, 0, write_varint(kind)),
                 (3, 2, emit([(1, 1, struct.pack("<d", 0.0))])),
                 (4, 2, b"00000000-0000-0000-0000-000000000000")])


def write_pages(path, text, tables, with_changes=True):
    archives = [(BODY_ID, T_STORAGE, storage(text, tables))]
    if with_changes:
        archives += [(DEL_TEMPLATE_ID, E.T_CHANGE, change(2)),
                     (INS_TEMPLATE_ID, E.T_CHANGE, change(1))]
    arcs = []
    for ident, mtype, body in archives:
        info = emit([(1, 0, write_varint(ident)),
                     (2, 2, emit([(1, 0, write_varint(mtype)),
                                  (2, 2, E.MSG_VERSION),
                                  (3, 0, write_varint(len(body)))]))])
        arcs.append([info, [[mtype, body]]])
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(E.BODY_ENTRY, iwa_encode(pack_archives(arcs)))
    return path


def tables_of(doc):
    """{field: raw table bytes} for the selected storage of a Document."""
    return {num: val for num, wire, val in E.tokenize(doc._msg)
            if num != F_TEXT and wire == 2}


def rows(doc, field):
    """[(index, ref or None)] of one index-keyed table, or [] if absent."""
    val = tables_of(doc).get(field)
    return [(i, r) for i, r, _s in E.entry_rows(val)] if val else []


def ranges(doc, field=F_COMMENTS):
    """[(start, length, ref)] of a range-keyed comment table."""
    out = []
    for sub in E.parse_fields_of(tables_of(doc)[field]).get(1, []):
        e = E.parse_fields_of(sub)
        r = E.parse_fields_of(e[1][0])
        out.append((read_varint(r[1][0], 0)[0], read_varint(r[2][0], 0)[0],
                    E.ref_of(e[2][0])))
    return out
