---
name: pages-edit
description: Edit an Apple Pages (.pages) document in place with pages_edit.py - replace text, add comments, insert or restyle paragraphs, tracked changes. Use for any request to change a .pages file. Dry run first.
---

# Editing a .pages document

`pages_edit.py` rewrites the package directly (no Pages needed). **Nothing it writes has
been opened in real Pages yet**; the tests only check what the tools read back. Tell the
user this before the first write, and prefer a copy of the document.

It needs Python 3.13+ (`python3 --version`; otherwise `uv run --no-project --python 3.13 python`
in place of `python3`). `${CLAUDE_PLUGIN_ROOT}` is this plugin's install folder; if it shows up
literally, the skill is running from a plain checkout, so use that checkout's folder.

## Rules

1. **Dry run first.** Every command previews unless `--write` is given. Show the user the
   preview (the `-`/`+` diff, or the paragraphs that would be inserted) and write only after
   they agree to that change.
2. **Pin the fingerprint.** Run `fingerprint` first, pass `--expect FP` on the write. The write
   is refused if the text moved since you looked.
3. **Do not write a document Pages has open**; Pages overwrites the edit on its next save.
   The tool refuses on a Mac when it can tell. Ask the user to close it.
4. **Keep the backup.** Never pass `--no-backup`. A `.bak` is written next to the file; for
   `history` / `revert` run `config --vcs on` first (it creates `.pages-vcs/` next to the document).
5. **Edit a copy** unless the user says to edit the original. Never `--write` on their only copy.
6. **Small steps.** Prefer a `plan` file for many replacements (one snapshot, one diff).
   Do not chain many `delete-paragraph` calls, and `import --replace-section` refuses above
   3 removed paragraphs: do not work around the limit (a known corruption, see README).
7. **After a write** the tool re-reads the file and prints the new fingerprint. Confirm with
   `python3 "${CLAUDE_PLUGIN_ROOT}/pages2md.py" --changes mark report.pages` and report what changed.
8. Search runs on the *accepted* text (tracked changes applied). A match that would swallow a
   page break, anchor or line break is refused: narrow it, do not use `--raw` to force it.

## The common edit

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" fingerprint report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" find "old text" report.pages                      # numbered matches
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" replace -f "old text" -r "new text" --expect FP report.pages          # preview
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" replace -f "old text" -r "new text" --expect FP --write report.pages  # after approval
```

Scope with `--in HEADING`, pick one match with `--occurrence N`, or use `--all`. Add `--track`
to record a native Pages tracked change instead of overwriting (the document must already
contain at least one tracked change).

## Comments, structure, plans, undo

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" comment add --on "anchor text" --text "note" --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" insert --after "Intro" --text "New paragraph." --style "Body" --expect FP report.pages
python3 "${CLAUDE_PLUGIN_ROOT}/pages_edit.py" retag --on "Advanced Usage" --style "Heading 3" --expect FP report.pages
```

Everything else (comment reply/delete, `format`, `delete-paragraph`, `import`, plan JSON,
`config`, `history`, `revert`, tracked-change details) is in `reference.md` beside this file.
Read it when you need one of those.
