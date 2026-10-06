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

- [ ] Deleting a paragraph that holds a footnote reference leaves the note's storage
      (`other1`) and its comment archives behind.
- [ ] An empty final paragraph inherits the style and list level of its predecessor
      (retag, insert at the end, delete the last paragraph). Deleting the last paragraph,
      or one followed by a break character, leaves an empty paragraph where the preceding
      separator should go.
- [ ] Tracebacks instead of messages: `pages2md` on a missing file or a package folder;
      `pages_edit` on a zip without `Document.iwa` or with garbage IWA; an invalid regex
      or replacement group reference; a missing `--find-file` or `.md`.
- [ ] A first comment is impossible in a document that has none yet.

## Features

- [ ] Extract all links (`pages2md.py --links`: text, URL, page or section).
- [ ] Support a Pages package that is a folder instead of a zip.

## Code health

- [ ] Body detection is duplicated between reader and editor (rule 7 in `CLAUDE.md`).
- [ ] Naming drift: margin note, sidenote, note.
- [ ] Dead imports (`pages_edit.fingerprint_parts`, unused names in `fixture.py` and the
      tests); `T_AUTHOR` is unused; `squeeze` is defined inside a loop.
- [ ] `Document.apply()` is O(edits × tables), about 38 ms per edit; `Document` loads the
      whole package including media.
- [ ] Coverage is 85%. Missing tests: `plan`, `section_range`, `--page`, the page index,
      config, history.
- [ ] `git` missing or `commit.gpgsign` set on the machine breaks the history commands.

## Samples wanted

- [ ] `fidelity.pages`: tables, images, nested numbered lists, text boxes.
- [ ] Replace the real comment author in `emoji.pages` and `kitchen-sink.pages`.
