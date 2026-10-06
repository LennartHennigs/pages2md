# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
The project has no tagged releases yet, so everything below sits under *Unreleased*.
Add each change here in the same commit that makes it.

## [Unreleased]

### Added

- `pages2md.py --links` (or `-t links`) lists every hyperlink: page (with an index), the
  linked text and the URL. `--in` and `--page` narrow it like any other format.
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
- `tests/anonymize_author.py`: replaces a name inside a `.pages` file (comment authors)
  without disturbing anything else, with tests.
- `CLAUDE.md`: working notes for the repository (architecture, the UTF-16 offset
  convention, the run-length rule for structural edits, how to find out what a field
  means, sample privacy, the changelog rule).
- `tests/pages_roundtrip.py`: a harness for macOS that has Pages open and re-save a copy of
  each sample after each kind of write (22 cases including deleting 1, 3, 9 and 12
  paragraphs and an unedited control per sample), then compares what is read back and
  Pages' page counts. Stages `prepare`, `run`, `check` and `all`, with a manual route for
  `run`; writes `report.md`. Tested with a stand-in for Pages; not yet run against the real
  application.
- `pyproject.toml`, `.python-version` and `uv.lock`: one supported Python (3.13), run with
  uv. The earlier "Python 3.8+" claim was never tested and is dropped.
- This changelog.

### Changed

- `insert --after X` and `import --after X`, when a page break, section break or object
  anchor ends X: the new paragraphs go between X and the break, on X's page, and the break
  ends the last of them. They used to go after the break, onto the next page or after the
  anchored object.
- One table shifter: a single edit is the one-pass shift with one edit, and the per-edit
  step for the tracked-change tables is one helper shared by both `apply` paths. No change
  in output.
- A write reads the saved file back once, not three times: `save` hands the checked
  document to `verify` and `commit`. The well-formedness check finds duplicate entries in
  linear time (a 30,000-entry table took 13 s, now well under one), and footnote marks
  are cached with the paragraph view.
- Faster edits on large documents. `Document.apply` moves each attribute-table entry once
  for all the edits of a call instead of rewriting every table after every edit: 3,000
  replacements in a 109,000-character, 800-entry-per-table document take 50 ms (about 38 ms
  per edit before, i.e. minutes). A single `shift_table` also reads its table with a
  byte-level scan, which speeds up `apply_tracked`, `insert` and `delete-paragraph` about
  four-fold. The bytes written are identical to the one-edit-at-a-time path, which is kept
  as `_apply_sequential` and compared against on every sample.
- `Document` reads only the `.iwa` files when it opens a package; images and previews are
  read when `save` copies them (a 60 MB package: 335 ms and 61 MB of memory, now 20 ms and
  1 MB). A package that changed on disk between opening and saving is refused.
- The editor takes the body storage from the reader (`PagesDoc._body_id`) instead of
  finding "the largest text storage" a second way. No change on any sample.
- One word for the text storages anchored in the body: **note** (a footnote, or a margin
  note, which is unnumbered and renders as a blockquote). `pages2md.py --notes` replaces
  `--sidenotes` (the old name still works), and the JSON key `sidenote` (a paragraph's
  note storage) is now `note`. Handles stay `note1`, `note2`…, and `--where notes` is
  unchanged.
- Housekeeping, no change in output (checked against every sample): dead imports and the
  unused `T_AUTHOR` removed, the span-squeezing helper in the reader is a function of its
  own.
- `delete-paragraph` and `import --replace-section` refuse text that holds a footnote
  reference. They used to remove the reference and leave the note's storage and archives
  behind, unreferenced; nobody has seen Pages open such a file.
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
- Large documents are faster to read and to edit, with identical output (measured on a
  synthetic document of 1000 paragraphs, 250 footnotes and 1000 links): reading
  paragraphs about 2.7× faster, rendering Markdown 2.4×, and finding a paragraph's
  bounds in the editor about 400× (the footnote-aware paragraph view is computed once per
  edit, not once per lookup). Links, footnote references and list kinds are no longer
  rescanned for every paragraph.
- The reader and editor now share one definition of a footnote reference
  (`footnote_marks`), of a storage's text (`storage_text`) and of the entry in force at an
  index (`row_at`); `format` uses the same table helpers as the other edits. Removed the
  unused `_emphasize` and `P_BASELINE`.
- `Document.save` checks what it wrote before it replaces anything: every attribute table must be
  sorted, hold one entry per index and stay inside the text, and the file must read back with both
  tools. If not it refuses, leaves the document untouched and says so. `verify` (the check after
  a write) now reads with `pages2md` as well, and `tests/pages_roundtrip.py` reports ill-formed
  tables in what we wrote.
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
- Documentation brought up to date: the README's status note (no write from this branch has
  been opened in Pages yet), what is editable (footnotes and other storages, not only the
  body), the fingerprint definition and a development section; `insights.md` gains a table
  of what a null entry means in each run-length table, the deletion and insertion bugs
  that came from it, the evidence habits (single-variable samples, built-in style names,
  the embedded preview image) and a longer "still unproven" list.

### Fixed

- Markdown: struck text (tracked deletions with `--changes mark`, strikethrough) that
  partly overlaps bold or italic text or a link gave crossing markers (`**ab~~c**d~~`);
  the spans are cut so the markers nest. Struck text is trimmed of surrounding spaces, so
  `~~ 2~~` (which renders as plain text) is now ` ~~2~~`.
- Markdown: a struck heading word that is also bold rendered as `~~**word**~~`; a
  heading takes its weight from its style, so it is `~~word~~`.
- Markdown: footnotes are numbered among the ones shown. A note whose reference is in
  deleted text left a gap (`[^2]` with no `[^1]`).
- `--links` printed a page computed from an offset in the wrong units (a note's link used
  its anchor plus its position in the note); it is the page the paragraph starts on.
- `^` and `$` in a regex search (`find`, `replace`, plans) matched at a footnote reference
  in the middle of a sentence; a footnote mark is not a line break.
- A write whose result could not be read back at all (undecodable IWA data) got past the
  save check, because the reader reports that through `sys.exit`: the `.tmp` file was left
  behind and there was no "refusing to write". `verify` had the same gap.
- "Changed on disk" is checked on every save and before the backup or history snapshot is
  taken; it used to run only when an image was still unread, and after the snapshot.
- A tracked edit inside someone else's pending insertion was accepted and split that
  insertion in two around it; it is refused, as one inside a pending deletion already was.
- A malformed attribute-table entry whose index runs past its own end is left to the
  general tokenizer instead of being rewritten with an empty payload.
- The document history works on a machine that signs commits (`commit.gpgsign=true` made
  every write fail with `config --vcs on`); signing is off for the history repo only.
- A missing `git`, or a git command that fails, ends in one message and changes nothing,
  not a traceback (a write with history on is refused before the document is touched).
- `replace --expect FINGERPRINT` only checked the fingerprint when writing, so a dry run
  against a changed document looked fine; it is checked first.
- A plan entry with `"page": 2` (a number) crashed; `page` takes a number or a string.
- `comment add` works in a document that has no comment yet: the author is taken from the
  document's annotation author when there is no existing comment to copy it from. (Untested
  in Pages: no sample without any comment exists; see `insights.md`, "Still unproven".)
- Deleting the last paragraph, or one that a page break or object anchor ends, left an empty
  paragraph behind (the previous paragraph's newline in front of nothing). That newline
  goes with it now.
- Messages instead of tracebacks: a missing file, a folder, a zip that is not Pages, a
  package without `Index/Document.iwa` and damaged IWA data (both tools); an invalid
  regular expression or replacement group reference; an unreadable `--find-file`,
  `--replace-file` or Markdown file. `pages_edit` no longer leaves those files open.
- `revert HEAD~1` restored the wrong version when the document had been edited outside
  `pages_edit` since the last snapshot: the safety snapshot taken first shifted what
  `HEAD~1` meant. The ref is resolved before it, and an unknown ref changes nothing.
- A comment thread whose `next` chain loops back on itself hung `pages2md --comments`
  and `comment reply`; the chain is now followed once.
- A zero-width regex match at the very end of the text (`$`) in a document with tracked
  deletions mapped to the accepted length instead of the end of the raw text.
- `comment add` over a manual line break said "replacing it would delete that character".
- `insert --style Body` failed on a document whose style is `Body 1`: style names now
  match case-insensitively, and without the number when only one style fits.
- `--where` is no longer accepted by `retag`, `insert`, `import`, `format` and
  `delete-paragraph`, where it was silently ignored.
- The `--replace-section` refusal pointed at `tools/insights.md`; it is `insights.md`.

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
- **Inserting before (or, next to an anchor, after) a paragraph added a spurious empty paragraph.**
  Pages leads some paragraphs with a page break, object anchor or section break, and
  `insert_paragraph` only counted a newline as "already at a paragraph start", so
  `insert --before "What It Is"` (and `import --before`) in a real document put an extra newline
  after the anchor and left an empty paragraph behind; `--after` did the same when a break
  character, not a newline, followed the paragraph. Every break character now counts as a
  separator. A footnote reference still does not (it sits inside a sentence). Found by random
  insertions on the real guide.
- **An empty search text destroyed the document.** `replace -f "" -r X --all --write` wrote `X`
  between every character (3638 insertions into a 3622-character document: "Titel" became
  "XTXiXtXeXlX"); a plan entry with `"find": ""` did the same. An empty search text is now refused
  everywhere one is taken (`find`, `replace`, `plan`, `--find-file`, `retag`, `insert`,
  `delete-paragraph`, `format`, `import`, `comment`) with one message that says what to do instead;
  zero-width patterns such as `^` and `$` with `--regex` still work. A plan entry whose `find` or
  `replace` is not a string is refused too, naming the entry, and `insert --after ""` no longer
  crashes (an empty string counted as "not given").
- **Replacing a whole annotated stretch with nothing wrote duplicate table entries.** Deleting a
  bold word (or a comment anchor, a language run, a tracked change) collapsed its run to zero
  width and left two entries at one index. `pages2md.py` then crashed reading the file
  (`TypeError`) while the editor's own re-read said "OK", and Pages never writes such tables.
  The editor now keeps one entry per index when it shifts a table (the later one, which is what
  the run-length rule says applies); the reader sorts by index alone and no longer crashes on
  duplicates in a file it is given. Found by random edits on the real samples; the round-trip
  case `replace-all-with-footnote` had been writing five of them.
- Deleting a paragraph dropped its entry in the list-level table, demoting the list items
  after it that relied on that entry (a nested item became a top-level one).

[Unreleased]: https://github.com/lennarthennigs/pages2md/commits/claude/youthful-cori-fa1roq
