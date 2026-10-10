# CLAUDE.md

Working notes for this repository. Read `README.md` for what the tools do and
`insights.md` for how the file format works; this file is about how to change the code
without breaking it.

## What this is

Four Python files, standard library only (no `protobuf`, no `snappy`, no Pages), on one
Python version: 3.13, pinned in `.python-version` and run with `uv` (`uv run python ...`):

| File | Role |
| --- | --- |
| `iwa_codec.py` | lossless protobuf + IWA (Snappy, chunk framing, archive framing). Round-trips byte for byte. |
| `pages2md.py` | the **reader**: `PagesDoc`, Markdown/plain/JSON rendering, outline, page index, fingerprint, UTF-16 helpers. |
| `pages_edit.py` | the **editor**: `Document`, attribute-table surgery, comments, tracked changes, plans, history. |
| `pages_mcp.py` | an MCP server (stdio, `--http`) that runs the two scripts above as subprocesses; it imports none of them. |

The dependency points one way: `pages_edit` imports `pages2md` imports `iwa_codec`.
Anything the editor needs that is also a reading concern (outline, fingerprint, style
lookups, `flow_view`, the UTF-16 helpers) lives in `pages2md.py`, so the two tools cannot
disagree. Keep it that way.

## Commands

```bash
uv run python -m unittest discover -s tests          # everything, a few seconds
uv run python -m unittest tests.test_footnotes       # one module
python3 pages2md.py tests/samples/formatting.pages            # render a sample
python3 pages_edit.py find "text" tests/samples/formatting.pages
uv run python tests/anonymize_author.py in.pages out.pages "Real Name" "Sample Author"
uv run python tests/pages_roundtrip.py all /tmp/rt   # macOS + Pages only: does Pages accept our writes?
```

`osascript` does not exist off macOS. `pages_has_open` returns False there, and tests that
drive `pages_edit.main` patch it (`mock.patch.object(E, "pages_has_open", return_value=False)`).

## Rules that have each cost a bug

1. **Tests first, and watch them fail.** Write the test, run it against the current code,
   see it fail for the reason you expect, then fix. `tests/fixture.py` builds minimal real
   `.pages` files (`write_pages`, `table`, `char_style`, `list_style`, `footnote`, …);
   `tests/samples/` has real documents. Extend the fixture rather than mocking the parser.
2. **Offsets are UTF-16 code units**, because Pages' tables are. `PagesDoc` and `Document`
   work on a *UTF-16 view* of the text (`u16`, `from_u16`, `show` in `pages2md.py`): an
   emoji is two characters in it. User input goes through `u16()` on the way in, printed
   text through `show()` on the way out. Never mix a Python code-point index with a table
   index. An edit may not split a surrogate pair.
3. **Attribute tables are run-length, and a structural edit changes text it does not
   touch.** An entry holds until the next one. Before deleting or inserting, read what is
   in force after the edit point; afterwards pin it explicitly (`_restate_following`,
   `carry_run`, `carry_payload`, `isolate_insertion`). What a *null* entry means differs
   per table: see the table in `insights.md` ("Run-length structures bite every time").
   Insertion wants a different answer for different tables (a new bold-run paragraph
   starts plain; a new list item takes its predecessor's level).
   A table must stay well-formed: sorted, **one entry per index**, inside the text.
   `shift_table` collapses duplicates (`last_per_index`) and `Document.save` refuses to write
   anything `table_problems` flags, so a new edit path cannot quietly break it; add the new
   path to `tests/test_table_integrity.py`.
4. **Edit a structure through the rule that interprets it**, not through its raw bytes
   (`put_span` rebuilds a change table from its spans; do not patch entries).
5. **Paragraph boundaries come from `flow_view`**, in both tools: a `\x0e` with an
   attachment-table entry is a footnote reference inside a sentence, not a section break.
6. **Writes are dry runs unless `--write`.** Keep it so. Every new write path goes through
   `commit()` (open-in-Pages guard, snapshot or `.bak`, atomic save, re-read, fingerprint).
7. **One definition per thing.** The fingerprint, the paragraph split, the field numbers
   and the style lookups each exist once, in `pages2md.py`. A second copy is how the
   reader and the editor came to disagree before.
8. **Never claim Pages accepts a write that only the tests have seen.** The README and
   `insights.md` ("Still unproven") say what has and has not been opened in Pages;
   `tests/pages_roundtrip.py` is how it gets checked (add a case there for every new kind
   of write, and run it on a Mac before saying a write path is safe). Do not
   raise the three-paragraph limit on `import --replace-section`, and do not call
   `clear_range` from new code, until a real Pages round-trip shows the batch-deletion
   corruption is gone.

## Finding out what a field means

Do not guess from names in other projects (numbers-parser's `.proto` field names are a
hint, not evidence). Measure:

- Make a sample in Pages that varies **one** thing and compare its tables with a
  neighbour. Dump a storage with `pages2md.parse_fields` / `iwa_codec.tokenize`; the
  scratch-script pattern is `PagesDoc(path)`, `doc._body()`, `doc._table(f, field)`,
  `doc.arcs[id] -> (type, message, file)`.
- Use what Pages labels. Its built-in character styles ("Underline", "Strikethrough") are
  a dictionary of property numbers. Each package embeds `preview.jpg`, a rendering of page
  one: read it to check anything visible (list levels, strikethrough, footnote marks).
- Compare across Pages versions: `tests/samples/` has 14.5 and 15.4 files.
- Write down what you found in `insights.md`, with the sample it came from.

## Samples and privacy

- A document in `tests/samples/` is public. Throwaway text only. Comments carry the author's
  name: run `tests/anonymize_author.py` on every file first and look at `pages2md.py
  --comments`. Also check hyperlinks and the text itself.
- `tests/samples/README.md` says what each file contains and lists the documents still
  wanted. Edits in tests run on a temporary copy, never on the sample.
- Never run `--write` on a user's real document without a copy.

## Claude plugin and skills

The repo is a Claude Code plugin marketplace: `.claude-plugin/marketplace.json` lists one
plugin, `pages2md`, whose root is the repo root (`source: "./"`), so installing it copies
the scripts too. `skills/pages-read` and `skills/pages-edit` teach Claude the command line
and call the scripts as `${CLAUDE_PLUGIN_ROOT}/pages_edit.py`. When a flag, command or
limit changes, change the skill in the same commit: `tests/test_skills.py` runs every
command quoted in a fenced block and fails on a flag argparse no longer accepts. Keep
`SKILL.md` short (only the description is always in context) and put the long tail in
`pages-edit/reference.md`. The read skill must never mention `--write`. Do not add
a `.claude/skills/` copy (the skills would load twice) and do not pin a `version` in
`plugin.json` (users would stop receiving updates). Check manifests with
`claude plugin validate .`; `CLAUDE.md` at the plugin root and the missing version are
expected warnings.

## MCP server

`pages_mcp.py` builds a command line per tool call and runs the real script, so it cannot
disagree with the CLI; keep it that way (no importing `Document`). When a `pages_edit`
subcommand or flag changes, update `EDIT_COMMANDS` / the builders there; `tests/test_mcp.py`
fails if a command is no longer a subcommand. Keep the safety layer: paths resolved with
`realpath` and checked against the roots, option values passed as `--opt=value` and
positionals after `--` (a value must never become a flag), writes behind `write: true` plus
`expect`, one write at a time. The HTTP token comes from `PAGES_MCP_TOKEN` only, never an
argument. Four tools on purpose: every schema is in context on each session, and a test caps
the size. Do not add a `.mcp.json` to the plugin (it would put the schemas in every session
that already has the skills).

## Docs and changelog

- `CHANGELOG.md` (Keep a Changelog): add the entry in the **same commit** as the change,
  under *Unreleased* → Added / Changed / Fixed. Say what a user would notice, including
  anything that makes an old fingerprint, plan or index stale.
- `README.md`: user-facing behaviour. Keep *Not considered yet* honest: when a feature is
  added, remove its row; when something is learned to be unhandled, add one.
- `insights.md`: how the format works and what was learned the hard way. Put the evidence
  next to the claim, and say when a claim rests on a single sample.
- `tests/PLAN.md`: tests still to write, grouped by finding. Update it when you add or
  retire one.
- Docs are in English. The maintainer writes English and German; answer in the language
  of their message.

## Git

Work on a feature branch and push to it; do not open a pull request unless asked. Run
the whole test suite before committing,
and compare the rendered output of every sample against the previous commit when you
change the reader: only the differences you meant to make should show.
