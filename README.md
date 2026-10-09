# pages2md

Read and edit Apple Pages documents directly, without Pages, AppleScript, or a DOCX
round-trip. Two command-line tools and the lossless codec they share.

| | |
| --- | --- |
| `pages2md.py` | convert a `.pages` document to Markdown, plain text, or JSON |
| `pages_edit.py` | find and replace text, restructure paragraphs, import Markdown, write comments, keep a history |
| `iwa_codec.py` | the lossless IWA/protobuf layer both are built on |

**Requirements:** Python 3.13, run with [uv](https://docs.astral.sh/uv/) — the version is
pinned in `.python-version`, so `uv run pages2md.py doc.pages` is all it takes (the examples
below leave `uv run` out). Nothing else — no `protobuf`, no `snappy`, no Pages running. See
[`insights.md`](insights.md) for how the file format works and why these tools are built
the way they are, [`CHANGELOG.md`](CHANGELOG.md) for what changed, and
[`CLAUDE.md`](CLAUDE.md) if you are working on the code.

**Status:** reading is well covered by tests and real documents. Writing is more
delicate: the changes listed under *Unreleased* in the changelog are tested against
synthetic and real documents, but no file they wrote has been opened in Pages yet. Work
on a copy until they have been.

## Why

Pages' own export path (Pages → DOCX → pandoc) is lossy: bullet lists can be dropped
entirely, headings flatten unless you patch localized style names by hand, and
unresolved tracked changes leave both the original and the edit mixed into the text.
`pages2md.py` reads the `.pages` package directly instead, with none of that.

| | DOCX → pandoc | `pages2md.py` |
| --- | --- | --- |
| Headings | flattened unless style IDs are patched by hand | read from the style table |
| Lists | bullets can vanish entirely | recovered: bullet, numbered and nested |
| Tracked changes | both versions land in the text | accept / reject / mark |
| Needs Pages running | yes | no |

## Which file to point them at

A `.pages` file that lives in iCloud Drive is often a placeholder — `file` reports it as
a `MacOS Alias file`, not a real zip archive. Both tools detect this and refuse with a
clear message rather than failing obscurely; point them at the real local file instead
(download it fully, or use a local copy).

---

## pages2md.py — reading

```bash
pages2md.py report.pages                   # Markdown to stdout
pages2md.py -o out.md report.pages         # ...or to a file
pages2md.py -t plain -o out.txt report.pages
pages2md.py --list-styles report.pages
pages2md.py -t json report.pages
```

### Options

| Flag | Meaning |
| --- | --- |
| `-t, --to FORMAT` | `markdown` (default), `plain`, `json`, `outline`, `comments`, `links`, `styles`, `archives`, `storages` |
| `-o, --output FILE` | write to a file instead of stdout |
| `--notes MODE` | footnotes and margin notes: `inline` (default), `skip`, `only` (`--sidenotes` still works) |
| `--in HEADING` | only the section with this heading |
| `--page N[-M]` | only this page or page range |
| `--outline` | shorthand for `-t outline` |
| `--comments` | shorthand for `-t comments` |
| `--links` | shorthand for `-t links`: every hyperlink with its page and URL |
| `--list-styles` | shorthand for `-t styles` |
| `--changes MODE` | `accept` (default), `reject`, `mark` |

### Formats

- **markdown** — headings from the real paragraph styles, bold and italic runs, hyperlinks,
  bullet and numbered lists, footnotes, and tracked changes (see `--changes`). Details
  below.
- **plain** — text only, no markup.
- **json** — one object per paragraph: `offset`, `text`, `style`, `semantic`, `list`,
  `list_kind`, `list_level`, the character `runs` (`[start, end, bold, italic,
  underline, strikethrough]`), `links`, and the footnote fields. Use this when you want to do your own analysis; the offsets
  are what `pages_edit.py` works in.
- **outline** — the heading tree with character offsets and page numbers.
- **comments** — review comments with the text they are attached to.
- **styles** — a census of which paragraph styles are in use and how often. Good first
  look at an unfamiliar document.
- **archives** — a census of internal archive types per `.iwa` file. For spelunking the
  format, not for prose.
- **storages** — every text storage in the document (body, margin notes, captions, the
  generated table of contents), with its handle, id and anchor.

### What the Markdown contains

- **Links** — `[text](url)`. A URL with spaces or parentheses goes in `<...>`. Other
  kinds of smart field (dates, page numbers) are ignored.
- **Lists** — items of one list are a tight list (no blank lines between them). Bullet
  styles — Bullet, Dash, Note Taking, Image, … — all become `-`; numbered styles —
  Numbered, Lettered, Harvard, … — become `1.`, `2.`, `3.`, counting up from 1 and
  starting over after any other paragraph or where Pages restarts the list. Which kind a
  style is comes from the style's label type, not its name, so renamed and localised
  list styles work. Letters and Roman numerals are not kept (Markdown has none).
  Nested levels are kept: a child is indented under its parent (two spaces under `-`,
  three under `1.`), and each new sub-list counts from 1 again.
- **Emphasis** — `**bold**`, `*italic*`, `***both***`, and `~~strikethrough~~` (character
  formatting and tracked deletions alike, so overlapping spans become one). Underline has
  no Markdown form and is dropped. Whitespace stays outside the markers, and where emphasis and a link only
  partly overlap, the emphasis is cut at the link's edge so the markup nests.
- **Escaping** — text that would turn into markup is escaped: `*`, `` ` ``, `[`, `]`, `\`,
  a line that begins like a heading, quote, bullet, number or rule (`1\.`), `<tag>`,
  `&entity;`, `~~`, and underscores that are not inside a word. Ordinary prose, and
  `snake_case`, come out unchanged. `plain` and `json` are never escaped.
- **Footnotes** — see *Footnotes and margin notes* below.

### Review comments

Pages comments are review metadata rather than document text, so they stay out of the
Markdown. `-t comments` lists them with the passage each is attached to, every reply in
the thread, and the page:

```bash
pages2md.py --comments report.pages
pages2md.py --comments --in "Getting Started" report.pages
```

```
p.5   @1203  “Getting Started”
    A. Reviewer · 2026-01-12 09:14
      Needs a short example before diving into configuration.

p.12  @6840  “Install the dependencies listed in Appendix B before continuing.”
    A. Reviewer · 2026-01-15 14:30
      Link to the appendix directly?
    A. Reviewer · 2026-01-16 08:02
      Added above.
```

Comments are anchored to a *range* of text, so the quoted passage comes out with them,
and `--in` / `--page` scope the listing like any other output.

Timestamps are stored in UTC and shown in local time, which is what Pages displays.

### Footnotes and margin notes

The body flow is not the whole document. Footnotes — and Pages' side-column margin
notes — live in their own text storages, each anchored into the body by an attachment
table. A body-only reader skips them silently, which is what every DOCX/RTF export does.

Both tools reach them. `pages2md.py` renders footnotes as Markdown footnotes: a `[^1]`
where the reference stands, numbered in reading order, and the definitions at the end.
A footnote with several paragraphs indents the later ones; a manual line break inside a
note becomes a Markdown hard break:

```bash
pages2md.py report.pages                      # footnotes as [^n] (default)
pages2md.py --notes only report.pages     # just the definitions
pages2md.py --notes skip report.pages     # body only, no references
```

```
It works particularly well for *wicked problems*[^1] – ill-defined challenges.

[^1]: For more details see: Camillus: Strategy as a Wicked Problem
```

A reference that tracked changes delete takes its note with it (`--changes accept`), and
numbers stay the same under `--in` / `--page`, so a section keeps the numbers it has in
the whole document. JSON output carries `footnote` (a note's number) and `ref_nos` (the
numbers of the references in a paragraph). Plain text drops the references. A note
attached to anything other than a footnote reference, if one exists, is still printed
as a `>` blockquote.

The CLI still calls these storages `note1`, `note2`… (the name from before it was clear
they were footnotes), in `find`, `--where` and `-t storages`.

`pages_edit.py` searches and edits them too. Matches are tagged with the note they are in
and the body offset they are anchored at, and page numbers come from that anchor:

```bash
$ pages_edit.py find "configuration reference" report.pages
1 match(es) in the accepted text
    1. p.5 @6     [note3, anchored @1840] …<anchor> See Appendix B for the full [configuration reference].
```

`--where` restricts the search — `body`, `notes`, `all` (the default), or a single handle
like `note3`. Plans take the same key per edit. Because notes have their own offset
spaces, `--in` and `--page` select a note by **where it is anchored**, so scoping to a
chapter includes the notes that belong to it.

### Extracting one section

`--in` and `--page` narrow the output to part of the document. They apply **before**
rendering, so every format honours them — including `--list-styles`, which then reports
only that section's styles.

```bash
pages2md.py --outline report.pages                       # see the structure
pages2md.py --in "Getting Started" report.pages          # one chapter
pages2md.py --in "Prerequisites" report.pages             # one subsection
pages2md.py --page 5 report.pages                         # one page
pages2md.py --page 12-20 -o part.md report.pages
pages2md.py -t json --in "Advanced Usage" report.pages
```

A section runs from its heading to the next heading at the same or a higher level, so
naming a chapter takes its subsections with it and naming a subsection takes just that.
Boundaries are exact: nothing from the chapter that follows leaks in.

Heading matching prefers an exact hit, then a unique substring. An ambiguous name lists
the candidates rather than guessing:

```
$ pages2md.py --in "Getting" report.pages
'Getting' matches 2 headings ('Getting Started', 'Getting Help'); be more specific
```

`--page` needs a page index, since pagination is not stored in the document — build one
with `pages_edit.py index` (see below). Everything else works without it.

### Tracked changes

A Pages document stores the original *and* the revision when a change is pending, so
naive extraction yields text like `The team may wWork on this next quarter`. `--changes`
decides what you get:

```bash
pages2md.py --changes accept report.pages   # Work on this next quarter
pages2md.py --changes mark   report.pages   # ~~The team may w~~Work on this next quarter
pages2md.py --changes reject report.pages   # The team may work on this next quarter
```

`accept` is the default, so the output reflects the current, resolved text.

---

## pages_edit.py — writing

Replaces text in the document's body flow, its footnotes and its other text storages
(`--where` picks which). Every character index in that storage's attribute tables is
shifted with it, so styles, lists, links, comments and existing tracked changes stay
attached to the right words.

**Edits are a dry run unless you pass `--write`.**

```bash
pages_edit.py find "example" report.pages
pages_edit.py replace -f "old text" -r "new text" report.pages          # preview
pages_edit.py replace -f "old text" -r "new text" --write report.pages
```

### Commands

| Command | Does |
| --- | --- |
| `plan PLAN.json FILE` | apply many edits in one pass (dry run unless `--write`) |
| `fingerprint FILE` | print the document's text fingerprint |
| `retag FILE` | change a paragraph's style (and list style) |
| `format FILE` | apply or clear bold/italic on existing text |
| `insert FILE` | insert a new paragraph |
| `delete-paragraph FILE` | remove a whole paragraph |
| `import MD FILE` | insert Markdown as styled paragraphs |
| `comment add/reply/delete FILE` | write review comments |
| `outline FILE` | list the headings with character offsets and page numbers |
| `index FILE` | build the page index (asks Pages to lay the document out) |
| `find PATTERN FILE` | locate text, print character offsets and context |
| `replace FILE` | replace text (needs `--write` to actually do it) |
| `config FILE` | show or change the stored settings |
| `history FILE` | list stored versions |
| `revert REF FILE` | restore a stored version |

### replace options

| Flag | Meaning |
| --- | --- |
| `-f, --find TEXT` | the text to look for |
| `--find-file PATH` | read the search text from a file |
| `-r, --replace TEXT` | the replacement |
| `--replace-file PATH` | read the replacement from a file |
| `--all` | every occurrence |
| `--occurrence N` | only the Nth (default: the first) |
| `--regex` | treat the pattern as a regex; `\1` works in the replacement |
| `--where WHICH` | storages to search: `all` (default), `body`, `notes`, or `noteN` |
| `--in HEADING` | only inside that section |
| `--page N[-M]` | only on that page or page range |
| `--raw` | work on the unaccepted text (see below) |
| `--track` / `--no-track` | record as a tracked change, or override the setting |
| `--expect FP` | refuse to write unless the fingerprint still matches |
| `--write` | actually modify the file |
| `--no-backup` | skip the `.bak` copy (ignored when history is on) |

Long replacements are easier from files:

```bash
pages_edit.py replace --find-file old.txt --replace-file new.txt --write report.pages
```

### Narrowing by section or page

Searching the whole document turns up headings, cross-references and body text all at
once. `outline` shows the structure, and `--in` / `--page` scope an edit to part of it.

```bash
pages_edit.py outline report.pages                # the heading tree
pages_edit.py outline --max-level 1 report.pages  # chapters only
```

```
  p.1   @0      # Introduction
  p.5   @1203   # Getting Started
  p.12  @6840   # Advanced Usage
  ...
```

`--in` takes a heading name — exact match wins, otherwise a unique substring. A section
runs from its heading to the next heading at the same or a higher level, so naming a
chapter covers its subsections too. An ambiguous name lists the candidates instead of
guessing.

```bash
pages_edit.py find "the dependencies" --in "Getting Started" report.pages
pages_edit.py replace --in "Getting Started" --all \
    -f "the dependencies" -r "the required packages" --write report.pages
```

`--page` takes `N` or `N-M`, numbered as printed:

```bash
pages_edit.py find "example" --page 5 report.pages
pages_edit.py find "example" --page 5-12 report.pages
```

`--occurrence N` counts within the scope, so `--in "..." --occurrence 2` means the second
match in that section.

### The page index

**Pagination is not stored in the document.** Pages computes it at layout time, so page
numbers require asking Pages:

```bash
pages_edit.py index report.pages
```

It opens the document, reads each page's text, aligns that against the stored text
comparing only significant characters, and caches the result in `.pages-index.json`.
Once cached, `find` and `outline` show page numbers with no further help from Pages.

Two things to know:

- **It refuses to run if Pages already has the document open**, because it would reopen
  and close the file and discard your unsaved changes. Close it first, or index a copy.
- The index is a snapshot. Re-run it after edits that reflow the text, or page numbers
  drift. Everything else in the tool works without it.

### Searching matches what you see, not what is stored

Because the raw text still contains tracked-change leftovers, searching it would miss or
mis-land. Both `find` and `replace` work on the **accepted** text — the document as the
author sees it — and map each hit back to the stored offsets. If a match would straddle a
pending deletion the replacement region is not contiguous, so the edit is refused rather
than guessed at; resolve that edit in Pages first, or use `--raw`.

### What the matcher sees

`^` and `$` anchor to paragraphs. Pages leads some paragraphs with a page-break or
object-anchor character and puts manual line breaks (U+2028) inside some headings — a
two-line heading is really `Getting<LS>Started` internally — so the matcher reads all of
those as whitespace. The substitution is one character for one character, so every
offset stays valid.

A replacement that would *consume* one of those characters is refused, since it would
silently delete a page break or a heading's line break:

```
$ pages_edit.py replace -f "Getting Started" -r "Quick Start" --occurrence 2 report.pages
match at offset 1203 contains a page break, object anchor or manual line break;
replacing it would delete that character. Narrow the match to one side of it.
```

Note that headings and prose cross-references often share wording, so check which
occurrence you are hitting — `find` numbers them.

### Tracked changes

With tracking on, an edit does not overwrite anything: the original stays as a pending
deletion and the replacement is inserted after it, so both show up in Pages' review pane
with Accept and Reject buttons.

```bash
pages_edit.py config --track on report.pages      # make it the default
pages_edit.py replace -f old -r new --write report.pages
pages_edit.py replace -f old -r new --track --write report.pages     # just this once
pages_edit.py replace -f old -r new --no-track --write report.pages  # override
```

Afterwards, `pages2md.py --changes mark` shows what is pending:

```
~~This will effect~~This will affect the outcome
```

The document must already contain at least one tracked change, since a new change record
is modelled on an existing one to keep the author attribution right. If it has none, make
one edit in Pages with tracking on and then use the tool.

### Edit plans

One `replace` at a time is fine for a one-off, but a review pass is a batch: every write
reloads and rezips the whole package, and offsets shift between runs, so by the second
command the numbers you reasoned about are stale. A plan fixes that — many edits
described declaratively, resolved against **one** snapshot, reviewed as a diff, then
applied in a single pass.

```json
{
  "fingerprint": "a1b2c3d4e5f6a1b2",
  "edits": [
    { "in": "Getting Started", "all": true,
      "find": "the dependencies", "replace": "the required packages",
      "why": "consistent with the Appendix wording" },
    { "in": "Advanced Usage", "find": "Set up", "replace": "Configure",
      "why": "matches the imperative style elsewhere" },
    { "page": "5", "find": "should work", "replace": "will work" }
  ]
}
```

```bash
pages_edit.py plan edits.json report.pages          # review the diff
pages_edit.py plan edits.json --write report.pages  # apply, one snapshot
```

A bare JSON list of edits works too, if you do not want to pin a fingerprint.

Per-edit keys: `find`, `replace` (both required), plus `in`, `page`, `all`,
`occurrence`, `regex`, `raw` — the same meanings as the `replace` flags — and `why`,
which shows up in the dry run and the history commit message. `--track` / `--no-track`
apply to the whole plan.

Because edits address text by **section and content** rather than by offset, a plan stays
meaningful while you work elsewhere in the document.

Plans are validated before anything is written:

```
edits 1 and 2 overlap at characters 1200-1210; they cannot both be applied
edit 1: unknown key(s) ['inn']; known keys are ['all', 'find', 'in', ...]
edit 2: no match for 'zzzznope' in the accepted text
edit 1: missing `replace`
```

Overlap detection matters: two edits touching the same characters would corrupt each
other, and which one "won" would depend on the order you happened to list them in.

### Fingerprints

A fingerprint is a short hash of the document's text — every text storage except the
generated table of contents: the body, then footnotes, then captions and text boxes — of
the *text*, not the file, because Pages rewrites the whole package on every save, so the
zip bytes change when nothing you care about did. `pages2md.py` and `pages_edit.py` share
one definition, so a page index and a plan agree about what "the same document" means.

```bash
pages_edit.py fingerprint report.pages            # a1b2c3d4e5f6a1b2
```

Pin it in a plan, or pass `--expect` to a single `replace`. If the document has moved on,
the write is refused before anything is resolved:

```
the document has changed since edits.json was written
(expected a1b2c3d4e5f6a1b2, found 9f8e7d6c5b4a3f2e).
Its offsets may no longer mean what they did -- re-read the document and rebuild the plan.
```

Every write prints the new fingerprint, so a follow-up plan can pin to it. Together with
the open-in-Pages guard, this closes both ways an edit could be built on text that is no
longer there: someone editing in Pages *after* you looked, and Pages overwriting your
write afterwards.

### Structural editing

Beyond replacing text, paragraphs can be retagged, inserted and removed. Each is located
by a piece of its text, the same way `replace` works.

```bash
pages_edit.py retag --on "Advanced Usage" --style "Heading 3" --write report.pages
pages_edit.py retag --on "Configure the client" --style "Heading 3" --list None --write report.pages
pages_edit.py format --on "Start here." --bold --write report.pages
pages_edit.py format --on "Read this first." --plain --write report.pages
pages_edit.py insert --after "Advanced Usage" --text "…" --style "Body" --write report.pages
pages_edit.py delete-paragraph --on "This paragraph is outdated" --write report.pages
```

`--style` takes the English style name (`Body 1`, `Heading 1`–`4`, `Title`, `Subtitle`, …).
Case does not matter, and a name without its number (`Body`) works when only one style
fits. The document's own list is printed if you name one it does not have.

Paragraph styles are run-length, and most paragraphs carry a "no change" entry that
inherits from the one before. So every structural edit also re-states the following
paragraph's style — without that, inserting a heading silently turns the rest of the
chapter into headings too. The same goes for character styles, tracked changes, comments
and list levels: deleting a paragraph carries the run in force after it back to where the
paragraph was, and an inserted paragraph starts plain and outside any tracked change.
A new paragraph takes its list level from the one before it, so it is that item's sibling.

### Importing Markdown

```bash
pages_edit.py import new-section.md --after "Advanced Usage" --write report.pages
pages_edit.py import replacement.md --replace-section "Getting Started" --write report.pages
```

`--replace-section` swaps a whole section's content for new Markdown, heading included.
**It refuses above 3 paragraphs removed** — see *Known issue* below; it is safe only for
small sections until a real Pages round-trip shows the cause is fixed.

Headings (`#`–`####`), paragraphs and bullets (`-`/`*`) become real styled paragraphs
(always at list level 0; numbered lists, links and nesting are not imported).
The dry run shows what each block will become:

```
import 4 paragraph(s) from new-section.md after @6840:
  [Heading 2       ] Troubleshooting
  [Body             ] If the build fails, check the log first…
  [Body/Bullet      ] Missing dependency: re-run the installer.
  [Body             ] Open an issue if none of the above helps…
```

Inline `**bold**` and `*italic*` become **real character styling**, reusing the
document's own weight styles — so an imported bullet comes out in the document's own
house style, `- **Start here.** Read this before anything else.`, not with literal
asterisks. The dry run counts the emphasis runs per block.

`retag --list None` clears a bullet, which matters when retagging a list item to a
heading — otherwise you get a bulleted heading.

### Writing comments

```bash
pages_edit.py comment add --on "Configure the client" --text "…" --write report.pages
pages_edit.py comment reply --at 6840 --text "…" --write report.pages
pages_edit.py comment delete --at 6840 --write report.pages
```

`--on` attaches the comment to the matched text; `--at` addresses an existing thread by
the anchor offset `pages2md.py --comments` prints. New comments are attributed to the
document's existing annotation author.

Comments do not change the body text, so they do not move any offsets — and the text
fingerprint stays the same across them.

### Internal source control

A git repo in `.pages-vcs/` keeps a snapshot of the document itself — deliberately
separate from any project's own history, since committing a multi-megabyte binary on
every text tweak would bloat it.

```bash
pages_edit.py config --vcs on report.pages
pages_edit.py history report.pages
pages_edit.py revert HEAD~1 report.pages
```

Each write snapshots before and after; the pre-edit snapshot is skipped when it already
matches `HEAD`; and `revert` is itself recorded, so nothing is ever lost. With the
setting off, a plain `.bak` is written next to the document instead.

Settings live in `.pages-edit.json` beside the document:

```json
{ "vcs": true, "track": true }
```

Add `.pages-vcs/` and `*.pages.bak` to your own project's `.gitignore`.

### After every write

The file is re-opened and re-parsed to confirm the new text reads back, and the result is
printed. That catches a corrupt write immediately rather than the next time you open
Pages.

---

## Known issue: large multi-paragraph deletes corrupt the document

Repeated `delete_paragraph` calls (what `clear_range` / `import --replace-section` use
internally) are not yet safe past a handful of paragraphs in one write. Verified against
a 22-paragraph section: deleting up to 9 sequential paragraphs works cleanly
(confirmed via a real Pages round-trip), but at 10 the resulting document opens and saves
in Pages fine — and **every heading in the entire document loses its style**, with the
page count roughly doubling. A single isolated deletion (not preceded by others) has also
made Pages hang on save.

The cause is not found. Table shifting was checked by hand against a real document and
shifts correctly. One lead has since been fixed — deleting a paragraph could drop the end
of a character-style or tracked-change run and let it spread over everything after it,
which would look much like this — but that has only been shown in tests, not in Pages.
The limit stays until it is. `import --replace-section` therefore **refuses
above 3 removed paragraphs** rather than risk producing a file like the one above. Do not
raise that limit, call `clear_range` directly, or chain many `delete-paragraph` calls in
one write until this is understood. See `insights.md` for the investigation, and please
open an issue if you can reproduce or root-cause it.

## Not considered yet

`pages2md.py` reads the text flow and what is attached to it. Everything below is **not
handled**, mostly because no sample document has exercised it yet — behaviour is
unverified unless stated. Each one that needs a sample is listed in
`tests/samples/README.md`; a document containing it is the most useful contribution.

| Not considered | What to expect today |
| --- | --- |
| **Images** | The object-anchor character is dropped, so an image should leave no trace in the Markdown (never seen with a real image). Nothing is extracted from `Data/`, and there is no alt text or caption link. |
| **Tables** | The table's cell text lives in its own storages and is not placed in the flow; expect it to be missing from `markdown`/`plain` (it may show up in `-t storages`). No Markdown table is built. |
| **List details** | Start-at values, "continue numbering from the previous list", custom number formats (letters, Roman numerals) and bullet characters are not read. Numbered lists nested under bullets (and the reverse) work in the tests but have not been seen in a real document. |
| **Shapes, text boxes and floating objects** | A text box's text is a separate storage: listed by `-t storages` and searchable by `pages_edit.py find`, but not placed in the Markdown. Shapes, lines and charts are not read. |
| **Headers, footers, page numbers** | Not read. |
| **Page layout documents** | Documents made from the *Page Layout* templates have no single text flow; only the largest storage is read. |
| **Sections, columns, page and section breaks** | Section and page breaks only separate paragraphs; they are not marked in the output. A `\x0e` with no attachment entry is assumed to be a section break — unverified. |
| **Table of contents** | Listed by `-t storages` but left out of the Markdown, since Pages regenerates it. |
| **Endnotes** | Probably read like footnotes, but untested. |
| **Cross-references, bookmarks, citations, index entries** | Not read. Only external hyperlinks are. Links stored outside the smart-field table (for example on an image) are not seen. |
| **Character formatting beyond bold, italic and strikethrough** | Underline, super- and subscript, colour, highlight, font and size are dropped. Monospace text is not turned into code spans. |
| **Paragraph formatting** | Alignment, indents, spacing, borders and shading are dropped. Styles other than headings and Title/Subtitle (Quote, Caption, Block Quote, custom styles) are rendered as plain paragraphs. |
| **Equations and math** | Not read. |
| **Media, forms, review marks other than comments and tracked changes** | Not read. |
| **Right-to-left and mixed-direction text** | Read in storage order; direction attributes are ignored. |
| **Document properties** | Title, author, language and template are not exported. |
| **Writing the above** | `pages_edit.py` edits text, its styles and its comments only. It does not create or modify images, tables or links, and it cannot set a list level (`import` writes level 0). Deleting a paragraph keeps the levels of the items after it; it does not carry list *restart* flags, so deleting the first item of a numbered list may leave the next one without its restart. |
| **Older and unusual files** | Pages '09 (XML) documents, folder-style `.pages` packages and password-protected files are not supported. |

## Caveats

- **Do not `--write` a document Pages currently has open** — Pages will overwrite your
  edit on its next save. Close it first.
- The **body flow**, footnotes and the other text storages in `Document.iwa` (captions,
  text boxes) are editable; headers and footers are not reached. Comments cannot be
  *written* inside a text box (Pages drops them), though they can be read.
- Indices are shifted only within the edited storage. If some other archive holds offsets
  into the body text it would not be updated; no evidence of one was found, but it cannot
  be ruled out — which is why the snapshot happens *before* the write.
- Body text split across multiple internal chunks is rejected rather than guessed at.
- Offsets (`@123`, `--at`, page bounds) count UTF-16 code units, as Pages does: an emoji
  counts as two. A match that would cut an emoji in half is refused.
- `pages2md.py` reads the largest text storage, which is the body. Short documents whose
  longest text lives in a text box are not handled.

## Using with Claude

The repo ships two [Claude Code skills](https://docs.claude.com/en/docs/claude-code/skills)
in `.claude/skills/`, so Claude can use the tools without an MCP server or any setup:

| Skill | Does | Idle cost |
| --- | --- | --- |
| `pages-read` | reads a `.pages` file as Markdown, JSON, outline, comments or links; never writes | its description only |
| `pages-edit` | edits one: dry run first, fingerprint pinned with `--expect`, backups kept, the known limits spelled out (`reference.md` holds the rest) | its description only |

Only the short descriptions are always in Claude's context; a skill's instructions load when
a request matches it, and `pages-edit` stays unloaded for read-only work.

- **Claude Code (terminal, VS Code extension, web/cloud sessions):** open the checkout, or a
  repository that contains your documents next to a copy of `.claude/skills/`; nothing else to
  install. Cloud sessions see only files in the cloned repository.
- **Other projects on your Mac:** copy the two folders to `~/.claude/skills/` and call the
  scripts by absolute path.
- Both need a shell and Python 3.13 (`uv run python`). Page numbers (`--page`) need
  `pages_edit.py index`, which only works on a Mac with Pages.

The same rules apply as for the command line: edits are a dry run until `--write`, and
nothing the editor writes has been opened in Pages yet, so use a copy.

## Development

`CLAUDE.md` has the working rules (architecture, the offset convention, how to investigate
a field). In short: write the failing test first, keep `CHANGELOG.md` current in the same
commit, never commit a document with a real author's name in it, and check any write
against a **copy** of a real document.

## Verifying a change to these tools

First, the unit tests (stdlib only, no Pages needed; `tests/PLAN.md` lists what is still
to write and `tests/samples/README.md` the real documents they run against):

```bash
uv run python -m unittest discover -s tests
```

Then, on a Mac with Pages, the round-trip harness, which is the check that settles whether
Pages accepts what the editor writes (see `tests/pages_roundtrip.py`):

```bash
uv run python tests/pages_roundtrip.py all /tmp/roundtrip     # ~25 cases, a minute or two
```

It copies the sample documents, applies each kind of write to a copy (replace, tracked
replace, insert, import, retag, format, comments, footnote and emoji edits, deleting 1, 3,
9 and 12 paragraphs), has Pages open each copy and *save it as a new file*, and compares what
`pages2md.py` reads back — Markdown with changes accepted and marked, headings, paragraph
styles, comments — with what was written. Pages' own page count must not grow after a case
that only deletes. Every sample also has an unedited control, so what Pages itself
normalises is not mistaken for damage. It writes `report.md`; paste that back if anything
fails. Without Pages, `prepare` and `check` support doing the opening and saving by hand.
**It has not yet been run against a real Pages**, so treat its first report as a test of
the harness too.

Then three checks against a real document, in increasing order of authority:

```bash
# 1. every IWA layer still round-trips byte-for-byte
python3 -c "
import iwa_codec as C, zipfile
with zipfile.ZipFile('report.pages') as z:
    for n in [n for n in z.namelist() if n.endswith('.iwa')]:
        p = C.iwa_decode(z.read(n))
        assert C.pack_archives(C.archives(p)) == p, n
        assert C.iwa_decode(C.iwa_encode(p)) == p, n
print('round-trip OK')"

# 2. re-render and diff: only the edited paragraph should move
pages2md.py -o after.md copy.pages && diff before.md after.md

# 3. the one that counts -- Pages itself opens it
osascript -e 'tell application "Pages"
  set d to open POSIX file "/full/path/copy.pages"
  set r to (count of pages of d) & " / " & (count of characters of (body text of d))
  close d saving no
  return r
end tell'
```

Always run these on a **copy**. Checks 1 and 2 are the tools agreeing with themselves;
only check 3 proves Pages accepts the result.

## Changes

See [`CHANGELOG.md`](CHANGELOG.md).

## License

MIT — see [LICENSE](LICENSE).
