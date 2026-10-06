# Test plan

Run everything with `python3 -m unittest discover -s tests` (stdlib only, no Pages
needed). `fixture.py` builds minimal real `.pages` packages, so tests go through the
same zip → IWA → archive → storage path as a real document.

## Covered

| File | What |
| --- | --- |
| `test_codec.py` | varint, protobuf, Snappy, IWA chunking and archive framing round-trips |
| `test_format.py` | review finding 1.5: `format` produces one clean run, `--plain`, new tables, no re-parse |
| `test_edit_tables.py` | review findings 1.1–1.4: runs carried across paragraph deletion, inserted text isolated from preceding runs, empty tracked-change spans, comment ranges clamped |

## Planned

Grouped by the review finding each one pins down. Each entry is a test name, the setup,
and the assertion that fails on today's code. **F** = needs a small fixture extension,
**R** = pure function, easy.

### Fixture extensions needed first

- `write_pages(..., extra=[(id, type, body)])` for additional archives: comment +
  comment-ref + author, attachment → second storage (margin note), character-style
  archives with properties, paragraph-style archives with names.
- A `storage_with_comments(text, [(start, len, text)])` helper that builds the whole
  comment chain, so reader and editor tests share one setup.
- A fake `osascript` on `PATH` (a shell script printing a fixed reply) for the
  Pages-guard and index tests, so they run on Linux CI.

### 1.6 UTF-16 offsets (needs a real Pages sample first)

- `test_samples.py` is written and skips until `tests/samples/emoji.pages` exists
  (recipe in `tests/samples/README.md`). If it reports UTF-16: add index maps
  between UTF-16 units and Python indices at the table boundary, then test reading
  styles and replacing text after an emoji.

### 2 Other bugs

| Test | Setup → assertion |
| --- | --- |
| **F** `test_find_in_caption_with_page_index` | doc with an unanchored storage + a `.pages-index.json` → `cmd_find` prints a match, no `TypeError` |
| **F** `test_locate_comment_in_note_when_body_has_comments` | body comment + note comment → `locate_comment(--on <note text>)` finds the note's |
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
