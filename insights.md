# Insights — Reading `.pages` Files Directly

Findings from reading the Pages source without Pages, AppleScript, or a DOCX
round-trip. The result is `pages2md.py` — a dependency-free, pandoc-style CLI — and
`pages_edit.py`, which writes back.

Numbers below are from testing against a real multi-chapter document (roughly 140
printed pages, ~80,000 characters, ~800 paragraphs) to keep the evidence concrete, with
its actual content left out.

## Why Bother

A DOCX → pandoc pipeline works, but it is lossy and fragile:

| | DOCX → pandoc | Direct IWA read |
| --- | --- | --- |
| Headings | silently flattened unless style IDs are patched by hand | read from the style table, no patching |
| Bullet lists | **can vanish entirely** | recovered |
| Tracked changes | both original *and* revision land in the text | accept / reject / mark |
| Needs Pages running | yes (slow AppleScript export) | no |
| Needs a style-name rename | yes | no |

The bullet loss and the tracked-change contamination are the two that actually corrupt
a proofread.

On the test document: 822 paragraphs, 14 H1 / 33 H2 / 7 H3, 36 bullets, 236 bold runs,
73 KB of Markdown out.

## The Format, Bottom Up

A `.pages` file is a zip. Since iWork 2013 the content is in `Index/*.iwa`, and each
layer has a gotcha:

1. **Chunk framing.** Each `.iwa` is a sequence of 4-byte headers: byte 0 is a flag
   (0 = compressed), bytes 1–3 are a little-endian length. Concatenate the blocks.
2. **Snappy, Apple's variant.** Raw Snappy — no stream header, no checksums, so
   `python-snappy`'s stream API will not read it. Copy operations may self-overlap, so
   the copy loop must be byte-wise, not a slice.
3. **`TSP.ArchiveInfo` framing.** A varint length, an `ArchiveInfo` message, then its
   payloads back to back. `ArchiveInfo.field 1` = archive id, `field 2` = repeated
   `MessageInfo` whose `field 1` is the message type and `field 3` the payload length.
4. **Protobuf without schemas.** Apple never published the `.proto` files, but a
   schema-less wire walk (`{field_no: [value]}`) is enough. No protobuf library needed.

Test document: 2,262 archives across 7 `.iwa` files, 288 KB decompressed.

## Message Types That Matter

| Type | Meaning |
| --- | --- |
| 2001 | `TSWP.StorageArchive` — text. 261 of them in the test document; the **largest is the body flow** |
| 2021 | character style |
| 2022 | paragraph style |
| 2023 | list style |
| 2060 | a tracked change |

Style **names** are not in `Document.iwa` — they are in `Index/DocumentStylesheet.iwa`,
so you must index every `.iwa` before resolving refs.

## The One Thing That Actually Matters

Inside `StorageArchive`, `field 5` = paragraph styles, `field 7` = list styles,
`field 8` = character styles, `field 21` = insertions, `field 22` = deletions. Each is an
`ObjectAttributeTable`: entries of `{1: char_index, 2: ref}`.

They all look alike. **A null entry — field 2 simply absent — means something different
in each one**, and there is no way to tell from the bytes. Determined empirically:

| Table | Null entry means | How it was proven |
| --- | --- | --- |
| Paragraph style (5) | *no change here* — the current style persists | Pages' generated TOC lists every chapter opener, which is only possible if they all carry a heading style |
| List style (7) | n/a — this table has no nulls; it uses an explicit `text-0-liststyle-None` ref to end a list | every entry carries a ref |
| Character style (8) | *no override here* — back to the paragraph's own formatting | a paragraph's bold lead-in is 14 chars; carrying it forward bolded the next 807 |
| Insertions / deletions (21, 22) | *end of span* — no change object here means not part of a change | a round-trip test with two nearby edits confirmed it |

So paragraph and list styles want the nulls **dropped** before a nearest-preceding
lookup; character styles and the change tables want them **kept** as boundaries.

Getting this wrong is quiet rather than loud. Treating paragraph-style nulls as resets
left 386 paragraphs with no style and silently dropped two chapters from the heading
list — with no error anywhere. See *How I Got This Wrong*.

## Other Gotchas

### Style names are two levels down, and often absent

A `ParagraphStyleArchive` has no name at its top level. The nested `TSS.StyleArchive`
(field 1) holds `field 1` = display name (localized, e.g. German in a German-locale
document) and `field 2` = a *semantic* identifier (e.g. `"text-11-paragraphstyle-Heading 1"`).
**Key the output off field 2** — it is stable across locales, which is exactly what the
DOCX route loses.

Many styles are anonymous local overrides with both names empty; follow `field 3`
(parent) until a name appears. Skipping this hides a meaningful fraction of a document's
styles — around 40 out of a few hundred on the test document.

### `\x04` and `\x05` lead a paragraph, they do not end it

The text uses `\n` for paragraph breaks, but Pages also emits `\x04` (page break),
`\x05` (object anchor) and `\x0e` (section break) **before** a paragraph's text. Splitting
on `\n` alone leaves paragraph starts a few characters short of their style entries, so
headings silently resolve to body text. Split on `[\n\x04\x05\x0e]`.

**Except `\x0e` is not always a break.** In documents with footnotes, a footnote's
reference is a `\x0e` in the middle of a sentence, with an entry at its offset in the
attachment table (field 16) pointing at the footnote's storage. Treating it as a section
break cut every footnoted paragraph in two -- the sentence broke around the note and the
second half lost its paragraph style. Seen in Pages 14.5 and 15.4 files. A `\x0e`
*without* an attachment entry is still treated as a break (nothing has shown what else it
could be). Both tools find paragraph boundaries on `flow_view`, the text with those
references neutralised, so they cannot disagree.

Also present: `U+2028` (line separator) inside headings that wrap across two lines in the
original layout, and `U+FFFC` (object replacement character) as an inline object
placeholder.

### Tracked changes are the real correctness issue

The storage holds the original *and* the revision, unmarked. Raw extraction produces
merged text like `"The team may wWork on this"` and duplicated paragraphs — a chapter
opener can appear twice, once in each version. This is not a parser artifact; it is what
the file contains.

`field 21` are insertions, `field 22` are deletions; the `2060` archive's `field 1` is
the kind (1 = insert, 2 = delete). On the test document: 298 insertion spans (24,788
chars), 268 deletion spans (11,526 chars). Accepting changes drops the deleted spans and
yields the current, resolved text:

- `--changes accept` → `Work on this next quarter` (default)
- `--changes mark` → `~~The team may w~~Work on this next quarter`
- `--changes reject` → `The team may work on this next quarter`

### Bold/italic live in the style properties

`field 11` of a style archive is the properties message: `field 1` = bold, `field 2` =
italic, `field 3` = size (LE float), `field 5` = font name, `field 11` = underline,
`field 12` = strikethrough. (An earlier version of these notes said `field 10` was
underline. It is not: Pages' own built-in "Underline", "Strikethrough", "Italic" and
"Emphasis" styles set 11, 12, 2 and 1, and field 10 appears only on the style of a
footnote reference, which is superscript. Seen in Pages 14.5 and 15.4 files.)
Sniffing the font name alone found only 13 of 236 bold runs on the test document —
read the booleans.

Don't emit inline `**` inside headings: a heading's weight comes from its paragraph
style, so the character runs underneath it are redundant and produce stray `****`.

### Watch your own offset arithmetic

When filtering deleted characters out of a paragraph, advance the running offset by the
paragraph's **original** width. Advancing by the shrunken length drifts every subsequent
lookup — this quietly deleted tens of kilobytes of good text in an early version.

### Character indices count UTF-16 code units

Every attribute table indexes the text the way NSString does: in UTF-16 code units, not
characters. An emoji or any other character outside the Basic Multilingual Plane is one
Python character but two units, so each one shifts every later index by one. Measured
on `tests/samples/emoji.pages` (Pages 15.4): with two emoji in front, the bold word, the
heading and the comment sit at Python index 4/21/45 but at table index 6/23/49.

Both tools therefore work on a *UTF-16 view* of the text (`pages2md.u16`): a string in
which each astral character is spelled as its surrogate pair. Its `len()` and slices
count exactly what the tables count, so no offset arithmetic needs converting. Text
goes back through `from_u16` only to be shown or written. Offsets the tools print
(`@123`) are in these units too, so they agree with Pages' tables and with each other.
An edit whose boundary falls between the two halves of a pair is refused.

### Hyperlinks, list kinds and list restarts

- **Hyperlinks** are field 11 of a text storage, a run-length smart-field table: an
  entry points at a `type 2032` archive and runs to the next entry, a null entry ends
  it. The archive's field 1 is a UUID string, field 2 the URL. Other smart fields (dates,
  page numbers) share the table with other archive types. In `sample-content.pages` the
  only links are in a footnote, so they live in that footnote's storage, not the body's.
- **List kind** is the first label type of the list-style archive (field 11, one value per
  level): 0 none, 1 image, 2 bullet text, 3 numbered. "Lettered", "Numbered" and "Harvard"
  are all 3. The semantic names in field 1 stay English in a German document, but the
  label type is what to trust.
- **List levels** are field 6, run-length, entries `{index, level, 0}`: an entry holds
  until the next. In `formatting.pages`: `This` = 0, `Is` and `A bulleted` = 1 (one entry
  covers both), `list` = 2, then an entry back to 0. The document's preview image agrees.
  Deleting a paragraph used to drop its entry and demote the paragraphs that relied on it.
- **List restarts** are field 14, entries `{index, first, second}`: `first = 1` at the
  paragraph that starts a list, `0` at the next one to say "continue". Not read: any
  start-at value (probably `second`).

## How I Got This Wrong

Worth recording, because the failure mode is specific to this format.

I changed two things at once — the paragraph-splitting rule *and* the null-entry
semantics — and judged them by a single number, the H1 count. Carry-forward plus the
broken `\n`-only split gave 5 headings; exact-match plus the correct split gave 57. I
concluded exact-match was right. The combination I never tried — carry-forward plus the
correct split — is the one that is actually correct.

That wrong model produced a plausible-looking false finding: two chapter openers
appeared to carry no paragraph style, which I first reported as a document bug to go and
fix. Their table entries really did have field 1 only, no ref — so the bytes were not in
question; the *interpretation* was. Both headings turned out to be fine.

Two lessons:

- **Change one variable at a time**, especially when the only feedback is an aggregate
  count. A count can move the right direction for the wrong reason.
- **Find in-document ground truth.** The generated TOC is produced by Pages itself from
  paragraph styles, so it is an independent oracle: if a paragraph appears there, it
  carries a heading style, full stop. Guessing at semantics from field presence was never
  going to settle it. Every row of the null-semantics table above is now pinned to a
  concrete observation rather than a plausible reading — and the useful signal that the
  model is right is that the "no style" bucket disappeared entirely.

## The CLI

```bash
pages2md.py report.pages                  # Markdown (changes accepted)
pages2md.py -t plain -o out.txt report.pages
pages2md.py --list-styles report.pages    # paragraph-style inventory
pages2md.py -t json report.pages          # paragraphs + runs + offsets
pages2md.py -t archives report.pages      # archive census, for spelunking
pages2md.py --changes mark report.pages   # see the edits
```

It refuses an iCloud placeholder with a useful message: a `.pages` file that has not
finished downloading from iCloud is a `MacOS Alias file`, not a real document.

## Writing Back

Reading is forgiving; writing is not. `pages_edit.py` replaces text in the body flow, and
`iwa_codec.py` is the lossless codec underneath it.

### Round-trip first, edit second

The whole thing rests on one property: **every layer must re-encode byte-for-byte**
before anything is changed. `tokenize`/`emit` preserve field order and wire type (a
`{num: [value]}` dict loses both and will not round-trip); `archives`/`pack_archives`
rewrite each `MessageInfo` length; `iwa_decode`/`iwa_encode` handle the chunk framing.
All 7 `.iwa` files of the test document round-trip exactly, and that test is what makes
an edit safe rather than hopeful.

### You need a Snappy compressor, and a trivial one is bad

Literal-only Snappy is valid and easy, but it can inflate a stylesheet archive several-
fold (46 KB to 328 KB on the test document). A ~30-line hash-chained LZ77 with greedy
matching got the main document archive to 162 KB against Apple's own 177 KB — smaller
than the original. Verify it by decompressing your own output, not by eye.

### The real work is the character indices

This is the part that makes naive text replacement destroy a document. Every attribute
table in the storage is keyed by character offset, so changing text length invalidates
all of them. One 26-character replacement shifted **5,071 indices** on the test
document.

The shift rule for an edit over `[start, end)` with length delta `d`:

| index | becomes |
| --- | --- |
| `i <= start` | `i` |
| `i >= end` | `i + d` |
| inside | clamped into the replacement |

Find the tables **structurally** — a message whose fields are all #1, each with a varint
field #1 — rather than by field number. The body storage has 18 of them and only a few
are identified; detecting by shape keeps the unknown ones in sync too.

### Edit in the accepted view, write to raw offsets

The raw text still contains tracked-change leftovers, so a search for text as the author
sees it will miss or land in the wrong place. `pages_edit` searches the accepted view and
maps each hit back through the deletion spans. If a match would straddle a deletion the
replacement region is not contiguous in raw, so the edit is refused rather than guessed
at.

### Repack the zip faithfully

Preserve entry order and the original compression method (these packages use
`ZIP_STORED`), and write via a temp file plus `os.replace` so an interrupted save cannot
leave a half-written document.

### Only Pages can really validate it

Every check above is the reader agreeing with the writer, which proves nothing about
whether Pages will open the result. It does: a pristine copy opened at 142 pages /
83,237 characters, and an edited copy at 142 pages / **83,263** — same pagination,
character count up by exactly the edit. Re-rendering to Markdown differed in exactly one
paragraph, with heading, bullet, bold and tracked-change counts untouched.

Worth running that AppleScript check on a copy after any change to the writer:

```bash
osascript -e 'tell application "Pages"
  set d to open POSIX file "/path/copy.pages"
  set r to (count of pages of d) & " / " & (count of characters of (body text of d))
  close d saving no
  return r
end tell'
```

### Writing native tracked changes

An edit can be recorded the way Pages records its own, so it lands in the review pane
with Accept and Reject buttons instead of overwriting anything.

The trick is that it is **not** a replacement at all. The original text stays exactly
where it is and the new text is inserted directly after it, so the operation is a pure
insertion at `end`, plus a deletion mark over `[start, end)`. That keeps the index
shifting simple: everything at or after `end` moves by `len(new)`, and the deleted span
needs no adjustment.

A change record is a type `2060` archive: `field 1` is the kind (1 = insert,
2 = delete), `field 2` the author session, `field 3` a nested fixed64 Core Foundation
timestamp (seconds since 2001-01-01), `field 4` a UUID string. Rather than invent one,
clone an existing archive of the right kind and give it a fresh id, timestamp and UUID —
that inherits the author attribution, which is otherwise a reference into structures
worth not guessing at. New archive ids come from one past the highest in the file.

The span is then opened by writing an entry carrying the reference at `start` in the
deletions table (`field 22`) or `end` in the insertions table (`field 21`), with a
terminator entry at the far end — but only when the table does not already define
something there, or you would clobber a neighbouring change.

Verified round-trip through all three views, plus Pages: a pristine copy opened at
142 pages / 83,237 characters and a tracked copy at **83,280** — exactly 43
characters more, the inserted text, with the original retained as pending.

**A tracked change makes Pages render the whole document's markup.** That same edit
later laid out at 202 pages against the pristine 142, with the character count
unchanged. It is not corruption. A PDF export — real rendered output, not a view
setting — proves it: the pristine PDF does **not** contain a pending deletion's
original wording, while the tracked PDF does, alongside both sides of the new edit.
Writing one change record flips Pages from final view into markup view, so all pending
deletions become visible at once — the page-count jump is exactly that text
re-rendered.

The practical consequence: **a page count or PDF taken after a tracked write describes
the marked-up document, not the finished one.** Resolve the changes in Pages, or edit
with `--no-track`, before trusting either.

### Writing character styles

Bold and italic are a run in the character table (field 8), and the weight styles
already exist in the document as property-only archives — `{bold: 1}`, `{italic: 1}`,
`{bold: 1, italic: 1}`. Reusing those by *looking them up by their properties* beats
minting new ones: it keeps the font and size out of it, and a style carrying a typeface
would drag that along with the weight.

Two things to get right:

- The lookup belongs in the reader. Character styles live in `DocumentStylesheet.iwa`,
  which the editor never loads by default — an early attempt scanned the editor's archive
  index and found nothing at all.
- A null entry in the character table is **meaningful** ("no override from here"), unlike
  the paragraph table where it means "inherit". So closing a run means restoring whatever
  applied at its end, null included — carried, not skipped.

### Run-length structures bite three times

Paragraph styles, list styles and change spans are all run-length: an entry says "from
here on", and most paragraphs carry a *null* entry meaning "no change". Every structural
operation therefore affects text it does not touch, and the failure is always silent:

- Inserting a `Heading 2` turned **every following paragraph** into a heading, because
  they inherited rather than stating their own style.
- Deleting a paragraph handed its style to the one after it.
- Importing a bullet list made the next plain paragraph a bullet.

The fix is the same each time: before changing anything, read what the *following*
paragraph resolves to today, and pin it explicitly afterwards. And read it **before**
shifting the table — after the shift, the entry that sat at the insertion point has
moved, and the nearest remaining one is the thing you just inserted. That subtlety cost
a second round of bullet-bleed after the pattern seemed handled.

A related trap in paragraph-boundary detection: a paragraph begins after *any* break
character (`\n`, `\x04`, `\x05`, `\x0e`), not just a newline. Anchoring on `\n` alone put
the style entry one or two characters early, where it parsed fine, wrote fine, and did
nothing at all.

### Guards belong to operations, not to code paths

The locating machinery was shared between `replace` and the structural commands, so
`retag` inherited `replace`'s guard against matching across a tracked deletion — and
refused to retag a heading it was only using as a landmark. Nothing was being rewritten;
the guard was about a span that structural edits never touch. Locating and rewriting
needed separating.

### Comments: where they are, and a lesson about looking

The package contains an `AnnotationAuthorStorage` file, a few hundred bytes holding only
the author's name and an identifier. Reading that alone and finding no comment text is
not evidence a document has no comments — on the test document it had dozens.

That file names authors, not comments. Comments live in the body storage like everything
else — `field 25`, one entry per comment:

```
{1: {1: start, 2: length}, 2: -> 2013} -> 2013{1: -> 3056, 2: uuid}
3056: {1: text, 2: {1: fixed64 date}, 3: -> author(212), 4: -> next reply(3056)}
```

Two details worth keeping:

- They anchor to a **range**, not a point, so the quoted passage comes out with the
  comment — which is most of what makes a comment listing readable.
- `field 4` chains replies, so a thread is a linked list rather than a collection.

Dates are seconds since 2001-01-01 **UTC**; Pages displays local time. Getting this wrong
shifts a comment by a day.

The lesson is not about the format. Finding an empty file whose name resembles the thing
you are looking for is not evidence the thing does not exist — and "the feature is
absent" is a much stronger claim than "I did not find it here." The cheap check skipped
at first was to search the body's own tables for an archive type that could not be
accounted for; that field had been sitting in an earlier probe's own output the whole
time, pointing at a type never identified.

Because comment anchors are a **range**, they also need shifting when body text changes —
and a plain index shifter skips them entirely, because their key is a nested message
rather than a bare varint. Unshifted, a comment silently ends up quoting the wrong words
after a distant edit. The same mis-decode, in an early version of the paragraph-delete
path, made every anchor read as index 8 — the protobuf tag byte — so deleting the
paragraph spanning offset 8 removed every comment in the document at once, silently.

Text-box/margin-note storages key comments differently again: an index-keyed run-length
table (field 23) instead of the body's range-keyed one (field 25), closed by the next
entry rather than by an explicit length. Same comment archives, different table shape —
worth checking for whenever a "simple" field turns out to vary by storage.

The split is not by storage either. In `tests/samples/emoji.pages` (Pages 15.4) the
**body** keys its comments run-length in field 23 and has no field 25. In
`kitchen-sink.pages`, saved by the same version, the body uses field 25. Readers and
writers have to go by which field is present, never by which storage they are in.

### The body flow is not the document

Margin notes holding a meaningful fraction of a document's prose (around 5% on the test
document) live in their own text storages, reached from the body's attachment table
(`field 16`): each entry maps a character index to a type `2008` archive whose `field 2`
references the note's storage. One hop, no searching. Nothing warns you they exist; a
body-only reader just returns less text than the document contains.

Each storage has its **own offset space and its own attribute tables**, so "where is this
text" needs a storage handle as well as an offset, and a note's position in the document
is its *anchor* in the body rather than anything in its own coordinates.

Two bugs came out of writing to them, both of the silent kind:

- **A storage has only the tables it has needed so far.** Note storages can carry an
  insertions table but no deletions table, so a tracked edit inserted the new text and
  then quietly failed to mark the original deleted — leaving merged original-plus-edit
  text behind. Missing change tables have to be created, not assumed.
- **Patching entries into a run-length table can change spans you did not touch.** One
  note's table ended with a dangling ref'd entry, which covers nothing because a span
  ends at the *next* entry. Inserting a new entry after it gave it a terminator and
  turned that empty span into a real one, so rejecting changes dropped text that had
  been accepted. The fix was to stop patching entries and instead rebuild the table from
  its effective spans — then the table means exactly what the span rule says, with no
  leftovers to misread.

The second one is the more general lesson: when a structure is interpreted by a rule,
edit it through that rule rather than through its raw representation. The same rebuild
has a limit worth knowing — a flat list of boundaries cannot express a span *nested*
inside another, so recording a tracked change inside existing tracked text would
truncate the enclosing span and quietly make part of it permanent. That case is refused
rather than approximated.

### Where structure logic belongs

Section and page lookup started out in the editor, which was the wrong home: extracting
a chapter is a *reading* operation, and putting `--in` on the converter would have made
the reader import the editor. Moving `outline`, `section_range` and the page index into
`pages2md.py` — with the editor importing them — keeps the dependency pointing one way
and lets both tools take the same flags for free. The character-style lookup belongs
there for the same reason: those archives live in `DocumentStylesheet.iwa`, which only
the reader loads.

### Finding things: headings are free, page numbers are not

Scoping an edit to a chapter needs nothing new — paragraph styles already give the
heading tree, and a section is simply its heading up to the next heading at the same or
a higher level.

Page numbers are a different matter: **pagination is nowhere in the file.** Pages
computes it at layout time, and the generated TOC stores its page numbers as computed
field placeholders (U+FFFC), not text. The only source of truth is Pages itself, via
`body text of page i of document 1`.

Mapping those pages back onto stored character offsets can't be done by length — Pages
can report a slightly different character count than the storage holds, and normalises
whitespace and control characters along the way. A sequential alignment that compares
only *significant* characters (skipping whitespace, control characters, U+2028 and
U+FFFC on both sides) aligned all but one character out of 83,237 on the test
document.

Two traps worth recording:

- A document's AppleScript `name` **drops the extension** ("report", not "report.pages"),
  so a basename comparison silently never matches. Compare
  `POSIX path of (file of d as alias)` instead. An early guard looked like it worked and
  did nothing.
- Blank pages return an empty string, so slicing their text throws. A page index must
  tolerate empty pages (print layouts often force blanks before chapter openers) rather
  than treat them as an error.

Reopening a document that Pages already has open, then closing it with `saving no`,
discards the user's unsaved work. Any tool that drives Pages for layout has to check
first.

### Batching is a correctness feature, not a speed one

The obvious reason to apply many edits at once is that each write reloads and rezips a
multi-megabyte package. The real reason is that **offsets shift between writes**, so a
sequence of single edits is reasoned about against text that no longer exists by the
second command.

Resolving every edit against one snapshot and applying them right-to-left in a single
pass removes the problem, and makes two things possible that matter more than the speed:

- A dry run that is exactly what will be written, so it can be reviewed before it lands.
- **Overlap detection.** Two edits touching the same characters would corrupt each other
  and the winner would depend on the order they happened to be listed in. Refusing is the
  only safe answer.

Addressing edits by section plus content rather than by offset is what lets a plan
survive the author working elsewhere in the document in the meantime.

### Fingerprint the text, not the file

Pages rewrites the entire package on every save, so the file hash changes when nothing
meaningful did — useless for detecting a real edit. Hashing the body text instead gives a
stable identity, and refusing to write when it has moved closes the gap that the
open-in-Pages guard does not: someone editing and saving *after* a read, whose offsets
are then silently wrong.

Fail fast on a mismatch, **before** resolving the plan. An early version checked after,
which turned a stale plan into a baffling "occurs 10 times but not in the requested
range" instead of "the document changed".

### Internal source control

`config --vcs on` keeps a git repo in `.pages-vcs/` holding a snapshot of the document
itself — deliberately separate from any project's own history, since committing a
multi-megabyte binary on every text tweak would bloat it. Each write snapshots before
and after, the pre-edit state is deduplicated when it already matches `HEAD`, and
`revert` records the revert as a commit so nothing is ever lost. Verified: reverting to a
baseline reproduced the earlier state byte-for-byte.

With the setting off, a plain `.bak` is written instead. Both belong in `.gitignore`.

### Still unproven

Only the body flow is editable — text boxes, headers and footers each live in their own
storage. And indices are shifted only within the edited storage; if another archive
elsewhere holds offsets into the body text, it would not be updated. No evidence of one
was found, but it cannot be ruled out, which is the reason the snapshot happens before
the write and not after.

### An unresolved corruption in batch paragraph deletion

`import --replace-section` swaps a whole section's content in one call —
`clear_range` deletes every paragraph in the range via repeated `delete_paragraph`, then
new content is inserted. It is **not safe** past a handful of paragraphs, and the
investigation did not reach a cause.

Testing against a real 22-paragraph section:

- Deleting up to **9** sequential paragraphs: clean. Verified via re-rendering and a real
  Pages round-trip (open, save, close) — all other headings in the document intact.
- Deleting **10+**: Pages still opens and saves it, but **every heading in the whole
  document loses its style** — not just nearby ones — and the page count roughly
  doubles. Not a local corruption; something propagates document-wide.
- One single-paragraph deletion, tested in isolation (no other deletions in the same
  write), made Pages hang indefinitely on save.

What was ruled out, checked directly against the archive bytes of a file in the broken
state:

- The codec still round-trips byte-for-byte (`pack_archives`/`iwa_encode` both hold).
- No table has a duplicate, unsorted, or out-of-bounds index.
- One table that looked suspicious (an "index"-shaped table with small integer refs,
  nothing like a normal archive id) does in fact shift correctly; its indices move by
  exactly the right delta, confirmed by inspecting entries before and after. It is not
  the cause.

What's unresolved: something about a *batch* of sequential `delete_paragraph` calls — or
the resulting document shape — causes Pages' own save path to discard paragraph styling
globally. Whether this is a specific field never checked, an accumulation effect across
repeated `tokenize`/`emit` passes, or something about how Pages itself revalidates the
document on open is not known.

`import --replace-section` refuses above 3 removed paragraphs as a result. Raising that
limit, or chaining many `delete-paragraph` calls in one write, is not supported until
this is root-caused. A `clear_range` helper exists for a future investigation but should
not be called directly. Issue reports with a reproduction are very welcome.

**A lead, not yet confirmed in Pages.** `drop_entries_in` used to drop every entry
inside the deleted paragraph, including the *terminator* of a run that started before
it. In a run-length table (character styles, tracked changes) the run then continued
until the next entry anywhere in the document -- a character style bleeding over every
later heading would look exactly like the symptom above, and more deletions make it more
likely that one of them removes such a terminator. `delete_paragraph` now carries the
run in force at the end of the deleted span back to its start, and re-states the
following paragraph's list style as well as its paragraph style
(`tests/test_edit_tables.py`). The 3-paragraph limit stays until a real Pages
round-trip confirms this was the cause.

One operational note from chasing this live: repeated open/save/close cycles against
Pages during testing left the application itself sluggish — later AppleEvents timed out
independent of any file corruption, and a `save`/`close` pair that appeared hung had in
fact completed (`modified` was already `false`) when checked separately. Budget real
time for this kind of investigation, and verify a file's bytes and hash directly rather
than only through Pages' responsiveness.

## Prior Art

Worth knowing about, though none of it was needed to build these tools:

- [psobot/keynote-parser](https://github.com/psobot/keynote-parser) — unpacks IWA to
  editable YAML; the most mature reference for the framing.
- [numbers-parser](https://github.com/masaccio/numbers-parser) — actively maintained,
  carries the best reverse-engineered `.proto` set.
- [matchaxnb/pyiwa](https://github.com/matchaxnb/pyiwa) — introspection-based IWA reader.
- [iwork-rs](https://github.com/zebra-pig/iwork-rs) and
  [peterheb/pnk](https://github.com/peterheb/pnk) — Rust/WASM parsers.
- [Reverse Engineering iWork](https://andrews.substack.com/p/reverse-engineering-iwork) —
  notes that Apple ships the protobuf descriptors inside the app binaries, recoverable
  with `proto-dump`. That is the route to exact field names if the wire walk stops being
  enough — and would answer the null-semantics question directly.
