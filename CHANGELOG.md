# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project has no tagged releases yet, so everything below sits under *Unreleased*.
Add each change here in the same commit that makes it.

## [Unreleased]

### Added

- Footnotes render as Markdown footnotes: a `[^n]` where the reference stands, numbered
  in reading order for the whole document, with the `[^n]:` definitions collected at the
  end. Later paragraphs of a note are indented and a manual line break inside one becomes
  a hard break. A reference deleted by tracked changes takes its note with it; `--in` and
  `--page` keep the document-wide numbers.
- JSON output: paragraphs carry `footnote` (a note's number) and `ref_nos` (the numbers of
  the references they contain).
- A stdlib-only test suite (`python3 -m unittest discover -s tests`, 140 tests):
  - `fixture.py` builds minimal real `.pages` packages (text, attribute tables, character
    styles, tracked-change templates, footnote archives).
  - Codec round-trips, structural-edit table tests, `format`, footnotes, the Pages guard,
    and the review fixes below.
  - `tests/PLAN.md` lists the tests still to write, grouped by review finding.
- Real documents in `tests/samples/` with tests that read them and edit copies of them:
  `emoji.pages` and `kitchen-sink.pages` (Pages 15.4) and `sample-content.pages`
  (Pages 14.5, a German-localised guide with footnotes; the comment author is replaced
  with "Sample Author"). `tests/samples/README.md` says what each contains.
- Hyperlinks render as `[text](url)` (a URL with spaces or parentheses goes in `<...>`).
  They are read from the smart-field table of every storage, so links inside footnotes
  work too. JSON paragraphs carry `links`.
- Numbered lists render as `1.`, `2.`, … — counting up, restarting after any other
  paragraph or where Pages restarts the list. Whether a list style is bulleted or numbered
  comes from its label type, so Lettered, Harvard and renamed styles work. JSON
  paragraphs carry `list_kind` and `list_start`.
- A "Not considered yet" section in the README (images, tables, nested lists, text
  boxes, headers and footers, character and paragraph formatting, …), and a wish list
  for a sample document in `tests/samples/README.md`.
- Nested lists: list levels are read (field 6) and rendered as indented Markdown, with
  numbering counted per level and each new sub-list starting at 1. JSON paragraphs carry
  `list_level`.
- `~~strikethrough~~` for strikethrough character formatting, merged with tracked
  deletions where they overlap.
- `tests/samples/formatting.pages` (Pages 15.4): a Title, character formatting, a body
  link and a three-level list.
- This changelog.

### Changed

- **Offsets are UTF-16 code units**, as Pages stores them, so an emoji counts as two.
  This applies to `@123` offsets, `--at`, page bounds and `--in` ranges. Both tools
  work on a UTF-16 view of the text and convert back only to show or write it. An edit
  whose match would split an emoji is refused.
- Footnotes are no longer printed as a `>` blockquote after their paragraph (see
  *Added*). An attachment anchored on something other than a footnote mark is still a
  blockquote. `--sidenotes` now covers footnotes; the storages are still called
  `note1`, `note2`… in `find`, `--where` and `-t storages`.
- **Fingerprints cover every text storage except the generated table of contents**, in
  both tools, so documents with captions or text boxes get a new value. A fingerprint
  stored earlier (`--expect`, a plan's `fingerprint`, a page index) will be reported as
  stale for those documents; rebuild the plan or run `index` again.
- Direct replacements rewrite only the part that changes, so bold, comments and other
  formatting beginning inside unchanged text stay on the same characters.
- A tracked edit that only partly overlaps an existing tracked change is now refused,
  not just one fully inside it.
- `format --plain` together with `--bold` or `--italic` is refused.
- `comment reply` and `comment delete` search every storage in scope. An ambiguous match
  names the storages it was found in.
- `revert` writes through a temporary file, so a failure cannot leave a half-written
  document.
- JSON `runs` are `[start, end, bold, italic, underline, strikethrough]`; strikethrough is
  new. `char_format` returns four flags.
- Markdown lists are tight: consecutive items of one list have no blank line between them.
  Numbered items used to come out as `-`.
- Markdown output escapes text that would turn into markup (`*`, `` ` ``, `[`, `]`, `\`,
  `<tag>`, `&entity;`, `~~`, non-word underscores, and lines that begin like a heading,
  quote, list item or rule). Prose and `snake_case` are unchanged. `plain` and `json`
  are not escaped.
- The Markdown importer needs text right inside `*`/`**` markers and an underscore at a
  word edge before it counts as emphasis.
- Documentation: `insights.md` records the UTF-16 offsets, the footnote marker, and how
  comment tables vary; the README documents footnotes and the offset units.

### Fixed

- Deleting a paragraph let character styles and tracked changes that began earlier run on
  into the following text, and dropped the next paragraph's list style. The run in force
  after the deleted span is now carried back to its start. Deleting the empty slot before
  a page break no longer writes a style entry onto the break. This may be the cause of
  the batch-deletion corruption described in `insights.md`; that is not yet confirmed in
  Pages itself, so `import --replace-section` keeps its three-paragraph limit.
- Inserted text, and a zero-width direct edit, no longer joins the formatting or tracked
  change that precedes it.
- A tracked pure insertion or deletion no longer writes a zero-length span, which marked
  unrelated text up to the next entry; no change archive is created for the empty side.
- Comment ranges are clamped like the other tables, so a comment ending inside a shrunk
  edit no longer quotes the text after it. Run-length comment tables (used by the body in
  Pages 15.4) are carried across paragraph deletion.
- `format` leaves one clean run: formatting inside the range no longer wins back its span.
  `--plain` works (it used to fail with "no plain  character style"). A storage with no
  character table gets one. `import` no longer re-reads the file for every emphasis run.
- **Footnote references split paragraphs.** A footnote's `\x0e` was treated as a section
  break, cutting every footnoted paragraph in two and leaving the second half without
  its style. Reader and editor now find paragraph boundaries on the same view, in which
  an attachment-anchored `\x0e` is inline.
- Stray asterisks around footnote references (`*wicked problems***`): runs over invisible
  characters only are skipped.
- Markdown emphasis: bold and italic together render as `***x***` (it was bold only),
  touching runs merge (`**foobar**`, not `**foo****bar**`), and `--changes mark` keeps
  emphasis next to struck text.
- `find` no longer crashes on a match in a caption or text box when a page index exists.
- The reader's fingerprint skipped captions and text boxes; the editor's did not.
- The AppleScript used to build the page index broke on paths containing `"` or `\`.
- Writing no longer crashes where `osascript` does not exist (the "Pages has it open"
  check now reports no conflict there).
- The Markdown importer turned `my_var_name` into italic `myvarname`.
- **Underline was read from the wrong style property** (10, which only a footnote reference
  sets); it is 11. The JSON `runs` underline flag is now right.
- Deleting a paragraph dropped its entry in the list-level table, demoting the list items
  after it that relied on that entry (a nested item became a top-level one).

[Unreleased]: https://github.com/lennarthennigs/pages2md/commits/claude/youthful-cori-fa1roq
