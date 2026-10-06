#!/usr/bin/env python3
"""Replace a name inside a .pages file, leaving everything else byte-for-byte.

Comments carry the author's name (in AnnotationAuthorStorage.iwa). Run this on
any document before it goes into tests/samples/:

    python3 tests/anonymize_author.py in.pages out.pages "Real Name" "Sample Author"

The name is replaced inside the protobuf, with every enclosing length rewritten,
so the file stays valid. It prints how many strings it replaced. Check the result
with `pages2md.py --comments out.pages`. Other places a name can hide -- the text,
hyperlinks, the preview image -- are not touched; look at them yourself.
"""
import os, sys, zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import iwa_codec as C


def swap(buf, old, new):
    """(rewritten message, number of replacements), recursing into sub-messages."""
    out, hits = [], 0
    for num, wire, val in C.tokenize(buf):
        if wire == 2:
            if val == old:
                val, hits = new, hits + 1
            elif old in val:
                try:
                    inner, n = swap(val, old, new)
                    if n and C.emit(C.tokenize(val)) == val:   # really a message
                        val, hits = inner, hits + n
                except Exception:
                    pass
        out.append((num, wire, val))
    return C.emit(out), hits


def replace_string(src, dst, old, new):
    """Copy `src` to `dst` with the string `old` replaced by `new`; -> count."""
    old, new = old.encode("utf-8"), new.encode("utf-8")
    total = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.endswith(".iwa") and old in C.iwa_decode(data):
                arcs = C.archives(C.iwa_decode(data))
                for _info_bytes, msgs in arcs:
                    for msg in msgs:
                        if old in msg[1]:
                            msg[1], n = swap(msg[1], old, new)
                            total += n
                data = C.iwa_encode(C.pack_archives(arcs))
            zout.writestr(info.filename, data, compress_type=info.compress_type)
    return total


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 4:
        sys.exit(__doc__)
    src, dst, old, new = args
    print("replaced", replace_string(src, dst, old, new))


if __name__ == "__main__":
    main()
