# Test plan

Run everything with `uv run python -m unittest discover -s tests` (stdlib only, no Pages
needed). `fixture.py` builds minimal real `.pages` packages, so tests go through the
same zip → IWA → archive → storage path as a real document.

## Covered

| File | What |
| --- | --- |
| `test_codec.py` | varint, protobuf, Snappy, IWA chunking and archive framing round-trips |
| `test_format.py` | review finding 1.5: `format` produces one clean run, `--plain`, new tables, no re-parse |
| `test_edit_tables.py` | review findings 1.1–1.4: runs carried across paragraph deletion, inserted text isolated from preceding runs, empty tracked-change spans, comment ranges clamped; minimal direct replacements |
| `test_footnotes.py` | footnote references (`\x0e` + attachment entry) are inline: reader paragraphs, editor bounds/starts/delete/retag, kitchen-sink sentence |
| `test_samples.py` (`RealGuide`) | the real 14.5 guide: footnoted sentences whole, localised styles, lead-in emphasis, comments, edit/insert/format/delete round trips on a copy |
| `test_footnotes.py` (`FootnotesAsMarkdown`) | `[^n]` references and definitions: order, multi-paragraph, skip/only, tracked deletion, JSON, plain |
| `test_markdown_fidelity.py` | hyperlinks (offsets, emoji, deletion, emphasis nesting, URL quoting), tight, numbered and nested lists, restarts, strikethrough, escaping table |
| `test_edit_tables.py` (`DeleteParagraphKeepsListLevels`) | list levels survive paragraph deletion; insertion is a sibling |
| `test_roundtrip_harness.py` | the Pages round-trip harness against a stand-in Pages: all cases prepared and readable, identity passes, a lost edit / lost headings / missing file / timeout / growing page count are caught |
| `test_table_integrity.py` | tables stay sorted, one entry per index and inside the text after any edit (collapsed runs, tracked changes, comments, paragraph styles); the reader tolerates duplicates; `save` refuses ill-formed output; `verify` reads with the reader; seeded random replacements on every sample |
| `test_search_text.py` | an empty search text is refused by every command that takes one, nothing is written; plan entries must hold strings; `^`, `$`, `\\b` still work |
| `test_insert_boundaries.py` | inserting before or after a paragraph led or followed by `\\x04`/`\\x05`/`\\x0e` adds exactly one paragraph (unit cases, and `insert`/`import --before` on the real guide); a footnote reference is not a boundary |
| `test_delete_boundaries.py` | deleting the last paragraph or one ended by a break character takes the previous newline; a paragraph with a footnote reference is refused (API, command, dry run) |
| `test_robustness.py` | cyclic comment chains, zero-width match at the end with tracked deletions, comment over a line break, style names (`Body` → `Body 1`), `--where` rejected on structural commands, `--links`, messages instead of tracebacks (bad packages, regexes, input files) |
| `test_commands.py` | `--in` sections (ranges, ambiguity, substring), the page index (built with a faked `osascript`, cached, stale, unreadable), `--page` for reading, edits and plans, plans (dry run, write, scope, fingerprint, overlap, bad input), config, history, revert, `fingerprint`/`--expect` |
| `test_git.py` | history with `commit.gpgsign=true` and a failing signer, with git missing, and with a failing git command |
| `test_shift_fast.py` | the byte-level `shift_table` and the one-pass `apply` give the same bytes and report as the general paths on synthetic tables and random edits on every sample; overlapping edits fall back; a package's media is read only on save and a changed file is refused |
| `test_write_safety.py` | undecodable output refused by `save` and reported by `verify`; changed-on-disk checked for IWA-only packages and before any snapshot; a malformed entry goes to the general path; tracked edits inside a pending insertion or deletion refused |
| `test_guard.py` | the Pages-open guard does not crash without `osascript` |
| `test_samples.py` | real Pages 15.4 files: 1.6 settled (tables count UTF-16 units); reading and editing after emoji; kitchen-sink smoke tests (codec, every format, headings, lists, tracked changes, comments in body and margin note, fingerprints) |

## Planned

Grouped by the review finding each one pins down. Each entry is a test name, the setup,
and the assertion that fails on today's code. **F** = needs a small fixture extension,
**R** = pure function, easy.

### Fixture extensions needed first

- `write_pages(..., extra=[(id, type, body)])` exists (used for character styles);
  still needed: comment + comment-ref + author, attachment → second storage (margin
  note), paragraph-style archives with names.
- A `storage_with_comments(text, [(start, len, text)])` helper that builds the whole
  comment chain, so reader and editor tests share one setup.
- A fake `osascript` on `PATH` (a shell script printing a fixed reply) for the
  Pages-guard and index tests, so they run on Linux CI.

### Not fixed yet (found by the same random edits)

- Deleting the last paragraph, or one directly followed by a break character, leaves an empty
  paragraph behind: it has no newline of its own to remove, so the one *before* it should go.
- The empty paragraph at the end of the text inherits the style or list level of the one before it
  (retag, insert at the end, delete the last paragraph); no entry is written at the very end.
- Deleting a paragraph that holds a footnote reference leaves the footnote's storage behind.

### Waiting for a real Pages

Run `uv run python tests/pages_roundtrip.py all DIR` on a Mac and read `report.md`. Until that
has been done, nothing the editor writes is known to be accepted by Pages. The first run
also tests the harness: the AppleScript (`open`, `save ... in`, `close`) has only been
exercised with a stand-in, and the controls may show normalisations by Pages that the
comparison needs to allow for.

### Waiting for `tests/samples/fidelity.pages`

See the wish list in `tests/samples/README.md`. Each row becomes tests once the file is
there: start-at and continued numbering, numbered-under-bullet nesting in a real file,
tables, images and captions, code spans and superscript, block quotes and other paragraph
styles, text boxes, headers and footers, endnotes. Also: carrying list *restart* flags
(field 14) across paragraph deletion, once Pages' behaviour is known.

### Found in the samples

| Test | Setup → assertion |
| --- | --- |
| `test_footnote_handles_keep_working` | the CLI still calls footnote storages `note1`… in `find`, `--where notes`, `-t storages`; decide later whether to add a `footnote1` alias |

**S** = runs against a sample in `tests/samples/`.

### 2 Other bugs

| Test | Setup → assertion |
| --- | --- |
| **F** `test_find_in_caption_with_page_index` | doc with an unanchored storage + a `.pages-index.json` → `cmd_find` prints a match, no `TypeError` |
| **S** `test_locate_comment_in_note_when_body_has_comments` | kitchen-sink has exactly this: body comment + note comment → `locate_comment(--on <note text>)` finds the note's |
| **F** `test_scope_excludes_unanchored_storages` | `--in` section → matches in captions are excluded; `--comments --in` same |
| **R** `test_zero_width_match_at_end_maps_to_raw_length` | raw with a tracked deletion; regex `$\Z` → raw offset `len(raw)` |
| **F** `test_pages_has_open_without_osascript` | `PATH` without osascript → returns False (or a clear message), no `FileNotFoundError` |
| **F** `test_page_script_path_quoting` | fake osascript records argv → a path with `"` and `\` arrives intact |
| **R** `test_fingerprints_agree` | doc with body + note + caption → `pages_edit.fingerprint == pages2md.text_fingerprint` |
| **R** `test_emphasize_bold_italic` | run `(0,5,True,True)` → `***hello***` |
| **R** `test_emphasize_merges_adjacent_runs` | two adjacent bold runs → `**foobar**` |
| **F** `test_marked_deletion_keeps_emphasis` | `--changes mark` paragraph with a bold run and a struck span keeps `**` |
| **R** `test_strip_markup_keeps_intraword_underscores` | `my_var_name` unchanged, no runs |
| **F** `test_import_without_none_list_style` | doc without a "None" list style → plain paragraph after bullets has no bullet |
| **F** `test_replace_refuses_newline` | `--regex 'a\s+b'` across a paragraph break → refused |
| **F** `test_reply_keeps_other_dependencies` | archive with two MessageInfos → reply changes only the edited one's field 5 |
| **F** `test_new_change_is_a_dependency` | tracked edit → storage MessageInfo field 5 lists both new change ids |
| **F** `test_revert_is_atomic` | patch `os.replace` to raise → original file unchanged |
| **F** `test_multi_message_archive_agrees` | archive with [style, storage] messages → reader and editor pick the same storage |

### 3 Robustness

- **R** `test_tokenize_rejects_truncated` — truncated length-delimited field raises
  `ValueError`, not a short slice.
- **R** `test_snappy_rejects_bad_offset` — copy with offset 0 or past the output → `ValueError`.
- **F** `test_directory_package_error` — a `.pages` directory → a clear CLI error, not a traceback.
- **F** `test_body_is_not_largest_storage` — body shorter than a text box → body found
  via the document archive (once implemented).
- **F** `test_corrupt_config` — invalid `.pages-edit.json` → clear error.

### 4 Refactors (guard rails, written before refactoring)

- **F** `test_single_parse_per_command` — count `PagesDoc` constructions in `replace`,
  `plan` (3 entries with `in`), `import`; assert 1 each (plus 1 for verify).
- **F** golden output: render the fixture doc with every `-t` format and compare against
  checked-in snapshots, so moving code between modules cannot change output silently.
- **F** CLI smoke tests via `main([...])` for every subcommand in dry-run mode.

### Batch paragraph deletion (known issue)

- **F** `test_clear_range_preserves_every_table` — 22 paragraphs with heading styles,
  bullets, bold runs crossing paragraph boundaries and a tracked change; clear 12 of
  them; assert each remaining paragraph's style, list, char runs and change spans match
  a reference computed from the text alone. If 1.1 was the cause, this plus a Pages
  round-trip on a real sample is the evidence needed to lift the 3-paragraph limit.
