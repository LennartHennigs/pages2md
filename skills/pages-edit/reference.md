# pages_edit.py reference

All commands are a dry run unless `--write` is given. Examples show the preview form; add
`--write` only after the user has seen the preview. Replace `FP` with the output of
`fingerprint`. Style names are English (`Body 1`, `Heading 1`-`4`, `Title`, `Subtitle`);
case is ignored and `Body` works when only one style fits.

## Locating text

`--in HEADING` (section), `--page N[-M]` (needs a page index, Mac only), `--where
all|body|notes|noteN`, `--occurrence N`, `--all`, `--regex` (`\1` works in the replacement),
`--raw` (unaccepted text; avoid). Long text: `--find-file` / `--replace-file`.

## Plans (many replacements, one pass)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" plan edits.json report.pages
```

`edits.json` is a list of edits, or `{"fingerprint": "FP", "edits": [...]}`. Per-edit keys:
`find`, `replace` (required), `in`, `page`, `all`, `occurrence`, `regex`, `raw`, `why`.
Overlapping edits, unknown keys, missing matches and a changed fingerprint are refused
before anything is written. `--track` / `--no-track` apply to the whole plan.

```json
{"fingerprint": "FP", "edits": [
  {"in": "Getting Started", "all": true, "find": "the dependencies",
   "replace": "the required packages", "why": "consistent wording"}
]}
```

## Comments

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" comment reply --at 6840 --text "Done." --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" comment delete --at 6840 --expect FP report.pages
```

`--at` is the anchor offset printed by `pages2md.py --comments`. Comments do not change the
text, so the fingerprint stays the same. Comments cannot be written inside a text box.

## Structure

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" format --on "Start here." --bold --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" format --on "Read this first." --plain --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" retag --on "Configure the client" --style "Heading 3" --list None --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" delete-paragraph --on "This paragraph is outdated" --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" import new-section.md --after "Advanced Usage" --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" import replacement.md --replace-section "Getting Started" --expect FP report.pages
```

`import` turns `#`-`####` headings, paragraphs and `-`/`*` bullets into styled paragraphs
(level 0 only; no numbered lists, links or nesting); `**bold**` and `*italic*` become real
styling. `--replace-section` refuses above 3 removed paragraphs; do not raise or bypass that.
`retag --list None` clears a bullet, which you want when turning a list item into a heading.
Deleting a paragraph that holds a footnote reference is refused.

## Tracked changes

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" config report.pages                    # show settings
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" replace -f old -r new --track --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --changes mark report.pages              # ~~old~~new shows what is pending
```

A tracked edit leaves the original as a pending deletion and inserts the replacement. The
document must already contain one tracked change to model the author on; if it has none, ask
the user to make one edit in Pages with tracking on. A tracked edit inside someone else's
pending change is refused.

## History and undo

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" config --vcs on report.pages           # keep snapshots in .pages-vcs/
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" history report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" revert HEAD~1 report.pages
```

With history off, each write leaves `report.pages.bak` next to the file instead. A revert is
itself recorded. Add `.pages-vcs/`, `*.pages.bak`, `.pages-index.json` and `.pages-edit.json`
to the project's `.gitignore`.
