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
# some names are only imported to be re-exported: the tests take them from here
from pages2md import (T_STORAGE, T_ATTACHMENT, T_CHAR_STYLE, T_LIST_STYLE,  # noqa: F401
                      P_BOLD, P_ITALIC, P_UNDERLINE, P_STRIKE, F_PROPS, F_LIST_LABEL,
                      F_TEXT, F_PARA_TBL, F_LIST_TBL, F_CHAR_TBL,
                      F_INSERTIONS, F_DELETIONS, F_COMMENTS, F_ATTACHMENTS)

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


def char_style(bold=False, italic=False, underline=False, strike=False):
    """A character-style archive with the given flags set (and nothing else)."""
    props = []
    for on, field in ((bold, P_BOLD), (italic, P_ITALIC),
                      (underline, P_UNDERLINE), (strike, P_STRIKE)):
        if on:
            props.append((field, 0, write_varint(1)))
    return emit([(F_PROPS, 2, emit(props))])


def para_triples(rows):
    """A table of {index, value, 0} entries from [(index, value)]: list levels
    (field 6) hold the level, list restarts (field 14) the `first` flag."""
    return emit([(1, 2, emit([(1, 0, write_varint(i)), (2, 0, write_varint(v)),
                              (3, 0, write_varint(0))])) for i, v in rows])


para_levels = para_starts = para_triples


def list_style(kind, label):
    """A list-style archive: its names and the label type of all nine levels.

    `label` is Pages' code: 0 none, 1 image, 2 bullet text, 3 numbered.
    """
    names = emit([(1, 2, kind.encode()),
                  (2, 2, f"text-1-liststyle-{kind}".encode())])
    return emit([(1, 2, names), (10, 0, write_varint(6))]
                + [(F_LIST_LABEL, 0, write_varint(label))] * 9)


def hyperlink(url):
    """A hyperlink smart-field archive (type 2032): a uuid and the URL."""
    return emit([(1, 2, b"\n$00000000-0000-0000-0000-000000000000"),
                 (2, 2, url.encode())])


def footnote(ref_id, storage_id, text, paragraphs=None):
    """Archives for one note: an attachment pointing at its own text storage.

    `paragraphs` splits `text` into paragraphs ("\n"-separated) with the
    paragraph-style table starting each one.
    """
    starts, pos = [], 0
    for part in text.split("\n"):
        starts.append((pos, 1))
        pos += len(part) + 1
    return [(ref_id, T_ATTACHMENT, emit([(2, 2, E.ref_bytes(storage_id))])),
            (storage_id, T_STORAGE, storage(text, {F_PARA_TBL: table(starts)}))]


def write_pages(path, text, tables, with_changes=True, extra=()):
    """`extra`: further (id, type, body) archives, e.g. character styles."""
    archives = [(BODY_ID, T_STORAGE, storage(text, tables))] + list(extra)
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
