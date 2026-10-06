# TODO

Open work, newest thinking first. Remove an item in the commit that finishes it; the
change itself goes in `CHANGELOG.md`. Tests still to write are in `tests/PLAN.md`.

## Needs a Mac with Pages

- [ ] Run `uv run python tests/pages_roundtrip.py all /tmp/roundtrip` and read `report.md`.
      Nothing written by `pages_edit` has been opened in Pages yet (see `insights.md`,
      "Still unproven").
- [ ] Find the cause of the "10+ deletions strip every heading style" corruption; then lift
      the three-paragraph limit on `import --replace-section` and allow `clear_range`.

## Bugs

- [ ] Deleting a paragraph that holds a footnote reference is refused. Do it properly
      (remove the note's storage, its attachment archive and any comments in it) once
      Pages has been shown a file like that.
- [ ] The empty paragraph after a final newline inherits its predecessor's style and list
      level (retag, insert at the end). Needs a look at what Pages does.
- [ ] A first comment is impossible in a document that has none yet.

## Features

- [ ] Support a Pages package that is a folder instead of a zip.

## Code health

- [ ] Body detection is duplicated between reader and editor (rule 7 in `CLAUDE.md`).
- [ ] `Document.apply()` is O(edits × tables), about 38 ms per edit; `Document` loads the
      whole package including media.
- [ ] Coverage is 85%. Missing tests: `plan`, `section_range`, `--page`, the page index,
      config, history.
- [ ] `git` missing or `commit.gpgsign` set on the machine breaks the history commands.

## Samples wanted

- [ ] `fidelity.pages`: tables, images, nested numbered lists, text boxes.
- [ ] Replace the real comment author in `emoji.pages` and `kitchen-sink.pages`.
