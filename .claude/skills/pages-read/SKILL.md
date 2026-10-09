---
name: pages-read
description: Read an Apple Pages (.pages) document as Markdown, plain text, JSON, outline, comments or links with pages2md.py. Use to read, summarise, search or review a .pages file. Read-only.
---

# Reading a .pages document

`pages2md.py` reads the package directly: no Pages, no network, nothing written. Run it
from the pages2md checkout (or by absolute path) on Python 3.13+ (`uv run python`).

```bash
uv run python pages2md.py report.pages                     # Markdown to stdout
uv run python pages2md.py -t plain report.pages            # text only
uv run python pages2md.py -t json report.pages             # paragraphs with offsets, styles, runs, links
uv run python pages2md.py --outline report.pages           # heading tree with offsets
uv run python pages2md.py --comments report.pages          # review comments and the text they sit on
uv run python pages2md.py --links report.pages             # every hyperlink
uv run python pages2md.py --list-styles report.pages       # which paragraph styles are used
```

## Narrow it before reading it all

```bash
uv run python pages2md.py --in "Getting Started" report.pages     # one section (heading name, substring ok)
uv run python pages2md.py --changes mark report.pages             # tracked changes: accept (default), reject, mark
uv run python pages2md.py --notes only report.pages               # just footnotes and margin notes (skip, inline)
uv run python pages_edit.py find "needle" report.pages            # where does this text occur?
uv run python pages_edit.py outline --max-level 1 report.pages    # chapters only
uv run python pages_edit.py fingerprint report.pages              # short hash of the text, to detect changes
```

For a long document, start with `--outline`, then read the sections you need with `--in`.
Comments are not in the Markdown; ask for them with `--comments`.

## Things that go wrong

- **"not a Pages package" / alias file**: a `.pages` file that lives in iCloud Drive may be a
  placeholder. Ask for the real local file (download it fully, or use a copy).
- **`--page N` fails** with "no page index": page numbers are not stored in the file. Only
  `pages_edit.py index` creates them, and it needs a Mac with Pages. Use `--in HEADING` instead.
- **Not read**: tables, images, headers and footers, text boxes in the Markdown, equations,
  most character and paragraph formatting. If the answer might depend on these, say so
  rather than guessing (README, "Not considered yet").
- Offsets such as `@1203` count UTF-16 code units, as Pages does; an emoji is two.

To change a document, use the `pages-edit` skill. This one never writes.
