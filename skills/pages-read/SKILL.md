---
name: pages-read
description: Read an Apple Pages (.pages) document as Markdown, plain text, JSON, outline, comments or links with pages2md.py. Use to read, summarise, search or review a .pages file. Read-only.
---

# Reading a .pages document

`pages2md.py` reads the package directly: no Pages, no network, nothing written. It needs
Python 3.13+ (check `python3 --version`; otherwise use `uv run --no-project --python 3.13 python`
in place of `python3`). `${CLAUDE_PLUGIN_ROOT}` is this plugin's install folder; if it shows up
literally, the skill is running from a plain checkout, so use that checkout's folder.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" report.pages                     # Markdown to stdout
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" -t plain report.pages            # text only
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" -t json report.pages             # paragraphs with offsets, styles, runs, links
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --outline report.pages           # heading tree with offsets
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --comments report.pages          # review comments and the text they sit on
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --links report.pages             # every hyperlink
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --list-styles report.pages       # which paragraph styles are used
```

## Narrow it before reading it all

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --in "Getting Started" report.pages     # one section (heading name, substring ok)
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --changes mark report.pages             # tracked changes: accept (default), reject, mark
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --notes only report.pages               # just footnotes and margin notes (skip, inline)
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" find "needle" report.pages            # where does this text occur?
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" outline --max-level 1 report.pages    # chapters only
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" fingerprint report.pages              # short hash of the text, to detect changes
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
