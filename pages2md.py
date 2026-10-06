#!/usr/bin/env python3
"""pages2md — convert Apple Pages (.pages) documents to Markdown.

A pandoc-style CLI for iWork documents. No dependencies: it reads the IWA
container (Apple's Snappy framing + protobuf) directly.

    pages2md doc.pages                  # Markdown to stdout
    pages2md -t plain -o out.txt doc.pages
    pages2md --list-styles doc.pages    # paragraph-style inventory
    pages2md -t json doc.pages          # structured paragraphs + runs

Formats: markdown (default), plain, json, styles, archives
"""
import argparse, bisect, contextlib, datetime, hashlib, json, os, re, struct, subprocess, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collections import Counter

# ---------------------------------------------------------------- IWA layers
# The container itself lives in iwa_codec, which the editor also builds on.
# Keeping a second copy here meant the read paths and the write paths were
# exercising different implementations of the same delicate Snappy decoder.
from iwa_codec import read_varint, tokenize, iwa_decode
from iwa_codec import archives as _framed_archives


def parse_fields(buf):
    """{field_no: [value, ...]} -- varints decoded, everything else raw bytes."""
    out = {}
    for num, wire, val in tokenize(buf):
        out.setdefault(num, []).append(
            read_varint(val, 0)[0] if wire == 0 else val)
    return out


def storage_text(f):
    """The text of one storage (fields as parse_fields returns them), as a str."""
    return b"".join(f.get(F_TEXT, [])).decode("utf-8", "replace")


def archives(payload):
    """Yield (archive_id, msg_type, msg_bytes), flattened for reading."""
    for info, msgs in _framed_archives(payload):
        ident = parse_fields(info).get(1, [0])[0]
        for mtype, msg in msgs:
            yield ident, mtype, msg


# ------------------------------------------------------------ document model
T_STORAGE, T_PARA_STYLE, T_CHAR_STYLE = 2001, 2022, 2021
T_TOC = 2240                  # owns the generated table of contents
T_LIST_STYLE = 2023
F_TEXT, F_PARA_TBL, F_LIST_TBL, F_CHAR_TBL = 3, 5, 7, 8
F_INSERTIONS, F_DELETIONS = 21, 22
F_SMARTFIELD = 11             # char index -> smart field (hyperlink, ...), run-length
T_HYPERLINK = 2032            # a hyperlink smart field; field 2 is the URL
F_PARA_STARTS = 14            # list restarts: entry {index, first, second}
F_ATTACHMENTS = 16            # char index -> attachment archive (type 2008)
T_ATTACHMENT = 2008
F_ATTACHED_STORAGE = 2        # the attachment's reference to its storage
F_COMMENTS = 25               # body: {start, length} -> comment-reference
F_COMMENTS_RUN = 23           # text boxes: a run-length index table instead
T_COMMENT_REF, T_COMMENT, T_AUTHOR = 2013, 3056, 212
C_TEXT, C_DATE, C_AUTHOR, C_NEXT = 1, 2, 3, 4   # inside a comment archive
APPLE_EPOCH = 978307200       # 2001-01-01, Core Foundation absolute time
F_NAME, F_IDENT, F_PARENT = 1, 2, 3          # inside the nested TSS.StyleArchive
F_PROPS = 11                                  # style properties
# Character-style properties. 11 and 12 are the ones Pages' own "Underline" and
# "Strikethrough" styles set (checked against its built-in styles and a
# rendered preview); 10 is only ever set on a footnote reference.
P_BOLD, P_ITALIC, P_FONT, P_UNDERLINE, P_STRIKE = 1, 2, 5, 11, 12
F_LEVELS = 6                  # list level, run-length: entry {index, level, 0}
F_LIST_LABEL = 11             # list style: label type of level 1 (0 none, 1 image, 2 bullet, 3 numbered)

CONTROL = {"\x04": "", "\x05": "", "\x0e": "", "￼": ""}
LINE_SEP = " "
INDEX_NAME = ".pages-index.json"


def _ref(buf):
    return read_varint(buf, 1)[0]


# ------------------------------------------------------------ UTF-16 offsets
# Pages counts characters the way NSString does: in UTF-16 code units. Every
# attribute table -- paragraph and character styles, comments, tracked
# changes, attachments -- indexes the text that way, so an emoji (one Python
# character, two UTF-16 units) shifts every later index by one. Verified
# against tests/samples/emoji.pages (Pages 15.4).
#
# Both tools therefore work on a *UTF-16 view* of the text: a str in which
# each astral character is spelled as its surrogate pair. len() and slicing
# of the view count exactly what the tables count, so offset arithmetic
# needs no conversion anywhere. Text is converted back with from_u16 only to
# show it or to write it.
_ASTRAL = re.compile("[\U00010000-\U0010FFFF]")
_SURROGATE = re.compile("[\ud800-\udfff]")


def _pair(m):
    n = ord(m.group()) - 0x10000
    return chr(0xD800 + (n >> 10)) + chr(0xDC00 + (n & 0x3FF))


def u16(s):
    """The UTF-16 view of `s` (idempotent: a view maps to itself)."""
    return _ASTRAL.sub(_pair, s)


def from_u16(s, errors="strict"):
    """Real text from a UTF-16 view; errors="replace" for display."""
    if not _SURROGATE.search(s):
        return s
    return s.encode("utf-16-le", "surrogatepass").decode("utf-16-le", errors)


def show(s):
    """A view slice made printable -- a slice may cut an emoji in half."""
    return from_u16(s, "replace")


def u16_index_map(view):
    """[index in from_u16(view, "replace") for each view index, plus the end]."""
    out, idx = [], 0
    for k, c in enumerate(view):
        out.append(idx)
        if not ("\ud800" <= c <= "\udbff"
                and "\udc00" <= view[k + 1:k + 2] <= "\udfff"):
            idx += 1
    out.append(idx)
    return out


@contextlib.contextmanager
def package_errors(path):
    """Say what is wrong with a file that is not a readable Pages document.

    Used around opening and decoding the package, so a missing file, a folder, a zip that
    is not Pages and damaged IWA data end in one line, not a traceback.
    """
    name = os.path.basename(path) or path
    try:
        yield
    except FileNotFoundError:
        sys.exit(f"{name}: no such file")
    except IsADirectoryError:
        sys.exit(f"{name} is a folder; give the .pages file (a package saved as a folder "
                 "is not supported yet)")
    except zipfile.BadZipFile:
        sys.exit(f"{name} is not a Pages package (an iCloud placeholder or alias? "
                 "use the real local file)")
    except (IndexError, ValueError, KeyError, struct.error, EOFError) as exc:
        sys.exit(f"{name}: damaged or unsupported Pages data "
                 f"({type(exc).__name__}: {exc})")


class PagesDoc:
    def __init__(self, path):
        self.path = path
        self.arcs = {}
        with package_errors(path), zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.endswith(".iwa")]
            if "Index/Document.iwa" not in names:
                sys.exit(f"{os.path.basename(path)}: no Index/Document.iwa; "
                         "not a Pages document")
            for n in names:
                for i, t, m in archives(iwa_decode(z.read(n))):
                    self.arcs[i] = (t, m, n)
        self._style = {}
        self._fmt = {}
        self._kind = {}

    # -- styles ------------------------------------------------------------
    def style(self, ref, depth=0):
        """(display name, semantic name) following the parent chain."""
        if ref in self._style:
            return self._style[ref]
        if ref is None or depth > 8:
            return (None, None)
        m = self.arcs.get(ref, (None, None, None))[1]
        res = (None, None)
        if m:
            base = parse_fields(m).get(1, [None])[0]
            if base:
                f = parse_fields(base)
                dec = lambda k: f[k][0].decode("utf-8", "replace") if k in f else None
                res = (dec(F_NAME), dec(F_IDENT))
                if res == (None, None) and F_PARENT in f:   # anonymous override
                    res = self.style(_ref(f[F_PARENT][0]), depth + 1)
        self._style[ref] = res
        return res

    def char_format(self, ref):
        """(bold, italic, underline, strikethrough) for a character style."""
        if ref in self._fmt:
            return self._fmt[ref]
        res = (False, False, False, False)
        m = self.arcs.get(ref, (None, None, None))[1]
        if m:
            props = parse_fields(m).get(F_PROPS, [None])[0]
            if props:
                p = parse_fields(props)
                flag = lambda k: p.get(k, [0])[0] == 1
                font = p[P_FONT][0].decode("utf-8", "replace") if P_FONT in p else ""
                res = (flag(P_BOLD) or "bold" in font.lower(),
                       flag(P_ITALIC) or "italic" in font.lower() or "oblique" in font.lower(),
                       flag(P_UNDERLINE), flag(P_STRIKE))
        self._fmt[ref] = res
        return res

    # -- text --------------------------------------------------------------
    def storage(self, ident):
        """Fields of one text storage, by archive id."""
        return parse_fields(self.arcs[ident][1])

    def sidenotes(self):
        """[(anchor offset in the body, storage id)] in reading order.

        Margin notes live in their own storage, anchored into the body by an
        attachment table -- which is why a body-only reader silently skips
        them.
        """
        out = []
        for idx, ref in self._table(self._body(), F_ATTACHMENTS):
            info = self.arcs.get(ref)
            if not info or info[0] != T_ATTACHMENT:
                continue
            sub = parse_fields(info[1]).get(F_ATTACHED_STORAGE, [None])[0]
            if not sub:
                continue
            sid = read_varint(sub, 1)[0]
            if self.arcs.get(sid, (None,))[0] == T_STORAGE:
                out.append((idx, sid))
        return sorted(out)

    def _author(self, ident):
        a = self.arcs.get(ident)
        if not a:
            return None
        name = parse_fields(a[1]).get(1, [b""])[0]
        return name.decode("utf-8", "replace") or None

    def _thread(self, ident):
        """One comment and its replies, following the `next` chain."""
        out, seen = [], set()
        while ident and ident not in seen and self.arcs.get(ident, (None,))[0] == T_COMMENT:
            seen.add(ident)                # a damaged chain may loop back on itself
            c = parse_fields(self.arcs[ident][1])
            when = None
            if C_DATE in c:
                stamp = parse_fields(c[C_DATE][0]).get(1, [None])[0]
                if stamp and len(stamp) == 8:
                    # seconds since 2001-01-01 UTC; shown in local time, which
                    # is what Pages displays
                    when = datetime.datetime.fromtimestamp(
                        struct.unpack("<d", stamp)[0] + APPLE_EPOCH)
            out.append({
                "author": self._author(read_varint(c[C_AUTHOR][0], 1)[0])
                          if C_AUTHOR in c else None,
                "date": when,
                "text": c.get(C_TEXT, [b""])[0].decode("utf-8", "replace"),
            })
            ident = read_varint(c[C_NEXT][0], 1)[0] if C_NEXT in c else None
        return out

    def _comment_runs(self, f, handle, anchor, raw):
        rows = []
        for sub in parse_fields(f[F_COMMENTS_RUN][0]).get(1, []):
            e = parse_fields(sub)
            rows.append((e.get(1, [0])[0],
                         read_varint(e[2][0], 1)[0] if 2 in e else None))
        rows.sort()
        out = []
        for k, (idx, ref) in enumerate(rows):
            if ref is None:
                continue
            end = rows[k + 1][0] if k + 1 < len(rows) else len(raw)
            info = self.arcs.get(ref)
            if not info or info[0] != T_COMMENT_REF:
                continue
            head = parse_fields(info[1]).get(1, [None])[0]
            if not head:
                continue
            thread = self._thread(read_varint(head, 1)[0])
            if thread:
                out.append({"handle": handle, "anchor": anchor,
                            "start": idx, "end": end,
                            "quote": _clean(show(raw[idx:end]), False),
                            "thread": thread})
        return out

    def comments(self):
        """Review comments: [{handle, start, end, quote, thread}].

        Anchored to a *range* of text rather than a single index, and kept in
        each storage's own table -- not in AnnotationAuthorStorage.iwa, which
        holds only the list of authors. Margin notes can carry them too.
        """
        out = []
        for handle, sid, anchor, _kind in self.storages():
            if sid is None:
                continue
            out += self._comments_in(self.storage(sid), handle, anchor)
        return sorted(out, key=lambda c: (c["anchor"] if c["anchor"] is not None
                                          else c["start"], c["start"]))

    def _comments_in(self, f, handle, anchor):
        """Comments in one storage.

        The body keys them by {start, length} in field 25; a text box keys
        them by character index in field 23, run-length, closed by the next
        entry. Same comment archives either way.
        """
        raw = u16(storage_text(f))
        if F_COMMENTS_RUN in f:
            return self._comment_runs(f, handle, anchor, raw)
        if F_COMMENTS not in f:
            return []
        out = []
        for sub in parse_fields(f[F_COMMENTS][0]).get(1, []):
            e = parse_fields(sub)
            if 2 not in e:
                continue
            span = parse_fields(e[1][0])
            start = span.get(1, [0])[0]
            end = start + span.get(2, [0])[0]
            ref = read_varint(e[2][0], 1)[0]
            info = self.arcs.get(ref)
            if not info or info[0] != T_COMMENT_REF:
                continue
            head = parse_fields(info[1]).get(1, [None])[0]
            if not head:
                continue
            thread = self._thread(read_varint(head, 1)[0])
            if thread:
                out.append({"handle": handle, "anchor": anchor,
                            "start": start, "end": end,
                            "quote": _clean(show(raw[start:end]), False),
                            "thread": thread})
        return out

    def _toc_storages(self, body_id):
        """Storage ids owned by a generated table of contents.

        Pages rebuilds these, so they are worth showing and never worth
        editing. The reference sits several messages deep inside the TOC
        archive, so this walks rather than peeks.
        """
        found = set()

        def walk(blob, depth):
            if depth > 6 or not isinstance(blob, bytes):
                return
            try:
                sub = parse_fields(blob)
            except Exception:
                return
            for vals in sub.values():
                for v in vals:
                    if isinstance(v, int):
                        if self.arcs.get(v, (None,))[0] == T_STORAGE:
                            found.add(v)
                    else:
                        walk(v, depth + 1)

        for _ident, (mtype, msg, _src) in self.arcs.items():
            if mtype == T_TOC:
                walk(msg, 0)
        # a TOC also points back at the text it was built from
        return found - {body_id}

    def _body_id(self):
        text = self._raw_text()
        return next((i for i, (t, msg, _s) in self.arcs.items()
                     if t == T_STORAGE
                     and storage_text(parse_fields(msg)) == text), None)

    def storages(self):
        """[(handle, storage id, anchor or None, kind)] -- every text storage.

        The body and its margin notes sit in the flow; anything else (figure
        captions, the generated TOC) has no anchor, so it gets a positionless
        handle rather than being left invisible.
        """
        body_id = self._body_id()
        notes = self.sidenotes()
        seen = {body_id} | {sid for _a, sid in notes}
        toc = self._toc_storages(body_id)
        out = [("body", body_id, None, "body")]
        for n, (anchor, sid) in enumerate(notes, 1):
            out.append((f"note{n}", sid, anchor, "note"))
        rest = []
        for ident, (mtype, msg, _src) in self.arcs.items():
            if mtype != T_STORAGE or ident in seen:
                continue
            txt = storage_text(parse_fields(msg))
            if _clean(txt, False):
                rest.append((ident, "toc" if ident in toc else "other"))
        counts = {}
        for ident, kind in sorted(rest):
            counts[kind] = counts.get(kind, 0) + 1
            out.append((f"{kind}{counts[kind]}", ident, None, kind))
        return out

    def _body(self):
        if getattr(self, "_body_cache", None) is not None:
            return self._body_cache
        best, size = None, -1
        for t, m, _ in self.arcs.values():
            if t != T_STORAGE:
                continue
            n = sum(len(b) for b in parse_fields(m).get(F_TEXT, []))
            if n > size:
                best, size = m, n
        if best is None:
            raise SystemExit(f"{self.path}: no text storage found")
        self._body_cache = parse_fields(best)
        return self._body_cache

    @staticmethod
    def _table(f, field):
        """ObjectAttributeTable -> sorted [(char_index, ref|None)]."""
        if field not in f:
            return []
        # sorted by index alone, and stable: comparing (index, ref) tuples crashed on two
        # entries at one index (a ref and None); the later of them stays later and wins
        return sorted(((i, _ref(ef[2][0]) if 2 in ef else None)
                       for i, ef in PagesDoc._entries(f, field)),
                      key=lambda row: row[0])

    @staticmethod
    def _entries(f, field):
        """[(char index, {field: [values]})] of an index-keyed table, unsorted."""
        return [(ef.get(1, [0])[0], ef)
                for ef in map(parse_fields, parse_fields(f[field][0]).get(1, []))
                ] if field in f else []

    def list_kind(self, ref):
        """"bullet", "numbered" or None for a list-style archive.

        Read from the style's label type for its first level (0 none, 1 image,
        2 bullet text, 3 numbered), not from its name: "Lettered", "Harvard"
        and "Numbered" are all numbered, and names are what a user renames.
        """
        if ref not in self._kind:
            m = self.arcs.get(ref, (None, None))[1] if ref is not None else None
            code = parse_fields(m).get(F_LIST_LABEL, [0])[0] if m else 0
            self._kind[ref] = {0: None, 3: "numbered"}.get(code, "bullet")
        return self._kind[ref]

    def _levels(self, f):
        """[(offset, level)] from the list-level table (sorted, run-length).

        Each entry holds until the next: in Pages' own sample, "This" is level
        0, "Is" and "A bulleted" are level 1 (one entry covers both) and
        "list" is level 2.
        """
        return sorted((i, ef.get(2, [0])[0]) for i, ef in self._entries(f, F_LEVELS))

    def _list_starts(self, f):
        """Paragraph offsets where a list restarts (para-starts `first` is 1)."""
        return {i for i, ef in self._entries(f, F_PARA_STARTS)
                if ef.get(2, [0])[0] == 1}

    def _links(self, f, text):
        """[(start, end, url)] for the hyperlinks of one storage.

        The smart-field table is run-length like the change tables: an entry
        runs to the next one, and a null entry ends it. Other kinds of smart
        field (dates, page numbers) share the table and are ignored.
        """
        tbl = self._table(f, F_SMARTFIELD)
        out = []
        for k, (idx, ref) in enumerate(tbl):
            info = self.arcs.get(ref) if ref is not None else None
            if not info or info[0] != T_HYPERLINK:
                continue
            url = parse_fields(info[1]).get(2, [b""])[0].decode("utf-8", "replace")
            end = tbl[k + 1][0] if k + 1 < len(tbl) else len(text)
            if url and end > idx:
                out.append((idx, end, url))
        return out

    def inline_marks(self, f, text):
        """Offsets of footnote references in one storage (see footnote_marks)."""
        return footnote_marks(self._table(f, F_ATTACHMENTS), text)
    def _styles(self, f, field):
        """Style table, keeping only entries that actually set a style."""
        return [(i, r) for i, r in self._table(f, field) if r is not None]

    def _ranges(self, f, field):
        """Change tables mark a span from a ref'd index to the next index.

        Unlike the style tables, a null entry here genuinely terminates the
        span -- "no change object at this index" means "not part of a change".
        Verified against both edits in the Briefing paragraph.
        """
        tbl = self._table(f, field)
        out = []
        for k, (idx, ref) in enumerate(tbl):
            if ref is None:
                continue
            end = tbl[k + 1][0] if k + 1 < len(tbl) else idx
            if end > idx:
                out.append((idx, end))
        return out

    def _raw_text(self):
        return storage_text(self._body())

    def _dropped(self, f, changes):
        """Character indices a tracked-change mode leaves out of the text."""
        ranges = (self._ranges(f, F_DELETIONS) if changes == "accept" else
                  self._ranges(f, F_INSERTIONS) if changes == "reject" else [])
        return {i for a, b in ranges for i in range(a, b)}

    def footnotes(self):
        """[(anchor, storage id)]: the notes whose reference is a footnote mark.

        Numbered 1.. in this order. Other attachments to a text storage are
        not footnotes and stay as they were (blockquotes).
        """
        raw = u16(self._raw_text())
        return [(a, sid) for a, sid in self.sidenotes()
                if raw[a:a + 1] == FOOTNOTE_MARK]

    def paragraphs(self, changes="accept", fields=None):
        f = fields if fields is not None else self._body()
        # the UTF-16 view, so positions line up with the tables; each
        # paragraph's raw text and run offsets are converted back below
        text = u16(b"".join(f.get(F_TEXT, [])).decode("utf-8", "replace"))
        # These tables are run-length maps, but a null entry (field 2 absent)
        # does NOT mean the same thing in each -- determined empirically:
        #   paragraph + list styles: null = "no change here", so the current
        #       style persists through it. Dropping the nulls makes a
        #       nearest-preceding lookup do the right thing. (Proof: Pages'
        #       generated TOC lists every chapter opener, which is only
        #       possible if they all carry a heading style.)
        #   character styles: null = "no override here", i.e. back to the
        #       paragraph's own formatting, so the nulls must be kept as run
        #       boundaries. (Proof: the bold lead-in of a paragraph is 14
        #       characters; carrying it forward bolds the next 807.)
        para_tbl = self._styles(f, F_PARA_TBL)
        list_tbl = self._styles(f, F_LIST_TBL)
        char_tbl = self._table(f, F_CHAR_TBL)
        l_idx = [i for i, _ in list_tbl]
        # Tracked changes: the storage holds original *and* revised text.
        drop = self._dropped(f, changes)
        marks = self._ranges(f, F_DELETIONS) if changes == "mark" else []
        m_idx = [a for a, _ in marks]
        m_ends = sorted(b for _a, b in marks)
        ref_marks = self.inline_marks(f, text)
        sorted_refs = sorted(ref_marks)
        links_all = self._links(f, text)      # sorted, and never overlapping
        link_ends = [b for _a, b, _u in links_all]
        list_starts = self._list_starts(f)
        levels = self._levels(f)
        lv_idx = [i for i, _ in levels]
        p_idx = [i for i, _ in para_tbl]
        c_idx = [i for i, _ in char_tbl]

        def lookup(tbl, idx, i):
            k = bisect.bisect_right(idx, i) - 1
            return tbl[k][1] if k >= 0 else None

        flow = flow_view(text, ref_marks)
        out, pos = [], 0
        for chunk in PARA_SPLIT.split(flow):
            raw = text[pos:pos + len(chunk)]
            width = len(raw)      # advance by the ORIGINAL width; `raw` may shrink
            name, semantic = self.style(lookup(para_tbl, p_idx, pos))
            lref = lookup(list_tbl, l_idx, pos)
            bullet = self.style(lref)[1]
            lname = (bullet or "").split("liststyle-")[-1]
            kind = self.list_kind(lref) if lname != "None" else None
            runs = []
            k = max(bisect.bisect_right(c_idx, pos) - 1, 0)
            while k < len(char_tbl) and char_tbl[k][0] < pos + len(raw):
                start = max(char_tbl[k][0], pos)
                end = char_tbl[k + 1][0] if k + 1 < len(char_tbl) else len(text)
                b, i_, u, x = (self.char_format(char_tbl[k][1])
                               if char_tbl[k][1] else (False,) * 4)
                if (b or i_ or u or x) and min(end, pos + len(raw)) > start:
                    runs.append((start - pos, min(end, pos + len(raw)) - pos,
                                 b, i_, u, x))
                k += 1
            links = []
            for j in range(bisect.bisect_right(link_ends, pos), len(links_all)):
                a, b, url = links_all[j]
                if a >= pos + len(raw):
                    break
                links.append((max(a, pos) - pos, min(b, pos + len(raw)) - pos, url))
            if drop:
                keep = [k for k in range(len(raw)) if pos + k not in drop]
                remap = {}
                for new, old in enumerate(keep):
                    remap[old] = new
                remap[len(raw)] = len(keep)
                raw = "".join(raw[k] for k in keep)

                def squeeze(spans):
                    """Move (start, end, ...) spans onto the shrunken text."""
                    out = []
                    for s, e, *rest in spans:
                        ns = remap.get(s, bisect.bisect_left(keep, s))
                        ne = remap.get(e, bisect.bisect_left(keep, e))
                        if ne > ns:
                            out.append((ns, ne, *rest))
                    return out

                runs, links = squeeze(runs), squeeze(links)
            struck = []
            if marks:
                # bisect the span *ends* so a long span starting far back is
                # still found; a fixed lookback window could clip it
                lo = bisect.bisect_right(m_ends, pos)
                hi = bisect.bisect_left(m_idx, pos + width)
                for a, b in marks[lo:hi] if lo <= hi else []:
                    if b > pos and a < pos + width:
                        struck.append((max(a, pos) - pos, min(b, pos + width) - pos))
            if _SURROGATE.search(raw):
                # `offset` stays in document (UTF-16) units; offsets *within*
                # the paragraph follow its text back to real characters
                m = u16_index_map(raw)
                runs = [(m[a], m[b], *rest) for a, b, *rest in runs]
                struck = [(m[a], m[b]) for a, b in struck]
                links = [(m[a], m[b], url) for a, b, url in links]
                raw = show(raw)
            refs = [m for m in sorted_refs[bisect.bisect_left(sorted_refs, pos):
                                           bisect.bisect_left(sorted_refs, pos + width)]
                    if m not in drop]
            out.append(dict(offset=pos, raw=raw, style=name, struck=struck,
                            refs=refs, links=links, list_kind=kind,
                            list_level=(lookup(levels, lv_idx, pos) or 0) if kind else 0,
                            list_start=pos in list_starts,
                            list=lname or None,
                            semantic=(semantic or "").split("paragraphstyle-")[-1],
                            runs=runs))
            pos += width + 1
        return out


    def all_paragraphs(self, changes="accept", sidenotes="inline"):
        """Body paragraphs with each margin note placed at its anchor."""
        paras = [dict(q, sidenote=None, footnote=None, ref_nos=[])
                 for q in self.paragraphs(changes)]
        if sidenotes == "skip":
            return paras
        numbers = {a: n for n, (a, _sid) in enumerate(self.footnotes(), 1)}
        gone = self._dropped(self._body(), changes)
        for q in paras:
            q["ref_nos"] = [numbers.get(a) for a in q["refs"]]
        notes = [dict(para, offset=anchor, sidenote=sid,
                      footnote=numbers.get(anchor), ref_nos=[])
                 for anchor, sid in self.sidenotes() if anchor not in gone
                 for para in self.paragraphs(changes, self.storage(sid))]
        if sidenotes == "only":
            return notes
        # a note sorts after the body paragraph it is anchored inside, since
        # that paragraph starts at or before the anchor
        return sorted(paras + notes,
                      key=lambda q: (q["offset"], q["sidenote"] is not None))

# ----------------------------------------------------- outline and pages
def outline(path):
    """[(level, heading text, char offset)] for the body flow, in order."""
    pd = PagesDoc(path)
    out = []
    for para in pd.paragraphs("mark"):     # "mark" keeps every character
        level = HEADING.get(para["semantic"])
        if not level:
            continue
        text = _clean(para["raw"], False)
        if text:
            out.append((level, text, para["offset"]))
    return out


def section_range(path, name, text_len):
    """Character range of the section whose heading matches `name`.

    A section runs from its heading to the next heading at the same or a
    higher level, which is what "in this chapter" means to a reader.
    """
    heads = outline(path)
    needle = name.strip().lower()
    exact = [i for i, (_l, t, _o) in enumerate(heads) if t.lower() == needle]
    hits = exact or [i for i, (_l, t, _o) in enumerate(heads)
                     if needle in t.lower()]
    if not hits:
        sys.exit(f"no heading matches {name!r}; run `outline` to list them")
    if len(hits) > 1:
        names = ", ".join(repr(heads[i][1]) for i in hits[:6])
        sys.exit(f"{name!r} matches {len(hits)} headings ({names}); "
                 "be more specific")
    level, title, start = heads[hits[0]]
    end = text_len
    for level2, _t, off in heads[hits[0] + 1:]:
        if level2 <= level:
            end = off
            break
    return title, start, end


def fingerprint_parts(texts):
    """Canonical fingerprint over a document's storages, in a fixed order.

    Defined here so the reader and the editor cannot drift apart: both build
    the list as body first, then margin notes in anchor order.
    """
    return hashlib.sha256("\x00".join(texts).encode("utf-8")).hexdigest()[:16]


def text_fingerprint(path):
    """Fingerprint over every text storage except the generated TOC.

    Body first, then footnotes and margin notes in anchor order, then
    everything else (captions, text boxes) by id -- the order `storages()`
    lists them in. The editor delegates here, so the two cannot differ;
    before, the reader hashed only the body and its notes.
    """
    doc = path if isinstance(path, PagesDoc) else PagesDoc(path)
    texts = [doc._raw_text()]
    for _handle, sid, _anchor, kind in doc.storages()[1:]:
        if kind != "toc" and sid is not None:
            texts.append(storage_text(doc.storage(sid)))
    return fingerprint_parts(texts)


def style_ids(path):
    """{semantic style name: archive id} for the named paragraph styles.

    Keyed off the English identifier ("Heading 2"), not the localised display
    name, so retagging does not depend on the document's language.
    """
    doc = path if isinstance(path, PagesDoc) else PagesDoc(path)
    out = {}
    for ident, (mtype, msg, _src) in doc.arcs.items():
        if mtype != T_PARA_STYLE:
            continue
        name, semantic = doc.style(ident)
        if not semantic:
            continue
        if "paragraphstyle-" not in semantic:
            continue          # character styles and the like share the prefix
        key = semantic.split("paragraphstyle-")[-1]
        if not any(c.isalpha() for c in key):
            continue          # numbered anonymous variants, not nameable
        out.setdefault(key, (ident, name))
    return out


def list_style_ids(path):
    """{semantic list style: archive id}, e.g. Bullet, Dash, None."""
    doc = path if isinstance(path, PagesDoc) else PagesDoc(path)
    out = {}
    for ident, (mtype, _msg, _src) in doc.arcs.items():
        if mtype != T_LIST_STYLE:
            continue
        name, semantic = doc.style(ident)
        if semantic and "liststyle-" in semantic:
            out.setdefault(semantic.split("liststyle-")[-1], (ident, name))
    return out


def char_style_ids(path):
    """{(bold, italic): archive id} for plain weight-only character styles.

    Lives here with the other style lookups because the archives are in
    DocumentStylesheet.iwa, which only the reader loads.
    """
    doc = path if isinstance(path, PagesDoc) else PagesDoc(path)
    out = {}
    for ident, (mtype, msg, _src) in doc.arcs.items():
        if mtype != T_CHAR_STYLE:
            continue
        props = parse_fields(msg).get(11, [None])[0]
        if not props:
            continue
        pf = parse_fields(props)
        # anything carrying a font or size would drag that along too
        if set(pf) - {P_BOLD, P_ITALIC}:
            continue
        out.setdefault((pf.get(P_BOLD, [0])[0] == 1,
                        pf.get(P_ITALIC, [0])[0] == 1), ident)
    return out


def index_path(doc):
    return os.path.join(os.path.dirname(os.path.abspath(doc)) or ".", INDEX_NAME)


def load_index(doc, warn=True):
    """Page boundaries, or None. Warns once when the index has gone stale.

    The index is a snapshot of a layout, so any edit that reflows text makes
    its page numbers wrong -- silently, which is the worst way to be wrong.
    """
    try:
        with open(index_path(doc), encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        print("warning: page index is unreadable; rebuild it with "
              "`pages_edit.py index`", file=sys.stderr)
        return None
    # one folder, several documents, one index file -- make sure it is ours
    owner = data.get("document")
    if owner and isinstance(doc, str) and owner != os.path.basename(
            os.path.abspath(doc)):
        if not getattr(load_index, "_warned", False):
            load_index._warned = True
            print(f"warning: the page index in this folder was built for "
                  f"{owner!r}, not this document; page numbers omitted",
                  file=sys.stderr)
        return None
    stored = data.get("fingerprint")
    if warn and not getattr(load_index, "_warned", False):
        actual = text_fingerprint(doc) if isinstance(doc, str) else None
        if stored is None:
            load_index._warned = True
            print("warning: page index predates fingerprinting; rebuild it "
                  "with `pages_edit.py index` if page numbers look wrong",
                  file=sys.stderr)
        elif actual and stored != actual:
            load_index._warned = True
            print(f"warning: page index is stale (built for {stored}, document "
                  f"is {actual}); page numbers may be wrong -- rebuild with "
                  "`pages_edit.py index`", file=sys.stderr)
    return data.get("bounds")


GUARD = """
tell application "System Events"
  if not (exists process "Pages") then return "NOTRUNNING"
end tell
tell application "Pages"
  set acc to {}
  repeat with d in documents
    try
      set end of acc to POSIX path of (file of d as alias)
    on error
      set end of acc to "unsaved:" & (name of d)
    end try
  end repeat
  set AppleScript's text item delimiters to "|"
  return (acc as text)
end tell"""


def pages_has_open(doc):
    """True if Pages already has this document open.

    Opening it again and closing it would discard the user's unsaved work, so
    `index` refuses rather than risk it. Compares real paths -- a document's
    `name` drops the extension, which made an earlier basename check useless.
    System Events is asked first so we never launch Pages just to find out.
    """
    try:
        res = subprocess.run(["osascript", "-e", GUARD],
                             capture_output=True, text=True)
    except FileNotFoundError:
        return False                  # no osascript, so no Pages to conflict
    if res.returncode:
        return False
    out = res.stdout.strip()
    if out == "NOTRUNNING" or not out:
        return False
    want = os.path.realpath(doc)
    return any(os.path.realpath(n.strip()) == want
               for n in out.split("|") if n.strip() and not
               n.strip().startswith("unsaved:"))


PAGE_SCRIPT = """
with timeout of 600 seconds
tell application "Pages"
  set d to open POSIX file "%s"
  set acc to {}
  repeat with i from 1 to (count of pages of d)
    set end of acc to (body text of page i of d)
  end repeat
  close d saving no
  set AppleScript's text item delimiters to "<<<PG>>>"
  return (acc as text)
end tell
end timeout"""


def applescript_quote(text):
    """Escape a string for use inside an AppleScript "..." literal."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def index_script(doc):
    """The layout script for one document, with its path safely quoted."""
    return PAGE_SCRIPT % applescript_quote(os.path.abspath(doc))


def significant(c):
    """Characters Pages and we agree on; everything else is layout noise."""
    return not (c.isspace() or ord(c) < 32 or c in " ￼")


def build_index(doc, raw):
    """Ask Pages to lay the document out, then align its pages to our text.

    Pagination is not stored in the file -- Pages computes it at open time --
    so this is the only way to know what sits on page N. The alignment walks
    both texts comparing only significant characters, which absorbs the
    whitespace and control characters Pages normalises away.
    """
    if pages_has_open(doc):
        sys.exit(f"Pages already has {os.path.basename(doc)} open. Indexing "
                 "would reopen and close it, discarding unsaved changes -- "
                 "close it in Pages first, or index a copy.")
    res = subprocess.run(["osascript", "-e", index_script(doc)],
                         capture_output=True, text=True)
    if res.returncode:
        sys.exit("could not ask Pages for the page layout (is Pages "
                 f"installed?):\n{res.stderr.strip()}")
    pages = res.stdout.split("<<<PG>>>")
    pos, bounds = 0, []
    for text in pages:
        bounds.append(pos)
        for ch in u16(text):            # compare like with like
            if not significant(ch):
                continue
            mark = pos
            while pos < len(raw) and not (significant(raw[pos]) and raw[pos] == ch):
                pos += 1
            if pos >= len(raw):
                pos = mark
                break
            pos += 1
    tmp = index_path(doc) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"document": os.path.basename(os.path.abspath(doc)),
                   "pages": len(pages), "aligned": pos, "chars": len(raw),
                   "fingerprint": text_fingerprint(doc), "bounds": bounds}, fh)
        fh.write("\n")
    os.replace(tmp, index_path(doc))      # never leave a half-written index
    return bounds, len(pages), pos


def page_of(bounds, offset):
    """Page number of a body offset; None without an index or an anchor.

    A caption or text box has no position in the flow, so no page.
    """
    if not bounds or offset is None:
        return None
    return bisect.bisect_right(bounds, offset)


def page_range(doc, spec, text_len):
    bounds = load_index(doc)
    if not bounds:
        sys.exit("no page index yet -- run `index` first (it needs Pages, "
                 "since pagination is not stored in the document)")
    m = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", spec.strip())
    if not m:
        sys.exit(f"--page expects N or N-M, not {spec!r}")
    lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
    if not 1 <= lo <= hi <= len(bounds):
        sys.exit(f"--page {spec} out of range (1..{len(bounds)})")
    return bounds[lo - 1], (bounds[hi] if hi < len(bounds) else text_len)


# ----------------------------------------------------------------- rendering
HEADING = {"Heading 1": 1, "Heading 2": 2, "Heading 3": 3, "Heading 4": 4,
           "Title": 1, "Subtitle": 2}
# \n ends a paragraph; \x04 (page break), \x05 (object anchor) and \x0e
# (section break, unless it is a footnote reference: see flow_view) lead one.
# Splitting on all four keeps paragraph start
# offsets aligned with the sparse style tables. Exported so pages_edit.py
# locates paragraph boundaries by the identical rule -- a second, separately
# maintained definition is exactly how the reader and the editor end up
# disagreeing about where a paragraph starts.
PARA_BREAKS = "\n\x04\x05\x0e"
PARA_SPLIT = re.compile(f"[{re.escape(PARA_BREAKS)}]")
FOOTNOTE_MARK = "\x0e"


def footnote_marks(rows, text):
    """Offsets of footnote references: a \\x0e with an entry in the attachment table.

    `rows` is that table as [(char index, ref or None)]; the reader and the
    editor each decode it their own way and share this rule.
    """
    return {i for i, ref in rows
            if ref is not None and text[i:i + 1] == FOOTNOTE_MARK}


def flow_view(text, marks):
    """`text` with inline references neutralised, for finding paragraph breaks.

    A \x0e that has an entry in the attachment table is a footnote reference
    in the middle of a sentence, not a break -- splitting there cut every
    footnoted paragraph in two and left the second half without its style
    (Pages 14.5 and 15.4 documents). Same length, so every offset still
    lines up; only the break test changes. Reader and editor both split on
    this view, so they cannot disagree about where a paragraph begins.
    """
    if not marks:
        return text
    out, prev = [], 0
    for i in sorted(marks):
        out += [text[prev:i], "\ufffc"]
        prev = i + 1
    return "".join(out) + text[prev:]


def _clean(s, keep_breaks=True):
    for k, v in CONTROL.items():
        s = s.replace(k, v)
    s = s.replace(LINE_SEP, "\n" if keep_breaks else " ")
    s = s.replace(" ", " ").replace(" ", " ")
    return s.strip()


def _merged_marks(raw, runs):
    """[[start, end, marker]] -- one entry per stretch of identical emphasis.

    Runs over invisible characters only (a footnote reference has its own
    style) are dropped first, so they neither leave bare markers behind nor
    drag the reference inside its neighbour's emphasis. Touching runs with
    the same marker merge: "**foo****bar**" becomes "**foobar**".
    """
    out = []
    for start, end, bold, italic, *_rest in sorted(runs):
        mark = "***" if bold and italic else "**" if bold else "*" if italic else ""
        if not mark or not _clean(raw[start:end].strip(), False):
            continue
        if out and out[-1][2] == mark and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end, mark])
    return out


_ENTITY = re.compile(r"&(?:#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")
_LINE_START = ((re.compile(r"#{1,6}(?:\s|$)"), 0),     # heading
               (re.compile(r">"), 0),                  # block quote
               (re.compile(r"[-+](?:\s|$)"), 0),        # bullet
               (re.compile(r"\d{1,9}(?=[.)](?:\s|$))"), None),   # "1." item
               (re.compile(r"(?:=+|-{3,})\s*$"), 0))   # setext / thematic break


def _escape_positions(raw):
    """Offsets in `raw` that need a backslash to stay literal text in Markdown.

    Conservative on purpose: characters that only matter in some places
    (underscores inside words, a lone < or &, a single ~) are left alone, so
    ordinary prose is not littered with backslashes.
    """
    out = set()
    for i, c in enumerate(raw):
        prv, nxt = raw[i - 1:i] if i else "", raw[i + 1:i + 2]
        if c in "\\*`[]":
            out.add(i)
        elif c == "_" and not (prv.isalnum() and nxt.isalnum()):
            out.add(i)
        elif c == "<" and nxt and (nxt.isalpha() or nxt in "/!?"):
            out.add(i)
        elif c == "&" and _ENTITY.match(raw, i):
            out.add(i)
        elif c == "~" and (nxt == "~" or prv == "~"):
            out.add(i)
    # what would turn a line into a heading, quote, list item or rule
    starts = [0] + [k + 1 for k, c in enumerate(raw) if c == LINE_SEP]
    for start in starts:
        j = start
        while j < len(raw) and (raw[j].isspace() or raw[j] in CONTROL):
            j += 1
        end = raw.find(LINE_SEP, j)
        line = raw[j:end if end >= 0 else len(raw)]
        for pattern, where in _LINE_START:
            m = pattern.match(line)
            if m:
                out.add(j + (m.end() if where is None else where))
                break
    return out


def _link_target(url):
    """A link destination, in <...> when it holds spaces or brackets."""
    if any(c in url for c in " ()<>"):
        return "<" + url.replace("<", "%3C").replace(">", "%3E") + ">"
    return url


def _annotate(raw, runs, struck=(), links=(), escape=False):
    """Put Markdown emphasis, links and ~~strikethrough~~ around spans of `raw`.

    All positions refer to the original text, so every marker is collected
    first and inserted right-to-left in one pass -- inserting strike markers
    first used to shift the emphasis runs, which is why a paragraph with a
    marked deletion lost its bold. Where spans meet they nest strikethrough,
    then link, then emphasis: opens run outer to inner, closes inner to outer,
    and a backslash escape goes right before its character. An emphasis run
    that only partly overlaps a link is cut at the link's edge so the
    markers still nest.
    """
    marks = []                          # (pos, rank, depth, text)
    # struck text: tracked deletions and strikethrough formatting alike, so
    # overlapping or touching spans become one ~~...~~
    struck_spans = sorted([*struck, *((r[0], r[1]) for r in runs
                                      if len(r) > 5 and r[5])])
    merged = []
    for a, b in struck_spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in merged:                 # rank: 0 close, 1 open, 2 escape
        if raw[a:b].strip():
            marks += [(a, 1, 0, "~~"), (b, 0, 0, "~~")]
    spans = []
    for a, b, url in links:
        seg = raw[a:b]
        if not _clean(seg.strip(), False):
            continue
        s = a + len(seg) - len(seg.lstrip())
        e = b - (len(seg) - len(seg.rstrip()))
        marks += [(s, 1, 1, "["), (e, 0, 1, "](" + _link_target(url) + ")")]
        spans.append((s, e))
    for start, end, mark in _merged_marks(raw, runs):
        cuts = set()
        for s, e in spans:
            if start <= s and e <= end or s <= start and end <= e:
                continue                # one wholly inside the other: nests
            cuts.update(c for c in (s, e) if start < c < end)
        points = [start, *sorted(cuts), end]
        for a, b in zip(points, points[1:]):
            seg = raw[a:b]
            if not _clean(seg.strip(), False):
                continue
            lead = len(seg) - len(seg.lstrip())
            trail = len(seg) - len(seg.rstrip())
            marks += [(a + lead, 1, 2, mark), (b - trail, 0, 2, mark)]
    if escape:
        marks += [(i, 2, 3, "\\") for i in _escape_positions(raw)]
    marks.sort(key=lambda m: (m[0], m[1], -m[2] if m[1] == 0 else m[2]),
               reverse=True)
    pieces, end = [], len(raw)          # build the result right to left
    for pos, _rank, _depth, text in marks:
        pieces += [raw[pos:end], text]
        end = pos
    pieces.append(raw[:end])
    return "".join(reversed(pieces))


def _place_refs(text, numbers):
    """Turn each footnote mark in `text` into [^n], in order; drop unnumbered."""
    parts = text.split(FOOTNOTE_MARK)
    out = [parts[0]]
    for k, part in enumerate(parts[1:]):
        n = numbers[k] if k < len(numbers) else None
        out.append((f"[^{n}]" if n else "") + part)
    return "".join(out)


def render_markdown(doc, paras):
    """Markdown for a list of paragraph dicts.

    Consecutive list items are a tight list; a child is indented by its
    parent's marker width. Numbered items count up from 1 per level, starting
    over after any other paragraph, where Pages restarts the list, and for
    each new sub-list. Footnote definitions are collected at the end.
    """
    blocks, notes = [], {}              # blocks: [(text, (kind, level) | None)]
    widths, counters, kinds = [], {}, {}    # list state, reset by other paragraphs
    for p in paras:
        raw = p["raw"]
        if HEADING.get(p["semantic"]):
            p = {**p, "runs": [r for r in p["runs"] if len(r) > 5 and r[5]]}
            # a heading's weight is its style, not markup
        body = _clean(_place_refs(
            _annotate(raw, p["runs"], p.get("struck", []), p.get("links", []),
                      escape=True),
            p.get("ref_nos", [])))
        if not body:
            continue
        level = HEADING.get(p["semantic"])
        kind = p["list_kind"]
        if p.get("footnote"):
            # definitions go at the end; a note's later paragraphs are indented
            # (a manual line break inside a note becomes a hard break)
            notes.setdefault(p["footnote"], []).append(
                body.replace("\n", "  \n    "))
            continue
        if p.get("sidenote"):
            blocks.append(("> " + body.replace("\n", "\n> "), None))
            continue
        if kind and not level:
            # a child can sit at most one level below the item before it
            depth = min(p.get("list_level", 0), len(widths))
            del widths[depth:]
            for state in (counters, kinds):
                for k in [k for k in state if k > depth]:
                    del state[k]
            if kind == "numbered":
                again = (kinds.get(depth) == "numbered" and depth in counters
                         and not p.get("list_start"))
                counters[depth] = counters[depth] + 1 if again else 1
                marker = f"{counters[depth]}. "
            else:
                counters.pop(depth, None)
                marker = "- "
            kinds[depth] = kind
            blocks.append(("".join(" " * w for w in widths) + marker
                           + body.replace("\n", " "), (kind, depth)))
            widths.append(len(marker))
            continue
        widths.clear(), counters.clear(), kinds.clear()
        if level:
            blocks.append(("#" * level + " " + body.replace("\n", " "), None))
        else:
            blocks.append((body.replace("\n", "  \n"), None))   # LS -> hard break
    for n in sorted(notes):
        first, *rest = notes[n]
        blocks.append((f"[^{n}]: {first}", None))
        blocks.extend(("    " + more, None) for more in rest)
    out = ""
    for k, (text, item) in enumerate(blocks):
        before = blocks[k - 1][1] if k else None
        # items run on without a blank line, except where a different kind of
        # list begins at the same level
        tight = item and before and (item[1] != before[1] or item[0] == before[0])
        out += ("" if k == 0 else "\n" if tight else "\n\n") + text
    return out + "\n"


def render_plain(doc, paras):
    return "\n\n".join(t for t in (_clean(p["raw"], False) for p in paras) if t) + "\n"


def render_json(doc, paras):
    return json.dumps([{**p, "text": _clean(p["raw"])} for p in paras],
                      indent=2, ensure_ascii=False) + "\n"


def render_styles(doc, paras):
    c = Counter((p["style"], p["semantic"]) for p in paras if _clean(p["raw"]))
    w = max((len(repr(n)) for n, _ in c), default=10)
    out = [f"{'count':>7}  {'style':<{w}}  semantic"]
    for (name, sem), k in c.most_common():
        out.append(f"{k:>7}  {name!r:<{w}}  {sem or '-'}")
    return "\n".join(out) + "\n"


def render_archives(doc, paras):
    c = Counter((t, src) for t, _, src in doc.arcs.values())
    out = [f"{len(doc.arcs)} archives", f"{'count':>7}  {'type':>6}  file"]
    for (t, src), k in c.most_common():
        out.append(f"{k:>7}  {t:>6}  {src}")
    return "\n".join(out) + "\n"


def render_comments(doc, paras):
    """Review comments with the text they are attached to."""
    bounds = load_index(doc.path)
    scope = getattr(doc, "_scope", None)
    def in_scope(c):
        if not scope:
            return True
        at = c["anchor"] if c["anchor"] is not None else c["start"]
        return scope[0] <= at < scope[1]

    rows = [c for c in doc.comments() if in_scope(c)]
    if not rows:
        return "no comments\n"
    out = [f"{len(rows)} comment(s), "
           f"{sum(len(c['thread']) for c in rows)} message(s)\n"]
    for c in rows:
        at = c["anchor"] if c["anchor"] is not None else c["start"]
        page = page_of(bounds, at)
        where = f"p.{page}  " if page else ""
        tag = "" if c["handle"] == "body" else f" [{c['handle']}]"
        quote = c["quote"]
        if len(quote) > 70:
            quote = quote[:67] + "…"
        out.append(f"{where}@{c['start']}{tag}  “{quote}”")
        for m in c["thread"]:
            when = f"{m['date']:%d.%m.%Y %H:%M}" if m["date"] else "?"
            out.append(f"    {m['author'] or '?'} · {when}")
            for line in m["text"].splitlines() or [""]:
                out.append(f"      {line}")
        out.append("")
    return "\n".join(out)


def render_links(doc, paras):
    """Every hyperlink: page (when an index exists), the linked text, the URL."""
    bounds = load_index(doc.path)
    rows = []
    for p in paras:
        for a, b, url in p.get("links", []):
            page = page_of(bounds, p["offset"] + a)
            where = f"p.{page}  " if page else ""
            rows.append(f"{where}“{' '.join(show(p['raw'][a:b]).split())}”  {url}")
    if not rows:
        return "no links\n"
    return f"{len(rows)} link(s)\n\n" + "\n".join(rows) + "\n"


def render_storages(doc, paras):
    """Every text storage in the document, with its handle."""
    out = [f"{'handle':<8} {'id':>10}  {'anchor':>7}  text"]
    for handle, sid, anchor, _kind in doc.storages():
        txt = " ".join(_clean(storage_text(doc.storage(sid)), False).split())
        where = f"@{anchor}" if anchor is not None else "-"
        out.append(f"{handle:<8} {sid:>10}  {where:>7}  "
                   f"{txt[:60]}{'…' if len(txt) > 60 else ''}")
    return "\n".join(out) + "\n"


def render_outline(doc, paras):
    bounds = load_index(doc.path)
    out = []
    for p in paras:
        level = HEADING.get(p["semantic"])
        if not level:
            continue
        title = _clean(p["raw"], False)
        if not title:
            continue
        page = page_of(bounds, p["offset"])
        where = f"p.{page:<4}" if page else ""
        out.append(f"{where}@{p['offset']:<6} {'  ' * (level - 1)}"
                   f"{'#' * level} {title}")
    return "\n".join(out) + "\n"


RENDERERS = {"markdown": render_markdown, "md": render_markdown,
             "outline": render_outline,
             "comments": render_comments,
             "links": render_links, "storages": render_storages,
             "plain": render_plain, "json": render_json,
             "styles": render_styles, "archives": render_archives}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="pages2md",
        description="Convert Apple Pages documents to Markdown (no dependencies).")
    ap.add_argument("file", help=".pages document (the real file, not an iCloud alias)")
    ap.add_argument("-t", "--to", default="markdown", choices=sorted(RENDERERS),
                    help="output format (default: markdown)")
    ap.add_argument("-o", "--output", help="write here instead of stdout")
    ap.add_argument("--changes", default="accept",
                    choices=["accept", "reject", "mark"],
                    help="tracked changes: accept (default), reject, or mark "
                         "deletions with ~~strikethrough~~")
    ap.add_argument("--sidenotes", default="inline",
                    choices=["inline", "skip", "only"],
                    help="footnotes and margin notes: Markdown [^n] references "
                         "with definitions at the end (default), skip them, or "
                         "output only the notes")
    ap.add_argument("--in", dest="in_section", metavar="HEADING",
                    help="only the section with this heading (see --outline)")
    ap.add_argument("--page", metavar="N[-M]",
                    help="only this page or page range (needs a page index; "
                         "build one with pages_edit.py index)")
    ap.add_argument("--outline", action="store_const", const="outline",
                    dest="to", help="shorthand for -t outline")
    ap.add_argument("--comments", action="store_const", const="comments",
                    dest="to", help="shorthand for -t comments")
    ap.add_argument("--links", action="store_const", const="links",
                    dest="to", help="shorthand for -t links: every hyperlink")
    ap.add_argument("--list-styles", action="store_const", const="styles",
                    dest="to", help="shorthand for -t styles")
    args = ap.parse_args(argv)

    doc = PagesDoc(args.file)
    paras = doc.all_paragraphs(args.changes, args.sidenotes)

    # --in / --page narrow the paragraph list before anything is rendered,
    # so every format honours the scope, not just Markdown.
    label = None
    if args.in_section and args.page:
        ap.error("use --in or --page, not both")
    if args.in_section:
        label, lo, hi = section_range(args.file, args.in_section,
                                      len(u16(doc._raw_text())))
        label = f"section {label!r}"
    elif args.page:
        lo, hi = page_range(args.file, args.page, len(u16(doc._raw_text())))
        label = f"page {args.page}"
    doc._scope = (lo, hi) if label else None
    if label:
        paras = [p for p in paras if lo <= p["offset"] < hi]
        if not paras:
            sys.exit(f"{label} contains no paragraphs")

    text = RENDERERS[args.to](doc, paras)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.output} ({len(text):,} chars, {len(paras)} paragraphs)",
              file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
