#!/usr/bin/env python3
"""pages_edit — edit text inside an Apple Pages (.pages) document in place.

Replaces text in the document's body flow and shifts every character index in
that storage's attribute tables so styles, lists and tracked changes stay
attached to the right words. No dependencies; Pages need not be running.

    pages_edit.py find "sticky note" book.pages
    pages_edit.py replace --find "jane doe" --replace "Jane Doe" book.pages
    pages_edit.py replace -f old.txt -r new.txt --write book.pages

Edits are a dry run unless you pass --write.

Internal source control (separate from the project's own git history) keeps a
snapshot of every version so any edit can be undone:

    pages_edit.py config --vcs on book.pages
    pages_edit.py history book.pages
    pages_edit.py revert HEAD~1 book.pages
"""
import argparse, difflib, json, os, random, re, shutil, struct, subprocess, sys, time, uuid, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from iwa_codec import (archives, pack_archives, iwa_decode, iwa_encode,
                       tokenize, emit, read_varint, write_varint)

BODY_ENTRY = "Index/Document.iwa"
T_CHANGE = 2060               # editor-only: a tracked-change archive
# Pages leads some paragraphs with a page-break or anchor character, so `^`
# would not match there. Mapping them to newlines for *matching only* is
# length-preserving, which keeps every offset valid.
# U+2028 is a manual line break inside a heading that wraps across two
# lines in the original layout, so it reads as a space to a searcher.
SEARCH_VIEW = {0x04: 0x0A, 0x05: 0x0A, 0x0E: 0x0A, 0x2028: 0x20}
BREAKS = "\x04\x05\x0e\u2028"
CONFIG_NAME = ".pages-edit.json"
VCS_DIR = ".pages-vcs"


# --------------------------------------------------------------- attribute tables
F_CHAR_TBL = 8                # character styles: runs of bold/italic/etc.


def char_value_at(val, index):
    """The character style in force at `index`, null entries included.

    Unlike the paragraph table, a null entry here is meaningful -- it means
    "no override from here" -- so it must be carried, not skipped.
    """
    best = None
    for idx, ref, _sub in entry_rows(val):
        if idx <= index:
            best = ref
        else:
            break
    return best


def entry_rows(val):
    """[(char index, ref or None, raw entry)] for one ObjectAttributeTable.

    Every table walker below is a filter over this, so "what an entry looks
    like" is written once rather than re-derived in each of them.
    """
    out = []
    for num, wire, sub in (tokenize(val) if val else []):
        if num != 1 or wire != 2:
            continue
        f = parse_fields_of(sub)
        if 1 not in f:
            continue
        # Field 2 is a TSP.Reference in the index-keyed tables, but these
        # walkers also run over tables keyed other ways (comment anchors),
        # where it is not. Decode it only when it really is one.
        ref = None
        if 2 in f and len(f[2][0]) > 1:
            ref = ref_of(f[2][0])
        out.append((read_varint(f[1][0], 0)[0], ref, sub))
    out.sort(key=lambda r: r[0])
    return out


def table_kind(val):
    """"index", "range", or None -- how this table keys its entries.

    An index table keys on a bare varint; a comment table keys on a nested
    {start, length} message. One probe rather than two near-identical ones,
    so the two shapes cannot drift apart.
    """
    try:
        toks = tokenize(val)
    except Exception:
        return None
    if not toks:
        return None
    kinds = set()
    for num, wire, sub in toks:
        if num != 1 or wire != 2:
            return None
        try:
            inner = tokenize(sub)
        except Exception:
            return None
        if not inner or inner[0][0] != 1:
            return None
        kinds.add(inner[0][1])
    if kinds == {0}:
        return "index"
    if kinds == {2}:
        return "range"
    return None


def shift_point(i, start, end, delta):
    """Where character index `i` lands after [start, end) becomes delta longer.

    Points after the edit move by delta; points inside it are clamped so they
    never land past the replacement's new end. One rule for both table
    shapes -- the range shifter used to move only points at or after `end`,
    so a comment ending inside a shrunk edit quoted the text after it.
    """
    if i >= end:
        return i + delta
    if i > start:
        return start + min(i - start, end - start + delta)
    return i


def shift_ranges(val, start, end, delta):
    """Shift a table whose entries carry a {start, length} range.

    Comment anchors are stored this way rather than as a bare character
    index, so the plain index shifter skips them entirely and the comment
    silently ends up quoting the wrong words.
    """
    rebuilt, moved = [], 0
    for num, wire, sub in tokenize(val):
        if num != 1 or wire != 2:
            rebuilt.append((num, wire, sub))
            continue
        inner = []
        for n2, w2, v2 in tokenize(sub):
            if n2 == 1 and w2 == 2:
                r = parse_fields_of(v2)
                a = read_varint(r[1][0], 0)[0] if 1 in r else 0
                ln = read_varint(r[2][0], 0)[0] if 2 in r else 0
                b = a + ln
                na = shift_point(a, start, end, delta)
                nb = shift_point(b, start, end, delta)
                if na != a or nb != b:
                    moved += 1
                parts = [(1, 0, write_varint(na))]
                if 2 in r:
                    parts.append((2, 0, write_varint(max(nb - na, 0))))
                for n3, w3, v3 in tokenize(v2):
                    if n3 not in (1, 2):
                        parts.append((n3, w3, v3))
                v2 = emit(parts)
            inner.append((n2, w2, v2))
        rebuilt.append((1, 2, emit(inner)))
    return emit(rebuilt), moved


def shift_table(val, start, end, delta):
    """Rewrite the character index of every entry in one attribute table."""
    kind = table_kind(val)
    if kind == "range":
        return shift_ranges(val, start, end, delta)
    if kind != "index":
        # Warn only for things shaped like a table (repeated field-1 messages)
        # whose key we cannot read -- leaving one unshifted detaches its
        # attributes from the text. Plain references are not tables.
        try:
            toks = tokenize(val)
            if toks and all(n == 1 and w == 2 for n, w, _v in toks):
                print("warning: an attribute table with an unrecognised key "
                      f"shape was left unshifted ({len(toks)} entries); its "
                      "formatting may now point at the wrong characters",
                      file=sys.stderr)
        except Exception:
            pass
        return val, 0
    moved = 0
    rebuilt = []
    for _num, _wire, entry in tokenize(val):
        new_inner = []
        for num, wire, sub in tokenize(entry):
            if num == 1 and wire == 0:
                i = read_varint(sub, 0)[0]
                j = shift_point(i, start, end, delta)
                if j != i:
                    moved += 1
                sub = write_varint(j)
            new_inner.append((num, wire, sub))
        rebuilt.append((1, 2, emit(new_inner)))
    return emit(rebuilt), moved


C_ID = 5                      # editor-only: a comment's own identifier pair
MSG_VERSION = b"\x01\x00\x05"


def packed(ids):
    """TSP MessageInfo's reference list: packed varint archive ids."""
    return b"".join(write_varint(i) for i in ids)


def apple_date(when=None):
    """{1: fixed64 seconds since 2001-01-01 UTC}, as Pages stores a date."""
    secs = (when or time.time()) - APPLE_EPOCH
    return emit([(1, 1, struct.pack("<d", secs))])


def comment_body(text, author_id, next_id=None, when=None):
    toks = [(C_TEXT, 2, text.encode("utf-8")),
            (C_DATE, 2, apple_date(when)),
            (C_AUTHOR, 2, emit([(1, 0, write_varint(author_id))]))]
    if next_id is not None:
        toks.append((C_NEXT, 2, emit([(1, 0, write_varint(next_id))])))
    toks.append((C_ID, 2, emit([(1, 0, write_varint(random.getrandbits(63))),
                                (2, 0, write_varint(random.getrandbits(63)))])))
    return emit(toks)


def comment_anchor(start, length, ref_id):
    """One entry of the body's comment table: a range plus the comment."""
    return emit([(1, 2, emit([(1, 0, write_varint(start)),
                              (2, 0, write_varint(length))])),
                 (2, 2, emit([(1, 0, write_varint(ref_id))]))])


PARA_END = "\n"


def after_paragraph(raw, end):
    """Where a new paragraph goes when inserted after one ending at `end`."""
    return min(end + 1, len(raw)) if raw[end:end + 1] == PARA_END else end


def para_bounds(raw, offset):
    """(start, end) of the paragraph containing `offset`.

    A paragraph begins after *any* break character, not just a newline:
    Pages leads some paragraphs with a page break or object anchor, and the
    style tables index the first character after it. Anchoring on `\n` alone
    puts the entry a character or two early, where it silently does nothing.
    """
    start = max(raw.rfind(c, 0, offset) for c in PARA_BREAKS) + 1
    # ...and it ends at the next break of any kind, not just a newline: the
    # following paragraph may begin with a page break or anchor instead, and
    # ending only on "\n" swallows it whole.
    ends = [i for i in (raw.find(c, offset) for c in PARA_BREAKS) if i >= 0]
    return start, min(ends) if ends else len(raw)


def effective_style_at(val, index):
    """The style a paragraph at `index` resolves to today.

    Most paragraphs carry a null entry meaning "no change", so the style in
    force comes from the nearest preceding entry that actually sets one.
    """
    best = None
    for idx, ref, _sub in entry_rows(val):
        if idx <= index and ref is not None:
            best = ref
    return best


def states_own_style(val, index):
    """True when an entry at exactly `index` already sets a style.

    Such a paragraph does not inherit, so pinning anything onto it would
    overwrite the style it states for itself.
    """
    return any(i == index and ref is not None for i, ref, _s in entry_rows(val))


def set_style_at(val, index, style_id):
    """Force the paragraph-style entry at `index` to `style_id`."""
    rows = [(i, sub) for i, _r, sub in entry_rows(val) if i != index]
    rows.append((index, entry_bytes(index, style_id)))
    rows.sort(key=lambda r: r[0])
    return emit([(1, 2, sub) for _i, sub in rows])


def has_entry_at(val, index):
    return any(i == index for i, _r, _s in entry_rows(val))


def carry_run(val, at, value, bound):
    """Make `value` the value in force at `at` in a run-length table.

    After deleting [start, end), the text that followed the deletion starts
    at `start` and should keep the value it had at `end`. Dropping the
    entries inside the deleted span can remove the terminator of a run that
    began before it, which used to let that run -- bold, a tracked deletion
    -- bleed on into the text that followed.
    """
    if at >= bound or has_entry_at(val, at) or char_value_at(val, at) == value:
        return val
    return set_style_at(val, at, value)


def isolate_insertion(val, at, length, bound):
    """Keep `length` characters just inserted at `at` out of the preceding run.

    shift_table moves the entry at `at` forward, so new text takes whatever
    value was in force just before it. For a run table that would put a new
    paragraph inside the bold run or the tracked change that precedes it.
    Close the run at `at` and, when the text after the insertion relied on
    the same run, re-open it there.
    """
    prev = char_value_at(val, at)
    if prev is None or length <= 0:
        return val
    after = at + length
    if after < bound and not has_entry_at(val, after):
        val = set_style_at(val, after, prev)
    return set_style_at(val, at, None)


def drop_entries_in(val, start, end):
    """Remove table entries that fall inside [start, end).

    Range-keyed tables (comment anchors) key on a nested {start, length}
    message, not a bare varint -- decoding one as the other made every anchor
    report index 8, the protobuf tag byte, so deleting the paragraph that
    spans offset 8 removed every comment in the document.
    """
    kind = table_kind(val)
    if kind == "range":
        kept = []
        for num, wire, sub in tokenize(val):
            f = parse_fields_of(sub)
            r = parse_fields_of(f[1][0])
            a = read_varint(r[1][0], 0)[0] if 1 in r else 0
            if not (start <= a < end):
                kept.append((num, wire, sub))
        return emit(kept)
    if kind != "index":
        return val                      # unknown shape: leave it alone
    return emit([(1, 2, sub) for i, _r, sub in entry_rows(val)
                 if not (start <= i < end)])


MD_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
MD_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
# Emphasis needs text right inside its markers, and an underscore must stand
# at a word edge: "my_var_name" and "2 * 3 * 4" are not emphasis.
MD_EMPH = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*"
                     r"|\*(?!\s)(.+?)(?<!\s)\*"
                     r"|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")


def strip_markup(text):
    """-> (plain text, [(start, end, bold, italic)]) in plain-text offsets.

    The markers come out and the emphasis is recorded as runs, so the
    importer can apply it as real character styling instead of discarding it.
    """
    out, runs, pos = [], [], 0
    for m in MD_EMPH.finditer(text):
        out.append(text[pos:m.start()])
        inner = m.group(1) or m.group(2) or m.group(3)
        at = sum(len(s) for s in out)
        out.append(inner)
        runs.append((at, at + len(inner), m.group(1) is not None,
                     m.group(1) is None))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out), runs


def parse_markdown(text):
    """-> [(para style, list style or None, text, emphasis runs)]."""
    blocks, buffer = [], []

    def flush():
        if buffer:
            text, runs = strip_markup(" ".join(buffer))
            blocks.append(("Body 1", None, text, runs))
            buffer.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        head = MD_HEADING.match(stripped)
        if head:
            flush()
            text, runs = strip_markup(head.group(2).strip())
            blocks.append((f"Heading {len(head.group(1))}", None, text, runs))
            continue
        bullet = MD_BULLET.match(line)
        if bullet:
            flush()
            text, runs = strip_markup(bullet.group(1))
            blocks.append(("Body 1", "Bullet", text, runs))
            continue
        # a wrapped bullet continues on an indented line; without this it
        # becomes a paragraph of its own mid-list
        if (blocks and not buffer and blocks[-1][1] == "Bullet"
                and line[:1].isspace()):
            style, listing, text, runs = blocks[-1]
            more, more_runs = strip_markup(stripped)
            joined = f"{text} {more}"
            shift = len(text) + 1
            blocks[-1] = (style, listing, joined,
                          runs + [(a + shift, b + shift, bo, it)
                                  for a, b, bo, it in more_runs])
            continue
        buffer.append(stripped)
    flush()
    return blocks


def parse_fields_of(msg):
    """{field_no: [value, ...]} for one message."""
    out = {}
    for num, _wire, val in tokenize(msg):
        out.setdefault(num, []).append(val)
    return out


def ref_bytes(archive_id):
    """A TSP.Reference to one archive."""
    return emit([(1, 0, write_varint(archive_id))])


def entry_bytes(index, archive_id=None):
    """One ObjectAttributeTable entry; no archive_id makes it a terminator."""
    toks = [(1, 0, write_varint(index))]
    if archive_id is not None:
        toks.append((2, 2, ref_bytes(archive_id)))
    return emit(toks)


def spans_of(val):
    """Effective [(start, end, ref)] change spans in one table.

    A ref'd entry's span ends at the next entry, so a *dangling* final ref'd
    entry covers nothing. That is the rule `pages2md` validated against the
    body's tracked changes, and it matters here: patching entries into such a
    table would retroactively give the dangling entry a terminator and turn an
    empty span into a real one.
    """
    rows = [(i, r) for i, r, _sub in entry_rows(val)]
    out = []
    for k, (idx, ref) in enumerate(rows):
        if ref is None:
            continue
        nxt = rows[k + 1][0] if k + 1 < len(rows) else idx
        if nxt > idx:
            out.append((idx, nxt, ref))
    return out


def put_span(val, start, end, archive_id):
    """Add a change span, rebuilding the table from its effective spans.

    Emitting canonical boundaries rather than editing entries in place keeps
    the table's meaning exactly what `spans_of` says it is, with no dangling
    leftovers to misinterpret later.

    An empty span changes nothing. Writing one anyway left an opener with no
    closer, and the "change" ran on to whatever entry came next.
    """
    if end <= start:
        return val
    for a, b, _r in spans_of(val):
        if a < end and start < b:
            sys.exit(
                f"the text at {start}-{end} overlaps an existing tracked "
                f"change ({a}-{b}); recording a change across another would "
                "silently re-attribute or un-mark part of it. Resolve that "
                "change in Pages first, or edit with --no-track.")
    spans = spans_of(val) + [(start, end, archive_id)]
    marks = {}
    for _a, b, _r in spans:
        marks[b] = None            # close every span...
    for a, _b, r in spans:
        marks[a] = r               # ...but an opener at the same index wins
    return emit([(1, 2, entry_bytes(i, marks[i])) for i in sorted(marks)])


# ------------------------------------------------------------------- document
class Document:
    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self.names = z.namelist()
            self.entries = {n: z.read(n) for n in self.names}
            self.compress = {i.filename: i.compress_type for i in z.infolist()}
        body_payload = iwa_decode(self.entries[BODY_ENTRY])
        self.arcs = archives(body_payload)
        self.by_id = {}
        for ai, (info, msgs) in enumerate(self.arcs):
            ident = next((read_varint(v, 0)[0]
                          for n, _w, v in tokenize(info) if n == 1), None)
            if ident is None:
                continue
            for mi, (mtype, _m) in enumerate(msgs):
                self.by_id.setdefault(ident, (ai, mi, mtype))
                if mtype in (T_STORAGE, T_ATTACHMENT, T_COMMENT,
                             T_COMMENT_REF, T_CHANGE):
                    self.by_id[ident] = (ai, mi, mtype)   # prefer what we use
        self.slot = self._find_body()
        # "body" plus one handle per margin note, in reading order. Notes live
        # in their own storages with their own offset spaces, which is why a
        # body-only editor cannot reach them.
        # One enumeration, shared with the reader, so a handle means the same
        # thing in both tools. The generated TOC is listed but not editable --
        # Pages rebuilds it.
        #
        # One PagesDoc parse serves both this and next_id below: it already
        # decodes every .iwa file and indexes every archive by id, so a
        # second manual decode of the non-body files (as a prior version did)
        # was pure redundant work -- roughly 40 of the constructor's ~50ms.
        import pages2md
        reader = pages2md.PagesDoc(path)
        self.reader = reader              # reused by callers that need style/list lookups
        self.slots, self.anchors = {}, {}
        for handle, sid, anchor, kind in reader.storages():
            if kind == "toc" or sid is None or sid not in self.by_id:
                continue
            ai, mi, _t = self.by_id[sid]
            self.slots[handle] = (ai, mi)
            self.anchors[handle] = anchor
        self.slots["body"] = self.slot
        self.anchors["body"] = None
        self.slots = {"body": self.slots.pop("body"), **self.slots}
        # TSP object ids are one document-wide namespace, so a new id has to
        # clear every component -- ViewState and Metadata hold ids above the
        # body's maximum, and reusing one produces a duplicate-id document.
        # `reader.arcs` already spans every .iwa file's archives, keyed by id.
        self.next_id = 1 + max(reader.arcs, default=0)

    def _find_body(self):
        """(archive index, message index) of the largest text storage."""
        best, size = None, -1
        for ai, (_info, msgs) in enumerate(self.arcs):
            for mi, (mtype, msg) in enumerate(msgs):
                if mtype != T_STORAGE:
                    continue
                n = sum(len(v) for num, _w, v in tokenize(msg) if num == F_TEXT)
                if n > size:
                    best, size = (ai, mi), n
        if best is None:
            sys.exit(f"{self.path}: no text storage found")
        return best

    def select(self, handle):
        """Point the text and edit methods at one storage."""
        if handle not in self.slots:
            sys.exit(f"unknown storage {handle!r}; have "
                     f"{', '.join(self.slots)}")
        self.slot = self.slots[handle]
        return self

    @property
    def _msg(self):
        ai, mi = self.slot
        return self.arcs[ai][1][mi][1]

    @_msg.setter
    def _msg(self, value):
        ai, mi = self.slot
        self.arcs[ai][1][mi][1] = value

    def _change_template(self, kind):
        """An existing change archive of this kind, to clone author + framing."""
        for info, msgs in self.arcs:
            if not msgs or msgs[0][0] != T_CHANGE:
                continue
            for n, _w, v in tokenize(msgs[0][1]):
                if n == 1 and read_varint(v, 0)[0] == kind:
                    return info, msgs[0][1]
        return None, None

    def add_archive(self, mtype, body, refs=()):
        """Append a new archive and return its id.

        MessageInfo field 5 is a packed list of the archives this one points
        at -- TSP's dependency list. Getting it wrong is invisible until Pages
        decides the object is unreachable.
        """
        ident = self.next_id
        self.next_id += 1
        mi = [(1, 0, write_varint(mtype)), (2, 2, MSG_VERSION),
              (3, 0, write_varint(len(body)))]
        if refs:
            mi.append((5, 2, packed(refs)))
        info = emit([(1, 0, write_varint(ident)), (2, 2, emit(mi))])
        self.arcs.append([info, [[mtype, body]]])
        self.by_id[ident] = (len(self.arcs) - 1, 0, mtype)
        return ident

    def add_dependency(self, new_ids):
        """Record that the selected storage now points at these archives.

        TSP tracks each archive's outgoing references in its MessageInfo
        field 5. Without the entry Pages treats the target as unreachable and
        drops it on the next save -- which is exactly what happened to
        comments written into a text box.
        """
        ai, mi = self.slot
        info = self.arcs[ai][0]
        rebuilt, seen = [], -1
        for num, wire, val in tokenize(info):
            if num == 2:
                seen += 1
                if seen == mi:
                    inner, ids = [], []
                    for n2, w2, v2 in tokenize(val):
                        if n2 == 5:
                            pos = 0
                            while pos < len(v2):
                                got, pos = read_varint(v2, pos)
                                ids.append(got)
                        else:
                            inner.append((n2, w2, v2))
                    for i in new_ids:
                        if i not in ids:
                            ids.append(i)
                    inner.append((5, 2, packed(ids)))
                    inner.sort(key=lambda t: t[0])
                    val = emit(inner)
            rebuilt.append((num, wire, val))
        self.arcs[ai][0] = emit(rebuilt)

    def author_id(self):
        """The annotation author to attribute new comments to.

        Taken from an existing comment rather than looked up: the author
        archive lives in AnnotationAuthorStorage.iwa, which this class never
        loads -- but the id it references is all a new comment needs.
        """
        for ident, (ai, mi, t) in self.by_id.items():
            if t != T_COMMENT:
                continue
            c = parse_fields_of(self.arcs[ai][1][mi][1])
            if C_AUTHOR in c:
                return ref_of(c[C_AUTHOR][0])
        sys.exit("this document has no comment to take an author from; "
                 "add one in Pages first")

    def comment_field(self):
        """Which field holds this storage's comments, and how it is keyed.

        The body keys comments by {start, length} in field 25; a text box
        keys them by character index in field 23, run-length. Writing the
        body's shape into a text box produces a table Pages discards.
        """
        f = parse_fields_of(self._msg)
        if F_COMMENTS in f:
            return F_COMMENTS, "range"
        if F_COMMENTS_RUN in f:
            return F_COMMENTS_RUN, "run"
        return ((F_COMMENTS, "range") if self.slot == self.slots["body"]
                else (F_COMMENTS_RUN, "run"))

    def comment_table(self):
        """[(start, length, ref archive)] for the selected storage."""
        field, kind = self.comment_field()
        f = parse_fields_of(self._msg)
        if field not in f:
            return []
        if kind == "range":
            rows = []
            for sub in parse_fields_of(f[field][0]).get(1, []):
                e = parse_fields_of(sub)
                if 2 not in e:
                    continue
                r = parse_fields_of(e[1][0])
                rows.append((read_varint(r[1][0], 0)[0] if 1 in r else 0,
                             read_varint(r[2][0], 0)[0] if 2 in r else 0,
                             ref_of(e[2][0])))
            return rows
        # run-length: a ref'd entry runs to the next entry. Same shape
        # entry_rows already decodes for every other index-keyed table, so
        # this reuses it in one pass instead of re-parsing the field twice.
        idx = entry_rows(f[field][0])
        out = []
        for k, (i, ref, _sub) in enumerate(idx):
            if ref is None:
                continue
            end = idx[k + 1][0] if k + 1 < len(idx) else len(self.text()[0])
            out.append((i, end - i, ref))
        return out

    def set_comment_table(self, rows):
        """Replace the selected storage's comment table with these rows.

        A storage that has never carried a comment has no table at all, so it
        is created -- in whichever shape that storage uses.
        """
        field, kind = self.comment_field()
        if kind == "range":
            table = emit([(1, 2, comment_anchor(a, n, r))
                          for a, n, r in sorted(rows)])
        else:
            marks = {}
            for a, n, r in sorted(rows):
                marks.setdefault(a + n, None)   # close, unless something opens
            for a, n, r in sorted(rows):
                marks[a] = r
            marks.setdefault(0, None)       # a run-length table must start at 0
            table = emit([(1, 2, entry_bytes(i, marks[i]))
                          for i in sorted(marks)])
        self._put_field(field, table)

    def thread_ids(self, head):
        """Every comment archive in one thread, head first."""
        out, ident = [], head
        while ident and self.by_id.get(ident, (None, None, None))[2] == T_COMMENT:
            out.append(ident)
            c = parse_fields_of(self.arcs[self.by_id[ident][0]][1]
                                [self.by_id[ident][1]][1])
            ident = ref_of(c[C_NEXT][0]) if C_NEXT in c else None
        return out

    def add_comment(self, start, length, text):
        """A new comment anchored to [start, start+length) of this storage.

        A run-length comment table (text boxes) cannot express two comments
        that overlap: the shared boundary would truncate one of them, and the
        quoted passage silently changes. Refuse instead.
        """
        _field, kind = self.comment_field()
        existing = self.comment_table()
        if kind == "run":
            for a, n, _r in existing:
                if start < a + n and a < start + length:
                    raw = self.text()[0]
                    sys.exit(
                        f"that text overlaps an existing comment on "
                        f"{show(raw[a:a + n]).strip()!r} in this text box. A text "
                        "box keys comments by character run, so two "
                        "overlapping ones cannot both be stored. Pick text "
                        "outside it.")
        author = self.author_id()
        cid = self.add_archive(T_COMMENT, comment_body(text, author), [author])
        ref = self.add_archive(
            T_COMMENT_REF,
            emit([(1, 2, emit([(1, 0, write_varint(cid))])),
                  (2, 2, str(uuid.uuid4()).upper().encode())]),
            [cid])
        self.set_comment_table(existing + [(start, length, ref)])
        self.add_dependency([ref])
        return cid

    def reply_to(self, ref_id, text):
        """Append a reply to the thread behind a comment-reference archive."""
        head = read_varint(parse_fields_of(
            self.arcs[self.by_id[ref_id][0]][1][self.by_id[ref_id][1]][1]
        )[1][0], 1)[0]
        chain = self.thread_ids(head)
        if not chain:
            sys.exit("that comment's thread is unreadable (its head archive is "
                     "missing); nothing to reply to")
        author = self.author_id()
        new_id = self.add_archive(T_COMMENT, comment_body(text, author), [author])
        last = chain[-1]
        ai, mi, _t = self.by_id[last]
        toks = [(n, w, v) for n, w, v in tokenize(self.arcs[ai][1][mi][1])
                if n != C_NEXT]
        toks.append((C_NEXT, 2, emit([(1, 0, write_varint(new_id))])))
        toks.sort(key=lambda t: t[0])
        self.arcs[ai][1][mi][1] = emit(toks)
        # the thread's tail now points at the reply, so record the dependency
        info = self.arcs[ai][0]
        rebuilt = []
        for n, w, v in tokenize(info):
            if n == 2:
                inner = [(n2, w2, v2) for n2, w2, v2 in tokenize(v) if n2 != 5]
                body_len = len(self.arcs[ai][1][mi][1])
                inner = [(n2, w2, write_varint(body_len) if n2 == 3 else v2)
                         for n2, w2, v2 in inner]
                inner.append((5, 2, packed([author, new_id])))
                inner.sort(key=lambda t: t[0])
                v = emit(inner)
            rebuilt.append((n, w, v))
        self.arcs[ai][0] = emit(rebuilt)
        return new_id

    def delete_comment(self, ref_id):
        self.set_comment_table([r for r in self.comment_table()
                                if r[2] != ref_id])

    def format_run(self, start, end, bold=False, italic=False):
        """Apply bold/italic across [start, end) of the selected storage.

        Neither bold nor italic means plain: a null entry, "no override from
        here", which hands the text back to its paragraph style. No
        character style is needed for that -- looking one up for
        (False, False) never found anything.

        The range becomes one run: entries inside it are dropped, or an
        italic word in the middle would win back its own span.
        """
        if end <= start:
            return 0
        want = None
        if bold or italic:
            # the reader parsed the stylesheet once already; re-reading the
            # file here cost a full parse per emphasis run during import
            want = char_style_ids(self.reader).get((bold, italic))
            if want is None:
                name = "/".join(k for k, on in (("bold", bold),
                                                ("italic", italic)) if on)
                sys.exit(f"this document has no plain {name} character style "
                         "to reuse; apply it once in Pages and try again")
        val = parse_fields_of(self._msg).get(F_CHAR_TBL, [None])[0]
        after = char_value_at(val, end)             # read before changing
        kept = [(i, sub) for i, _r, sub in entry_rows(val)
                if not start <= i <= end]
        kept.append((start, entry_bytes(start, want)))
        if end < len(self.text()[0]):
            kept.append((end, entry_bytes(end, after)))
        if val is None and start > 0:
            kept.append((0, entry_bytes(0)))       # a new table starts at 0
        kept.sort(key=lambda r: r[0])
        self._put_field(F_CHAR_TBL, emit([(1, 2, sub) for _i, sub in kept]))
        return 1

    def _put_field(self, field, value):
        """Replace a field of the selected storage, or add it in field order."""
        rebuilt, seen = [], False
        for num, wire, val in tokenize(self._msg):
            if num == field:
                val, seen = value, True
            rebuilt.append((num, wire, val))
        if not seen:
            at = next((i for i, (n2, _w, _v) in enumerate(rebuilt)
                       if n2 > field), len(rebuilt))
            rebuilt.insert(at, (field, 2, value))
        self._msg = emit(rebuilt)

    @staticmethod
    def _restate_following(val, write_at, keep, bound):
        """Re-state `keep` at `write_at` after a structural edit.

        The three structural operations (retag, insert, delete) each mutate
        a style table and then need to undo the inheritance that mutation
        would otherwise hand to the paragraph right after the edit -- unless
        that paragraph already states its own style, or the edit ran past
        where the text still extends. One shared place for that rule, rather
        than three independently maintained copies (which had already
        drifted: only two of the three carried the bound check).
        """
        if keep is not None and write_at < bound and not states_own_style(val, write_at):
            return set_style_at(val, write_at, keep)
        return val

    def retag(self, offset, style_id, list_id=None):
        """Give the paragraph containing `offset` a different style.

        `list_id` also sets the list style -- without it, retagging a bullet
        to a heading leaves the bullet in place.
        """
        self._require_single_chunk()
        raw = self.text()[0]
        start, _end = self.paragraph_bounds(offset)
        after = min(_end + 1, len(raw))
        rebuilt = []
        for num, wire, val in tokenize(self._msg):
            new_id = (style_id if num == F_PARA_TBL else
                      list_id if num == F_LIST_TBL else None)
            if new_id is not None:
                # the next paragraph may inherit from this one, so pin it to
                # what it resolves to today before changing anything
                keep = effective_style_at(val, after)
                val = set_style_at(val, start, new_id)
                val = self._restate_following(val, after, keep, len(raw))
            rebuilt.append((num, wire, val))
        self._msg = emit(rebuilt)
        return start

    def flow(self):
        """The selected storage's text with footnote references neutralised.

        Paragraph boundaries are found on this view (see pages2md.flow_view);
        the real text is still what gets edited.
        """
        raw = self.text()[0]
        val = parse_fields_of(self._msg).get(F_ATTACHMENTS, [None])[0]
        return flow_view(raw, {i for i, ref, _s in entry_rows(val)
                               if ref is not None
                               and raw[i:i + 1] == FOOTNOTE_MARK})

    def paragraph_bounds(self, offset):
        """(start, end) of the paragraph containing `offset`."""
        return para_bounds(self.flow(), offset)

    def paragraph_starts(self, lo, hi):
        """Offsets of every paragraph beginning inside [lo, hi)."""
        out, pos = [], 0
        for chunk in PARA_SPLIT.split(self.flow()):
            if lo <= pos < hi:
                out.append(pos)
            pos += len(chunk) + 1
        return out

    def clear_range(self, lo, hi):
        """Delete every paragraph that begins inside [lo, hi).

        Done back to front through `delete_paragraph` so each removal reuses
        the verified single-paragraph path -- including its re-statement of
        the following paragraph's style -- and earlier offsets stay valid.
        """
        removed = 0
        for start in reversed(self.paragraph_starts(lo, hi)):
            if self.delete_paragraph(start)[1]:
                removed += 1
        return removed

    def insert_paragraph(self, at, text, style_id=None, list_id=None):
        """Insert a whole paragraph starting at `at` (a paragraph boundary)."""
        self._require_single_chunk()
        raw = self.text()[0]
        text = u16(text)
        # at the very end there may be no terminator to insert after, so the
        # new text would run on into the last paragraph
        lead = "" if (at == 0 or raw[at - 1:at] in (PARA_END, "")) else PARA_END
        body = lead + text + PARA_END
        delta = len(body)
        rebuilt = []
        for num, wire, val in tokenize(self._msg):
            if num != F_TEXT and wire == 2:
                # Read what is in force here BEFORE shifting: afterwards the
                # entry that sat at `at` has moved to `at + delta`, and the
                # nearest remaining one is whatever precedes the insertion --
                # for a run of bullets, that is the bullet just inserted.
                keep = (effective_style_at(val, at)
                        if num in (F_PARA_TBL, F_LIST_TBL) else None)
                val, _n = shift_table(val, at, at, delta)
                new_id = (style_id if num == F_PARA_TBL else
                          list_id if num == F_LIST_TBL else None)
                if new_id is not None:
                    val = set_style_at(val, at + len(lead), new_id)
                    # the following paragraph inherits unless it re-states
                    # what was in force before the insertion
                    val = self._restate_following(val, at + delta, keep,
                                                  len(raw) + delta)
                elif num in RUN_TABLES and table_kind(val) == "index":
                    # a new paragraph starts plain and outside any change
                    val = isolate_insertion(val, at, delta, len(raw) + delta)
            rebuilt.append((num, wire, val))
        self._msg = emit(rebuilt)
        self._write_text(raw[:at] + body + raw[at:])
        return at + len(lead), delta

    def delete_paragraph(self, offset):
        """Remove the paragraph containing `offset`, newline included."""
        self._require_single_chunk()
        raw = self.text()[0]
        start, end = self.paragraph_bounds(offset)
        if raw[end:end + 1] == PARA_END:    # its own terminator, not the
            end += 1                        # next paragraph's leading break
        if end == start:
            # The empty slot PARA_SPLIT yields just before a page break or
            # anchor: nothing to delete. Carrying on would still write a
            # style entry onto the break character itself.
            return start, 0
        delta = -(end - start)
        bound = len(raw) + delta
        rebuilt = []
        for num, wire, val in tokenize(self._msg):
            if num != F_TEXT and wire == 2:
                # the next paragraph may inherit from the one being removed,
                # so pin it to what it resolves to today -- its list style as
                # much as its paragraph style
                styled = num in (F_PARA_TBL, F_LIST_TBL)
                keep = effective_style_at(val, end) if styled else None
                run = num in RUN_TABLES and table_kind(val) == "index"
                carry = char_value_at(val, end) if run else None
                val = drop_entries_in(val, start, end)
                val, _n = shift_table(val, start, end, delta)
                if styled:
                    val = self._restate_following(val, start, keep, bound)
                elif run:
                    val = carry_run(val, start, carry, bound)
            rebuilt.append((num, wire, val))
        self._msg = emit(rebuilt)
        self._write_text(raw[:start] + raw[end:])
        return start, delta

    def new_change(self, kind):
        """Clone a change archive with a fresh id, timestamp and UUID."""
        info, msg = self._change_template(kind)
        if msg is None:
            sys.exit("this document has no existing tracked change to model a "
                     "new one on; make one edit in Pages with tracking on first")
        now = time.time() - APPLE_EPOCH
        toks = []
        for n, w, v in tokenize(msg):
            if n == 3:                      # nested fixed64 timestamp
                v = emit([(1, 1, struct.pack("<d", now))])
            elif n == 4:                    # change UUID
                v = str(uuid.uuid4()).upper().encode()
            toks.append((n, w, v))
        body = emit(toks)
        ident = self.next_id
        self.next_id += 1
        new_info = []
        for n, w, v in tokenize(info):
            if n == 1:
                v = write_varint(ident)
            elif n == 2:                    # MessageInfo: rewrite payload length
                v = emit([(n2, w2, write_varint(len(body)) if n2 == 3 else v2)
                          for n2, w2, v2 in tokenize(v)])
            new_info.append((n, w, v))
        self.arcs.append([emit(new_info), [[T_CHANGE, body]]])
        return ident

    def text(self):
        """(UTF-16 view of the selected storage's text, chunk count).

        The view's indices are the attribute tables' indices, so every
        offset below is in Pages' own units. Anything typed by the user goes
        through u16() before it meets this text, and anything shown goes
        through show().
        """
        chunks = [v for num, _w, v in tokenize(self._msg) if num == F_TEXT]
        return u16(b"".join(chunks).decode("utf-8")), len(chunks)

    def deletions(self):
        """Tracked-change deletion spans, so we can show the accepted view."""
        for num, _w, val in tokenize(self._msg):
            if num == F_DELETIONS:
                return [(a, b) for a, b, _ref in spans_of(val)]
        return []

    def accepted_map(self):
        """(accepted text, [raw index for each accepted char]).

        Built from the deletion spans directly: the per-character set
        membership test this replaced walked all 83,000 characters.
        """
        raw, _ = self.text()
        spans = self.deletions()
        if not spans:
            return raw, list(range(len(raw)))
        parts, keep, pos = [], [], 0
        for a, b in spans:
            if a > pos:
                parts.append(raw[pos:a])
                keep.extend(range(pos, a))
            pos = max(pos, b)
        parts.append(raw[pos:])
        keep.extend(range(pos, len(raw)))
        return "".join(parts), keep

    # -- editing ---------------------------------------------------------
    def apply(self, edits):
        """Apply [(raw_start, raw_end, new_text)]; returns a report."""
        raw, chunks = self.text()
        if chunks != 1:
            sys.exit(f"{self.path}: body text is split across {chunks} chunks; "
                     "not supported")
        report = []
        # right-to-left so earlier offsets stay valid
        for start, end, new in sorted(edits, reverse=True):
            start, end, new = minimal_edit(raw, start, end, new)
            delta = len(new) - (end - start)
            raw = raw[:start] + new + raw[end:]
            moved = 0
            rebuilt = []
            for num, wire, val in tokenize(self._msg):
                if num in (F_TEXT,) or wire != 2:
                    rebuilt.append((num, wire, val))
                    continue
                val, n = shift_table(val, start, end, delta)
                if (start == end and num in CHANGE_TABLES
                        and table_kind(val) == "index"):
                    # untracked text typed at a change's edge is not part of it
                    val = isolate_insertion(val, start, delta, len(raw))
                moved += n
                rebuilt.append((num, wire, val))
            self._msg = emit(rebuilt)
            report.append((start, end, new, delta, moved))
        self._write_text(raw)
        return report

    def apply_tracked(self, edits):
        """Record edits as native Pages tracked changes.

        The original text stays and the replacement is inserted after it, so
        the edit is a pure insertion at `end` plus a deletion mark over
        [start, end) -- which is exactly how Pages stores its own.
        """
        raw, chunks = self.text()
        if chunks != 1:
            sys.exit(f"{self.path}: body text is split across {chunks} chunks; "
                     "not supported")
        report = []
        for start, end, new in sorted(edits, reverse=True):
            delta = len(new)
            # a pure deletion inserts nothing and a pure insertion deletes
            # nothing; minting a change for the empty side produced an
            # unterminated span that marked unrelated text
            del_id = self.new_change(2) if end > start else None
            ins_id = self.new_change(1) if delta else None
            moved = 0
            rebuilt = []
            seen = set()
            for num, wire, val in tokenize(self._msg):
                if num == F_TEXT or wire != 2:
                    rebuilt.append((num, wire, val))
                    continue
                val, n = shift_table(val, end, end, delta)   # pure insertion
                moved += n
                if num in CHANGE_TABLES and table_kind(val) == "index":
                    # the new text belongs to this edit's insertion only, not
                    # to a change that happens to end where it goes
                    val = isolate_insertion(val, end, delta, len(raw) + delta)
                if num == F_DELETIONS:
                    val = put_span(val, start, end, del_id)
                    seen.add(num)
                elif num == F_INSERTIONS:
                    val = put_span(val, end, end + delta, ins_id)
                    seen.add(num)
                rebuilt.append((num, wire, val))

            # A storage that has never carried a change of this kind has no
            # table for it -- margin notes typically lack the deletions field.
            # Without this, the replacement text is inserted but the original
            # is never marked deleted, so both end up visible.
            for num, span in ((F_DELETIONS, (start, end, del_id)),
                              (F_INSERTIONS, (end, end + delta, ins_id))):
                if num in seen or span[2] is None:
                    continue
                token = (num, 2, put_span(None, *span))
                at = next((i for i, (n2, _w, _v) in enumerate(rebuilt)
                           if n2 > num), len(rebuilt))
                rebuilt.insert(at, token)
            self._msg = emit(rebuilt)
            raw = raw[:end] + new + raw[end:]
            report.append((start, end, new, delta, moved))
        self._write_text(raw)
        return report

    def _require_single_chunk(self):
        """_write_text only replaces the first text chunk."""
        if self.text()[1] != 1:
            sys.exit(f"{self.path}: this storage's text is split across "
                     "chunks; not supported")

    def _write_text(self, raw):
        rebuilt, done = [], False
        for num, wire, val in tokenize(self._msg):
            if num == F_TEXT and not done:
                # strict: a half-pair here means an edit split an emoji
                val, done = from_u16(raw).encode("utf-8"), True
            rebuilt.append((num, wire, val))
        self._msg = emit(rebuilt)

    def save(self, out_path):
        payload = pack_archives(self.arcs)
        self.entries[BODY_ENTRY] = iwa_encode(payload)
        tmp = out_path + ".tmp"
        with zipfile.ZipFile(tmp, "w") as z:
            for n in self.names:          # preserve order and storage method
                z.writestr(n, self.entries[n],
                           compress_type=self.compress.get(n, zipfile.ZIP_STORED))
        os.replace(tmp, out_path)


# Structure and pagination are reading concerns, so they live in pages2md.
from pages2md import (outline, section_range, index_path, load_index,
                      text_fingerprint,
                      build_index, page_of, page_range, pages_has_open,
                      fingerprint_parts, style_ids, list_style_ids,
                      char_style_ids,
                      # one definition of the reverse-engineered field numbers:
                      # two tables that must agree is the worst failure mode
                      # this codebase has (reads fine, writes corrupt)
                      T_STORAGE, T_ATTACHMENT, T_COMMENT_REF, T_COMMENT,
                      F_TEXT, F_PARA_TBL, F_LIST_TBL, F_COMMENTS,
                      F_INSERTIONS, F_DELETIONS, F_COMMENTS_RUN,
                      C_TEXT, C_DATE, C_AUTHOR, C_NEXT, APPLE_EPOCH,
                      PARA_BREAKS, PARA_SPLIT, F_ATTACHMENTS, FOOTNOTE_MARK,
                      flow_view,
                      # text is handled as a UTF-16 view; see pages2md
                      u16, from_u16, show)
from pages2md import _ref as ref_of   # skip a TSP.Reference's tag byte, read the varint

# Run-length tables of references where a null entry means "no value from
# here": the run in force is exactly what the entries say, so a structural
# edit has to carry it explicitly. Paragraph and list styles are the other
# kind (null = "no change") and get _restate_following instead.
# Comments join them when a storage keys them run-length (field 23), which
# Pages 15.4 does for the body too, not only for text boxes.
RUN_TABLES = (F_CHAR_TBL, F_INSERTIONS, F_DELETIONS, F_COMMENTS_RUN)
CHANGE_TABLES = (F_INSERTIONS, F_DELETIONS)


# ------------------------------------------------------ fingerprint and plans
def fingerprint(doc):
    """Short hash of the body text.

    Of the text rather than the file, because Pages rewrites the whole package
    on every save: the zip bytes change when nothing you care about did. The
    definition lives in pages2md so the reader's page index and this agree.
    """
    return text_fingerprint(doc.reader)


def scope_from(path, in_section, page, text_len):
    """Resolve a section name or page spec into a character range."""
    if in_section and page:
        sys.exit("an edit can carry `in` or `page`, not both")
    if in_section:
        title, lo, hi = section_range(path, in_section, text_len)
        return (lo, hi), f"in {title!r}"
    if page:
        lo, hi = page_range(path, page, text_len)
        return (lo, hi), f"page {page}"
    return None, "whole document"


def load_plan(path):
    """-> (expected fingerprint or None, [entry, ...])

    Accepts either a bare list of edits or an object with `edits` and an
    optional `fingerprint` to pin the plan to the text it was written against.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"{path}: no such plan file")
    except json.JSONDecodeError as exc:
        sys.exit(f"{path}: not valid JSON -- {exc}")
    if isinstance(data, list):
        return None, data
    if not isinstance(data, dict) or "edits" not in data:
        sys.exit(f"{path}: expected a list of edits, or an object with `edits`")
    if not isinstance(data["edits"], list):
        sys.exit(f"{path}: `edits` must be a list")
    return data.get("fingerprint"), data["edits"]


KNOWN_KEYS = {"find", "replace", "in", "page", "all", "occurrence", "regex",
              "raw", "why", "where"}


def resolve_plan(doc, path, entries):
    """Resolve every entry against the *original* text.

    All offsets come from one unedited snapshot, so the plan means the same
    thing however its entries are ordered, and reviewing the dry run tells you
    exactly what will be written.
    """
    text_len = len(doc.text()[0])
    resolved = []
    for n, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            sys.exit(f"edit {n}: expected an object, got {type(entry).__name__}")
        unknown = set(entry) - KNOWN_KEYS
        if unknown:
            sys.exit(f"edit {n}: unknown key(s) {sorted(unknown)}; "
                     f"known keys are {sorted(KNOWN_KEYS)}")
        for required in ("find", "replace"):
            if required not in entry:
                sys.exit(f"edit {n}: missing `{required}`")
        scope, where = scope_from(path, entry.get("in"), entry.get("page"),
                                  text_len)
        which = None if entry.get("all") else (entry.get("occurrence") or 1)
        groups = resolve(doc, entry["find"], entry["replace"], which,
                         bool(entry.get("raw")), bool(entry.get("regex")),
                         scope, label=f"edit {n}",
                         where=entry.get("where", "all"))
        resolved.append((n, entry, where, groups))

    # Overlapping edits would corrupt each other, and the order they were
    # written in should not decide the outcome.
    flat = sorted(((h, rs, re_, n) for n, _e, _w, gs in resolved
                   for h, eds in gs for rs, re_, _new, _old in eds))
    for (a_h, a_s, a_e, a_n), (b_h, b_s, b_e, b_n) in zip(flat, flat[1:]):
        if a_h == b_h and b_s < a_e:
            sys.exit(f"edits {a_n} and {b_n} overlap at characters "
                     f"{b_s}-{min(a_e, b_e)}; they cannot both be applied")
    return resolved


def describe_plan(resolved, expected, actual, path):
    total = sum(len(eds) for _n, _e, _w, gs in resolved for _h, eds in gs)
    print(f"plan: {path}  ({len(resolved)} entries, {total} replacements)")
    if expected:
        ok = "matches" if expected == actual else "DOES NOT MATCH"
        print(f"fingerprint: {expected} {ok} the document ({actual})")
    else:
        print(f"fingerprint: not pinned (document is {actual})")
    for n, entry, where, gs in resolved:
        why = f"  — {entry['why']}" if entry.get("why") else ""
        print(f"\n{n}. {where}{why}")
        for handle, eds in gs:
            for rs, _re, new, old in eds:
                tag = "" if handle == "body" else f" [{handle}]"
                print(f"   @{rs:<6}{tag} {show(old)!r}")
                print(f"   {'':7} -> {show(new)!r}")
    return total


# ------------------------------------------------- internal source control
def config_path(doc):
    return os.path.join(os.path.dirname(os.path.abspath(doc)) or ".", CONFIG_NAME)


def load_config(doc):
    try:
        with open(config_path(doc), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"vcs": False}


def save_config(doc, cfg):
    with open(config_path(doc), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")


def vcs_root(doc):
    return os.path.join(os.path.dirname(os.path.abspath(doc)) or ".", VCS_DIR)


def git(root, *args, check=True):
    return subprocess.run(["git", "-C", root, *args], check=check,
                          capture_output=True, text=True)


def vcs_init(doc):
    """A git repo dedicated to document snapshots, separate from the project's."""
    root = vcs_root(doc)
    if not os.path.isdir(os.path.join(root, ".git")):
        os.makedirs(root, exist_ok=True)
        git(root, "init", "-q")
        git(root, "config", "user.name", "pages_edit")
        git(root, "config", "user.email", "pages_edit@localhost")
        with open(os.path.join(root, ".gitattributes"), "w") as fh:
            fh.write("*.pages binary\n")
        git(root, "add", ".gitattributes")
        git(root, "commit", "-q", "-m", "Initialise document history")
    return root


def vcs_snapshot(doc, message):
    """Commit the document's current bytes. Returns the short hash, or None."""
    root = vcs_init(doc)
    name = os.path.basename(doc)
    shutil.copy2(doc, os.path.join(root, name))
    git(root, "add", "--", name)
    if not git(root, "status", "--porcelain", "--", name).stdout.strip():
        return None                                   # nothing changed
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "--short", "HEAD").stdout.strip()


# ---------------------------------------------------------------- commands
def matches(doc, pattern, use_raw, regex, scope, anchor=None):
    """Raw matches in the currently selected storage.

    Returns [(raw_start, raw_end, accepted_offset, span)]. Guards are applied
    later, by check_edits, so that an unrelated match cannot block the one you
    actually asked for.
    """
    if use_raw:
        hay, keep = doc.text()[0], None
    else:
        hay, keep = doc.accepted_map()
    # MULTILINE so ^ and $ anchor to paragraphs, which is what a caller means
    pattern = u16(pattern)
    rx = re.compile(pattern if regex else re.escape(pattern), re.MULTILINE)
    out = []
    for h in rx.finditer(hay.translate(SEARCH_VIEW)):
        s, e = h.start(), h.end()
        if keep is None:
            rs, re_ = s, e
        elif e == s:                       # zero-width: an insertion point
            rs = re_ = keep[s] if s < len(keep) else len(hay)
        else:
            rs, re_ = keep[s], keep[e - 1] + 1
        if scope:
            lo, hi = scope
            # A margin note is in scope when its anchor is: its own offsets
            # live in a different space from the body's.
            inside = (lo <= anchor < hi) if anchor is not None else (
                rs >= lo and re_ <= hi)
            if not inside:
                continue
        out.append((rs, re_, s, e - s, hay[s:e], h))
    return out


def check_edits(doc, selected, replacement, regex, label=None):
    """Turn selected matches into edits, refusing the unsafe ones."""
    tag = f"{label}: " if label else ""
    raw = doc.text()[0]
    edits = []
    for rs, re_, acc, span, old, h in selected:
        if replacement is not None:
            replacement = u16(replacement)
        new = (h.expand(replacement) if regex and replacement is not None
               else replacement)
        if splits_pair(raw, rs) or splits_pair(raw, re_):
            sys.exit(f"{tag}match at offset {rs} starts or ends inside an "
                     "emoji or other character outside the BMP; widen it to "
                     "the whole character")
        if re_ - rs != span:
            sys.exit(f"{tag}match at accepted offset {acc} spans a tracked "
                     "deletion; resolve that edit in Pages first, or use --raw")
        if any(c in raw[rs:re_] for c in BREAKS):
            sys.exit(f"{tag}match at offset {rs} contains a page break, object "
                     "anchor or manual line break; replacing it would delete "
                     "that character. Narrow the match to one side of it.")
        edits.append((rs, re_, new, old))
    return edits


def splits_pair(view, i):
    """True if offset `i` falls between the two halves of a surrogate pair."""
    return ("\udc00" <= view[i:i + 1] <= "\udfff"
            and "\ud800" <= view[i - 1:i] <= "\udbff")


def minimal_edit(raw, start, end, new):
    """Shrink a replacement to the part that actually changes.

    Text shared at both ends of old and new is left alone, so formatting
    that starts or ends inside it stays on the same characters. Replacing
    "xx Bold" with "x Bold" otherwise clamps the bold run's start to a
    position inside the replacement and bolds "old" instead of "Bold".
    Never trims to a boundary inside a surrogate pair: two emoji can share
    their first half.
    """
    old = raw[start:end]
    room = min(len(old), len(new))
    p = 0
    while p < room and old[p] == new[p]:
        p += 1
    if p and "\ud800" <= old[p - 1] <= "\udbff":
        p -= 1
    q = 0
    while q < room - p and old[-1 - q] == new[-1 - q]:
        q += 1
    if q and "\udc00" <= old[len(old) - q] <= "\udfff":
        q -= 1
    return start + p, end - q, new[p:len(new) - q]


def search(doc, pattern, use_raw, regex, scope, handles):
    """Search several storages; -> [(handle, match tuple), ...] in reading order."""
    out = []
    for handle in handles:
        doc.select(handle)
        for m in matches(doc, pattern, use_raw, regex, scope,
                         doc.anchors.get(handle)):
            out.append((handle, m))
    return out


def handles_for(doc, where):
    """Which storages a `where` setting covers."""
    notes = [h for h in doc.slots if h.startswith("note")]
    if where == "body":
        return ["body"]
    if where in ("notes", "sidenotes"):
        return notes
    if where == "other":
        return [h for h in doc.slots if h.startswith("other")]
    if where == "all":
        return list(doc.slots)
    if where in doc.slots:
        return [where]
    sys.exit(f"--where expects body, notes, other, all, or a single handle "
             f"({', '.join(doc.slots)}), not {where!r}")


def resolve(doc, pattern, replacement, which, use_raw, regex, scope=None,
            label=None, where="all"):
    """Locate matches across storages and map them onto raw offsets.

    -> [(handle, [(raw_start, raw_end, new, old), ...]), ...]
    """
    tag = f"{label}: " if label else ""
    handles = handles_for(doc, where)
    found = search(doc, pattern, use_raw, regex, scope, handles)

    if not found:
        doc.select("body")
        hint = ""
        if not use_raw and u16(pattern) in doc.text()[0]:
            hint = ("  (it does appear in the raw text -- tracked changes may "
                    "split it; try --raw)")
        extra = "" if where == "all" else f" of {where}"
        sys.exit(f"{tag}no match for {pattern!r} in the "
                 f"{'raw' if use_raw else 'accepted'} text{extra}{hint}")

    if which is not None:
        if not 1 <= which <= len(found):
            sys.exit(f"{tag}occurrence {which} out of range (1..{len(found)})")
        found = [found[which - 1]]

    grouped = {}
    for handle, m in found:
        grouped.setdefault(handle, []).append(m)
    out = []
    for handle, ms in grouped.items():
        doc.select(handle)
        out.append((handle, check_edits(doc, ms, replacement, regex, label)))
    doc.select("body")
    return out


def wanted_scope(args, doc):
    """Resolve --in / --page into a character range, or None."""
    scope, where = scope_from(args.file, getattr(args, "in_section", None),
                              getattr(args, "page", None),
                              len(doc.text()[0]))
    if scope:
        print(f"scope: {where}  chars {scope[0]}-{scope[1]}")
    return scope


def flatten(groups):
    """[(handle, edits)] -> flat [(rs, re_, new, old)] for reporting."""
    return [e for _h, eds in groups for e in eds]


def apply_groups(doc, groups, track):
    """Apply each storage's edits, then save once."""
    report = []
    for handle, eds in groups:
        doc.select(handle)
        todo = [(rs, re_, new) for rs, re_, new, _old in eds]
        report += (doc.apply_tracked(todo) if track else doc.apply(todo))
    doc.select("body")
    return report


def label_of(doc, handle):
    if handle == "body":
        return ""
    anchor = doc.anchors.get(handle)
    if anchor is None:          # captions and the like sit outside the flow
        return f" [{handle}]"
    return f" [{handle}, anchored @{anchor}]"


def cmd_find(args):
    doc = Document(args.file)
    scope = wanted_scope(args, doc)
    bounds = load_index(args.file)
    found = []
    for handle in handles_for(doc, args.where):
        doc.select(handle)
        hay = doc.text()[0] if args.raw else doc.accepted_map()[0]
        for m in matches(doc, args.pattern, args.raw, args.regex, scope,
                         doc.anchors.get(handle)):
            found.append((handle, hay, m[5], m[0]))
    doc.select("body")
    print(f"{len(found)} match(es) in the "
          f"{'raw' if args.raw else 'accepted'} text")
    for n, (handle, hay, h, rs) in enumerate(found, 1):
        lo, hi = max(h.start() - 45, 0), min(h.end() + 45, len(hay))
        pre = show(hay[lo:h.start()]).replace("\n", "⏎")
        mid = show(hay[h.start():h.end()])
        post = show(hay[h.end():hi]).replace("\n", "⏎")
        page = page_of(bounds, rs if handle == "body" else doc.anchors[handle])
        where = f"p.{page} " if page else ""
        print(f"  {n:3}. {where}@{rs:<6}{label_of(doc, handle)} "
              f"…{pre}[{mid}]{post}…")


def cmd_replace(args):
    doc = Document(args.file)
    # a file almost always ends with a newline the author did not mean to match
    pattern = (args.find if args.find is not None
               else open(args.find_file, encoding="utf-8").read().rstrip("\n"))
    repl = (args.replace if args.replace is not None
            else open(args.replace_file, encoding="utf-8").read().rstrip("\n"))
    which = None if args.all else (args.occurrence or 1)
    groups = resolve(doc, pattern, repl, which, args.raw, args.regex,
                     wanted_scope(args, doc), where=args.where)
    edits = flatten(groups)

    cfg_pre = load_config(args.file)
    mode = (cfg_pre.get("track", False) if args.track is None else args.track)
    print(f"{len(edits)} replacement(s) in {args.file}"
          f" ({'tracked change' if mode else 'direct replacement'}):")
    for handle, eds in groups:
        for rs, re_, new, old in eds:
            print(f"  @{rs}{label_of(doc, handle)}  {show(old)!r}  ->  "
                  f"{show(new)!r}")
            for line in difflib.unified_diff([show(old) + "\n"],
                                             [show(new) + "\n"],
                                             "before", "after", n=0,
                                             lineterm="\n"):
                if line.startswith(("+", "-")) and not line.startswith(
                        ("+++", "---")):
                    print("      " + line.rstrip())

    if not args.write:
        print("\ndry run — pass --write to apply")
        return

    check_fingerprint(doc, args.expect, "you took that fingerprint")
    # Pages writes the whole package on its next save, which would silently
    # discard this edit. Refuse rather than lose work.
    if pages_has_open(args.file):
        sys.exit(f"\nPages has {os.path.basename(args.file)} open — it would "
                 "overwrite this edit on its next save. Close it first.")

    cfg = load_config(args.file)
    before = None
    if cfg.get("vcs"):
        before = vcs_snapshot(args.file, "Before: " + _summary(edits))
        print(f"\nsnapshot of the current version: {before or '(already current)'}")
    elif not args.no_backup:
        bak = args.file + ".bak"
        shutil.copy2(args.file, bak)
        print(f"\nbackup: {bak}   (enable history with: config --vcs on)")

    track = cfg.get("track", False) if args.track is None else args.track
    report = apply_groups(doc, groups, track)
    doc.save(args.file)
    total = sum(r[4] for r in report)
    print(f"wrote {args.file}: {len(report)} edit(s)"
          f"{' as tracked changes' if track else ''}, "
          f"{total} character indices shifted")

    if cfg.get("vcs"):
        after = vcs_snapshot(args.file, _summary(edits))
        print(f"committed: {after}")
    print(f"new fingerprint: {verify(args.file, edits, track)}")


def _summary(edits):
    first = edits[0]
    more = f" (+{len(edits) - 1} more)" if len(edits) > 1 else ""
    return f"Replace {show(first[3])!r} -> {show(first[2])!r}{more}"


def verify(path, edits, tracked=False):
    """Re-open the written file and confirm the new text is readable."""
    try:
        doc = Document(path)
        # every storage, not just the body: an edit may live in a margin note
        parts = []
        for handle in doc.slots:
            doc.select(handle)
            parts.append(doc.accepted_map()[0])
        text = "\x00".join(parts)
    except Exception as exc:
        print(f"VERIFY FAILED: cannot re-read {path}: {exc}")
        return "?"
    missing = [new for _rs, _re, new, _old in edits if new and new not in text]
    stale = [old for _rs, _re, _new, old in edits if tracked and old in text]
    notes = []
    if missing:
        notes.append(f"MISSING {missing!r}")
    if stale:
        notes.append(f"original still in the accepted text: {stale!r}")
    print("verified: re-read OK, "
          + ("; ".join(notes) if notes else
             "accepted text shows the replacement"
             + (", original preserved as a pending deletion" if tracked else "")))
    return fingerprint(doc)          # reuse the document we just opened


def check_fingerprint(doc, expected, source):
    """Refuse to write against text that has moved since the plan was made."""
    actual = fingerprint(doc)
    if expected and expected != actual:
        sys.exit(f"the document has changed since {source} "
                 f"(expected {expected}, found {actual}).\n"
                 "Its offsets may no longer mean what they did -- re-read the "
                 "document and rebuild the plan.")
    return actual


def cmd_fingerprint(args):
    doc = Document(args.file)
    print(fingerprint(doc))


def cmd_plan(args):
    doc = Document(args.file)
    expected, entries = load_plan(args.plan)
    if not entries:
        sys.exit(f"{args.plan}: no edits in the plan")
    # Fail fast: if the text moved, the plan's matches and offsets are
    # meaningless, and a resolve error here would be deeply confusing.
    actual = check_fingerprint(doc, expected, f"{args.plan} was written")
    resolved = resolve_plan(doc, args.file, entries)
    total = describe_plan(resolved, expected, actual, args.plan)

    if not args.write:
        print("\ndry run — pass --write to apply")
        return

    if pages_has_open(args.file):
        sys.exit(f"\nPages has {os.path.basename(args.file)} open — it would "
                 "overwrite these edits on its next save. Close it first.")

    groups = {}
    for _n, _e, _w, gs in resolved:
        for handle, eds in gs:
            groups.setdefault(handle, []).extend(eds)
    groups = list(groups.items())
    flat = flatten(groups)
    cfg = load_config(args.file)
    summary = f"Apply {os.path.basename(args.plan)}: {total} replacement(s)"
    if cfg.get("vcs"):
        vcs_snapshot(args.file, "Before: " + summary)
    elif not args.no_backup:
        shutil.copy2(args.file, args.file + ".bak")
        print(f"\nbackup: {args.file}.bak")

    track = cfg.get("track", False) if args.track is None else args.track
    # One pass for the whole plan: the edits were resolved against a single
    # snapshot, and applying them right-to-left keeps every offset valid.
    report = apply_groups(doc, groups, track)
    doc.save(args.file)
    shifted = sum(r[4] for r in report)
    print(f"\nwrote {args.file}: {len(report)} replacement(s)"
          f"{' as tracked changes' if track else ''}, "
          f"{shifted} character indices shifted")
    if cfg.get("vcs"):
        print(f"committed: {vcs_snapshot(args.file, summary)}")
    print(f"new fingerprint: {verify(args.file, flat, track)}")


def locate_comment(doc, args):
    """Find the comment the user means, in whichever storage holds it.

    Every storage in scope is searched. Stopping at the first one that had
    any comment made a note's comment unreachable whenever the body had one
    too. The document is left selected on the storage that holds it.
    """
    on = u16(args.on).lower() if args.at is None else None
    found = []
    for handle in handles_for(doc, getattr(args, "where", None) or "all"):
        doc.select(handle)
        raw = doc.text()[0]
        for row in doc.comment_table():
            if args.at is not None:
                hit = row[0] == args.at
            else:
                hit = on in raw[row[0]:row[0] + row[1]].lower()
            if hit:
                found.append((handle, row))
    what = (f"anchored at {args.at}" if args.at is not None
            else f"whose quoted text contains {args.on!r}")
    if not found:
        sys.exit(f"no comment {what}; run `pages2md.py --comments` to list them")
    if len(found) > 1:
        where = ", ".join(f"{h} @{r[0]}" for h, r in found)
        sys.exit(f"{len(found)} comments {what} ({where}); narrow it with "
                 f"{'--on' if args.at is not None else '--at'} or --where")
    handle, row = found[0]
    doc.select(handle)
    return row


def comment_preamble(args):
    doc = Document(args.file)
    if args.expect:
        check_fingerprint(doc, args.expect, "you took that fingerprint")
    return doc


def commit(doc, args, summary, describe=None):
    """Write the document under the standard safety protocol.

    Dry-run bail, refuse if Pages holds the file, snapshot (history or .bak),
    save, re-open to confirm it still parses, report the new fingerprint.
    `describe` renders the one line that differs per command.
    """
    if not args.write:
        print("\ndry run — pass --write to apply")
        return
    if pages_has_open(args.file):
        sys.exit(f"\nPages has {os.path.basename(args.file)} open — it would "
                 "overwrite this on its next save. Close it first.")
    cfg = load_config(args.file)
    if cfg.get("vcs"):
        vcs_snapshot(args.file, "Before: " + summary)
    elif not args.no_backup:
        shutil.copy2(args.file, args.file + ".bak")
        print(f"backup: {args.file}.bak")
    doc.save(args.file)
    print(f"wrote {args.file}: {summary}")
    if cfg.get("vcs"):
        print(f"committed: {vcs_snapshot(args.file, summary)}")
    check = Document(args.file)
    if describe:
        print(describe(check))
    print(f"new fingerprint: {fingerprint(check)}")


def finish_comment(doc, args, summary):
    commit(doc, args, summary,
           lambda d: f"verified: {sum(len(d.select(h).comment_table()) for h in d.slots)}"
                     " comment(s) in the document")


def finish_struct(doc, args, summary):
    commit(doc, args, summary,
           lambda d: f"verified: re-read OK, {len(d.text()[0]):,} characters")


def cmd_comment_add(args):
    doc = comment_preamble(args)
    groups = resolve(doc, args.on, None, 1, False, False,
                     wanted_scope(args, doc), where=args.where)
    (handle, eds), = groups
    start, end, _new, old = eds[0]
    if handle != "body":
        # Reading comments in a text box works. Writing one does not: Pages
        # discards it on the next save even though the table shape, the
        # comment archives and the MessageInfo dependency list are all
        # byte-identical in structure to one Pages wrote itself. Something
        # further is required that I have not identified, so refuse rather
        # than write something that silently disappears.
        sys.exit(f"{show(old)!r} is in {handle}, not the body flow. Comments can be "
                 "read there but not written: Pages drops a tool-written one "
                 "on its next save. Add it in Pages, or comment on the body "
                 "text that references it.")
    print(f"comment on @{start}{label_of(doc, handle)} “{show(old)}”:\n"
          f"  {args.text}")
    if args.write:
        doc.select(handle)
        doc.add_comment(start, end - start, args.text)
        doc.select("body")
    finish_comment(doc, args, f"Add comment on {show(old[:40])!r}")


def cmd_comment_reply(args):
    doc = comment_preamble(args)
    start, length, ref = locate_comment(doc, args)
    raw = doc.text()[0]
    print(f"reply to the comment on @{start} "
          f"“{show(raw[start:start + length][:60])}”:\n  {args.text}")
    if args.write:
        doc.reply_to(ref, args.text)
    finish_comment(doc, args, f"Reply to comment @{start}")


def cmd_comment_delete(args):
    doc = comment_preamble(args)
    start, length, ref = locate_comment(doc, args)
    raw = doc.text()[0]
    print(f"delete the comment on @{start} "
          f"“{show(raw[start:start + length][:60])}”")
    if args.write:
        doc.delete_comment(ref)
    finish_comment(doc, args, f"Delete comment @{start}")


def located_paragraph(doc, start):
    """(body text, paragraph start, paragraph end) for the paragraph at `start`.

    The same two-line lookup every structural command needs before it can
    describe or act on "the paragraph the user pointed at".
    """
    raw = doc.select("body").text()[0]
    ps, pe = doc.paragraph_bounds(start)
    return raw, ps, pe


def one_match(doc, args, needle):
    """Locate a single body match.

    Deliberately skips the replacement guards: nothing here rewrites the
    matched span, so a match sitting across a tracked deletion or a page
    break is still a perfectly good way to point at a paragraph.
    """
    doc.select("body")
    found = matches(doc, needle, False, False, wanted_scope(args, doc))
    if not found:
        sys.exit(f"no match for {needle!r} in the body text")
    if len(found) > 1:
        sys.exit(f"{needle!r} matches {len(found)} places "
                 f"({[m[0] for m in found[:5]]}…); be more specific "
                 "or use --in/--page")
    rs, re_, _acc, _span, old, _h = found[0]
    return rs, re_, None, old


def resolve_style(args):
    styles = style_ids(args.file)
    if args.style not in styles:
        sys.exit(f"unknown style {args.style!r}; this document has: "
                 + ", ".join(sorted(styles)))
    return styles[args.style]


def cmd_retag(args):
    doc = comment_preamble(args)
    start, end, _n, old = one_match(doc, args, args.on)
    style_id, display = resolve_style(args)
    lists = list_style_ids(args.file)
    list_id = None
    if args.list:
        if args.list not in lists:
            sys.exit(f"unknown list style {args.list!r}; this document has: "
                     + ", ".join(sorted(lists)))
        list_id = lists[args.list][0]
    raw, ps, pe = located_paragraph(doc, start)
    print(f"retag the paragraph at @{ps} as {args.style!r} ({display!r})"
          + (f", list {args.list!r}" if args.list else "") + ":")
    print(f"  {show(raw[ps:pe][:90])!r}")
    if args.write:
        doc.retag(start, style_id, list_id)
    finish_struct(doc, args, f"Retag @{ps} as {args.style}")


def cmd_insert(args):
    doc = comment_preamble(args)
    start, end, _n, old = one_match(doc, args, args.after or args.before)
    style_id, display = (resolve_style(args) if args.style else (None, "inherited"))
    raw, ps, pe = located_paragraph(doc, start)
    at = after_paragraph(raw, pe) if args.after else ps
    print(f"insert {'after' if args.after else 'before'} the paragraph at @{ps}"
          f", as {args.style or 'the surrounding style'} ({display}):")
    print(f"  anchor: {show(raw[ps:pe][:80])!r}")
    print(f"  new:    {args.text[:80]!r}")
    if args.write:
        doc.insert_paragraph(at, args.text, style_id)
    finish_struct(doc, args, f"Insert paragraph at @{at}")


def cmd_delete_para(args):
    doc = comment_preamble(args)
    start, end, _n, old = one_match(doc, args, args.on)
    raw, ps, pe = located_paragraph(doc, start)
    print(f"delete the paragraph at @{ps}:")
    print(f"  {show(raw[ps:pe][:110])!r}")
    if args.write:
        doc.delete_paragraph(start)
    finish_struct(doc, args, f"Delete paragraph @{ps}")


def cmd_format(args):
    doc = comment_preamble(args)
    start, end, _n, old = one_match(doc, args, args.on)
    kinds = [k for k, on in (("bold", args.bold), ("italic", args.italic)) if on]
    if not kinds and not args.plain:
        sys.exit("say what to apply: --bold, --italic, or --plain")
    if kinds and args.plain:
        sys.exit("--plain removes emphasis; it cannot be combined with "
                 "--bold or --italic")
    print(f"format @{start} as {', '.join(kinds) if kinds else 'plain'}:")
    print(f"  {show(old[:90])!r}")
    if args.write:
        doc.select("body")
        doc.format_run(start, end, bold=args.bold, italic=args.italic)
    finish_struct(doc, args, f"Format @{start} as "
                             f"{', '.join(kinds) if kinds else 'plain'}")


def cmd_import(args):
    doc = comment_preamble(args)
    with open(args.markdown, encoding="utf-8") as fh:
        blocks = parse_markdown(u16(fh.read()))     # runs in Pages' units
    if not blocks:
        sys.exit(f"{args.markdown}: nothing to import")
    styles, lists = style_ids(doc.reader), list_style_ids(doc.reader)
    unknown = {s for s, _l, _t, _r in blocks if s not in styles}
    if unknown:
        sys.exit(f"this document has no style(s) {sorted(unknown)}; it has: "
                 + ", ".join(sorted(styles)))

    doc.select("body")
    if args.replace_section:
        title, lo, hi = section_range(args.file, args.replace_section,
                                      len(doc.text()[0]))
        raw = doc.text()[0]
        going = doc.paragraph_starts(lo, hi)
        at = lo
        print(f"replace section {title!r} (chars {lo}-{hi}, "
              f"{len(going)} paragraph(s)) with {len(blocks)} from "
              f"{args.markdown}:")
        for s in going[:3]:
            _a, b = doc.paragraph_bounds(s)
            print(f"  - {show(raw[s:b][:66])!r}")
        if len(going) > 3:
            print(f"  - …and {len(going) - 3} more")
    else:
        start = one_match(doc, args, args.after or args.before)[0]
        raw, ps, pe = located_paragraph(doc, start)
        at = after_paragraph(raw, pe) if args.after else ps
        print(f"import {len(blocks)} paragraph(s) from {args.markdown} "
              f"{'after' if args.after else 'before'} @{ps}:")
        print(f"  anchor: {show(raw[ps:pe][:70])!r}")
    for style, listing, text, runs in blocks:
        tag = f"{style}/{listing}" if listing else style
        mark = f"  ({len(runs)} emphasis run(s))" if runs else ""
        print(f"  [{tag:<16}] {show(text[:60])}{mark}")

    if args.replace_section and len(going) > 3:
        sys.exit(
            f"--replace-section would remove {len(going)} paragraphs in one "
            "batch. Testing found that 10+ sequential paragraph deletions in "
            "a single write corrupt the document -- Pages opens and saves it, "
            "but every heading in the whole document loses its style. The "
            "cause is not yet found. Refusing rather than risk it; see "
            "tools/insights.md.")

    if args.write:
        if args.replace_section:
            doc.select("body")
            doc.clear_range(lo, hi)
        cursor = at
        for style, listing, text, runs in blocks:
            # state the list style on every imported paragraph: a plain
            # paragraph following bullets would otherwise inherit the bullet
            list_id = lists.get(listing or "None", (None, None))[0]
            para_at, delta = doc.insert_paragraph(cursor, text,
                                                  styles[style][0], list_id)
            for a, b, bold, italic in runs:
                doc.format_run(para_at + a, para_at + b, bold, italic)
            cursor += delta        # each paragraph follows the one before it
    what = (f"Replace section {args.replace_section!r} from "
            if args.replace_section else "Import from ")
    finish_struct(doc, args, what + os.path.basename(args.markdown)
                  + f" ({len(blocks)} paragraph(s))")


def cmd_outline(args):
    bounds = load_index(args.file)
    heads = outline(args.file)
    if not bounds:
        print("(no page index — run `index` to show page numbers)\n")
    for level, title, off in heads:
        if level > args.max_level:
            continue
        page = page_of(bounds, off)
        where = f"p.{page:<4}" if page else ""
        print(f"  {where}@{off:<6} {'  ' * (level - 1)}{'#' * level} {title}")
    print(f"\n{len(heads)} headings")


def cmd_index(args):
    doc = Document(args.file)
    raw = doc.text()[0]
    print("asking Pages to lay out the document (this takes a moment)…")
    bounds, pages, aligned = build_index(args.file, raw)
    pct = 100.0 * aligned / max(len(raw), 1)
    print(f"indexed {pages} pages; aligned {aligned:,} of {len(raw):,} "
          f"characters ({pct:.1f}%)")
    if pct < 99:
        print("  warning: alignment is incomplete, so page numbers may drift")
    print(f"cached in {index_path(args.file)}")


def cmd_config(args):
    cfg = load_config(args.file)
    if args.track:
        cfg["track"] = args.track == "on"
        print("tracked changes "
              + ("ON — edits appear in Pages' review pane for you to accept "
                 "or reject" if cfg["track"] else "OFF — edits replace text outright"))
    if args.vcs:
        cfg["vcs"] = args.vcs == "on"
        if cfg["vcs"]:
            root = vcs_init(args.file)
            h = vcs_snapshot(args.file, "Baseline")
            print(f"internal source control ON — history in {root}"
                  f"\nbaseline commit: {h or '(already current)'}")
            print("add '.pages-vcs/' to .gitignore to keep it out of the project repo")
        else:
            print("internal source control OFF (existing history is kept)")
    if args.track or args.vcs:
        save_config(args.file, cfg)
    print(json.dumps(cfg, indent=2))


def cmd_history(args):
    root = vcs_root(args.file)
    if not os.path.isdir(os.path.join(root, ".git")):
        sys.exit("no history yet — enable it with: config --vcs on")
    print(git(root, "log", "--format=%h  %ad  %s", "--date=short").stdout.rstrip())


def cmd_revert(args):
    root = vcs_root(args.file)
    name = os.path.basename(args.file)
    if not os.path.isdir(os.path.join(root, ".git")):
        sys.exit("no history yet — enable it with: config --vcs on")
    if pages_has_open(args.file):
        sys.exit(f"Pages has {os.path.basename(args.file)} open — it would "
                 "overwrite the restored version on its next save. Close it "
                 "first.")
    vcs_snapshot(args.file, "Before revert to " + args.ref)
    blob = subprocess.run(["git", "-C", root, "show", f"{args.ref}:{name}"],
                          capture_output=True)
    if blob.returncode:
        sys.exit(f"cannot read {name} at {args.ref}: "
                 f"{blob.stderr.decode(errors='replace').strip()}")
    tmp = args.file + ".tmp"            # never leave a half-written document
    try:
        with open(tmp, "wb") as fh:
            fh.write(blob.stdout)
        os.replace(tmp, args.file)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    print(f"restored {name} from {args.ref} ({len(blob.stdout):,} bytes)")
    vcs_snapshot(args.file, "Revert to " + args.ref)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pages_edit", description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("file", help=".pages document (the real file, not an alias)")

    f = sub.add_parser("find", help="locate text and show character offsets")
    f.add_argument("pattern")
    common(f)
    f.add_argument("--raw", action="store_true",
                   help="search the raw text, including tracked-change leftovers")
    f.add_argument("--in", dest="in_section", metavar="HEADING",
                   help="only inside the section with this heading "
                        "(see `outline`)")
    f.add_argument("--page", metavar="N[-M]",
                   help="only on this page or page range (needs `index`)")
    f.add_argument("--where", default="all",
                   help="which storages to search: all (default), body, "
                        "notes, or a single note handle")
    f.add_argument("--regex", action="store_true")
    f.set_defaults(func=cmd_find)

    r = sub.add_parser("replace", help="replace text (dry run unless --write)")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("-f", "--find")
    g.add_argument("--find-file", help="read the search text from a file")
    g2 = r.add_mutually_exclusive_group(required=True)
    g2.add_argument("-r", "--replace")
    g2.add_argument("--replace-file", help="read the replacement from a file")
    common(r)
    r.add_argument("--all", action="store_true", help="every occurrence")
    r.add_argument("--occurrence", type=int, metavar="N",
                   help="only the Nth occurrence (default: 1)")
    r.add_argument("--in", dest="in_section", metavar="HEADING",
                   help="only inside the section with this heading "
                        "(see `outline`)")
    r.add_argument("--page", metavar="N[-M]",
                   help="only on this page or page range (needs `index`)")
    r.add_argument("--where", default="all",
                   help="which storages to search: all (default), body, "
                        "notes, or a single note handle")
    r.add_argument("--raw", action="store_true")
    r.add_argument("--regex", action="store_true")
    r.add_argument("--track", dest="track", action="store_true", default=None,
                   help="record the edit as a native Pages tracked change "
                        "instead of replacing the text outright")
    r.add_argument("--no-track", dest="track", action="store_false",
                   help="replace outright, overriding the stored setting")
    r.add_argument("--expect", metavar="FINGERPRINT",
                   help="refuse to write unless the document still has this "
                        "fingerprint")
    r.add_argument("--write", action="store_true", help="actually modify the file")
    r.add_argument("--no-backup", action="store_true",
                   help="skip the .bak copy (ignored when history is on)")
    r.set_defaults(func=cmd_replace)

    c = sub.add_parser("config", help="show or change settings")
    c.add_argument("--vcs", choices=["on", "off"],
                   help="keep every version in internal source control")
    c.add_argument("--track", choices=["on", "off"],
                   help="record edits as native Pages tracked changes by default")
    common(c)
    c.set_defaults(func=cmd_config)

    h = sub.add_parser("history", help="list stored versions")
    common(h)
    h.set_defaults(func=cmd_history)

    v = sub.add_parser("revert", help="restore a stored version")
    v.add_argument("ref", help="a commit from `history`, e.g. HEAD~1")
    common(v)
    v.set_defaults(func=cmd_revert)

    pl = sub.add_parser("plan", help="apply a JSON file of edits in one pass")
    pl.add_argument("plan", help="JSON plan: a list of edits, or {fingerprint, edits}")
    common(pl)
    pl.add_argument("--track", dest="track", action="store_true", default=None,
                    help="record the edits as native Pages tracked changes")
    pl.add_argument("--no-track", dest="track", action="store_false",
                    help="replace outright, overriding the stored setting")
    pl.add_argument("--write", action="store_true", help="actually modify the file")
    pl.add_argument("--no-backup", action="store_true")
    pl.set_defaults(func=cmd_plan)

    fp = sub.add_parser("fingerprint", help="print the document's text fingerprint")
    common(fp)
    fp.set_defaults(func=cmd_fingerprint)

    cm = sub.add_parser("comment", help="add, reply to, or delete a comment")
    cs = cm.add_subparsers(dest="action", required=True)

    def comment_common(sp, need_target=True):
        sp.add_argument("file")
        sp.add_argument("--text", required=True, help="the comment text")
        if need_target:
            g = sp.add_mutually_exclusive_group(required=True)
            g.add_argument("--on", help="quoted text the comment attaches to")
            g.add_argument("--at", type=int, help="anchor offset (see --comments)")
        sp.add_argument("--where", default="all")
        sp.add_argument("--expect", metavar="FINGERPRINT")
        sp.add_argument("--write", action="store_true")
        sp.add_argument("--no-backup", action="store_true")

    ca = cs.add_parser("add", help="attach a new comment to some text")
    ca.add_argument("file")
    ca.add_argument("--on", required=True, help="the text to attach it to")
    ca.add_argument("--text", required=True)
    ca.add_argument("--where", default="all",
                    help="storages to search: all (default), body, notes, "
                         "or a single handle")
    ca.add_argument("--in", dest="in_section", metavar="HEADING")
    ca.add_argument("--page", metavar="N[-M]")
    ca.add_argument("--expect", metavar="FINGERPRINT")
    ca.add_argument("--write", action="store_true")
    ca.add_argument("--no-backup", action="store_true")
    ca.set_defaults(func=cmd_comment_add)

    cr = cs.add_parser("reply", help="reply in an existing thread")
    comment_common(cr)
    cr.set_defaults(func=cmd_comment_reply)

    cd = cs.add_parser("delete", help="remove a comment thread")
    cd.add_argument("file")
    g = cd.add_mutually_exclusive_group(required=True)
    g.add_argument("--on")
    g.add_argument("--at", type=int)
    cd.add_argument("--where", default="all")
    cd.add_argument("--expect", metavar="FINGERPRINT")
    cd.add_argument("--write", action="store_true")
    cd.add_argument("--no-backup", action="store_true")
    cd.set_defaults(func=cmd_comment_delete, text=None)

    def struct_common(sp):
        sp.add_argument("file")
        sp.add_argument("--in", dest="in_section", metavar="HEADING")
        sp.add_argument("--page", metavar="N[-M]")
        sp.add_argument("--where", default="all")
        sp.add_argument("--expect", metavar="FINGERPRINT")
        sp.add_argument("--write", action="store_true")
        sp.add_argument("--no-backup", action="store_true")

    rt = sub.add_parser("retag", help="change a paragraph's style")
    rt.add_argument("--on", required=True, help="text identifying the paragraph")
    rt.add_argument("--style", required=True, help='e.g. "Heading 2", "Body 1"')
    rt.add_argument("--list", help='list style, e.g. "None" to clear a bullet')
    struct_common(rt)
    rt.set_defaults(func=cmd_retag)

    ins = sub.add_parser("insert", help="insert a new paragraph")
    g3 = ins.add_mutually_exclusive_group(required=True)
    g3.add_argument("--after", help="text identifying the paragraph to follow")
    g3.add_argument("--before", help="text identifying the paragraph to precede")
    ins.add_argument("--text", required=True, help="the new paragraph")
    ins.add_argument("--style", help="style for the new paragraph")
    struct_common(ins)
    ins.set_defaults(func=cmd_insert)

    dp = sub.add_parser("delete-paragraph", help="remove a whole paragraph")
    dp.add_argument("--on", required=True, help="text identifying the paragraph")
    struct_common(dp)
    dp.set_defaults(func=cmd_delete_para)

    fm = sub.add_parser("format", help="apply bold/italic to existing text")
    fm.add_argument("--on", required=True, help="the text to format")
    fm.add_argument("--bold", action="store_true")
    fm.add_argument("--italic", action="store_true")
    fm.add_argument("--plain", action="store_true", help="remove emphasis")
    struct_common(fm)
    fm.set_defaults(func=cmd_format)

    im = sub.add_parser("import", help="insert Markdown as styled paragraphs")
    im.add_argument("markdown", help="a .md file: headings, paragraphs, bullets")
    g4 = im.add_mutually_exclusive_group(required=True)
    g4.add_argument("--after", help="text identifying the paragraph to follow")
    g4.add_argument("--before", help="text identifying the paragraph to precede")
    g4.add_argument("--replace-section", metavar="HEADING",
                    help="replace this whole section, heading included")
    struct_common(im)
    im.set_defaults(func=cmd_import)

    o = sub.add_parser("outline", help="list the headings with offsets and pages")
    common(o)
    o.add_argument("--max-level", type=int, default=3, metavar="N",
                   help="deepest heading level to show (default 3)")
    o.set_defaults(func=cmd_outline)

    ix = sub.add_parser("index", help="build the page index (asks Pages to lay "
                                     "the document out)")
    common(ix)
    ix.set_defaults(func=cmd_index)

    args = ap.parse_args(argv)
    try:
        zipfile.ZipFile(args.file).close()
    except zipfile.BadZipFile:
        ap.error(f"{args.file} is not a Pages package "
                 "(an iCloud placeholder/alias? use the real local file)")
    except FileNotFoundError:
        ap.error(f"{args.file}: no such file")
    args.func(args)


if __name__ == "__main__":
    main()
