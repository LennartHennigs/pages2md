# Sample documents

Real files saved by Pages, for the checks in `test_samples.py`. The synthetic fixture
(`fixture.py`) can only test what we already believe about the format; these test the
beliefs themselves.

For every sample:

- Make it in Pages, from the **Blank** template, and save it as a **single file**
  (File ▸ Advanced ▸ File Format: Single File), not a package.
- Copy the real local file, not an iCloud placeholder.
- Use throwaway text only, because the file is committed. Comments carry your Pages
  author name.
- Write down your Pages version in the commit message.

## `emoji.pages` — offset units (review finding 1.6; result: UTF-16)

Exactly this, in the body. `😀` is one character to Python but two UTF-16 units, so
each checked offset differs by 2 between the two conventions.

1. Paragraph 1 (Body style): `😀😀 Bold word here.` Make only the word **Bold** bold
   with ⌘B.
2. Paragraph 2: `Heading after emoji`, set to the **Heading** paragraph style.
3. Paragraph 3 (Body): `😀😀 a commented word.` Select the word `commented` and add a
   comment (Insert ▸ Comment) with any text.

`python3 -m unittest tests.test_samples` then says whether each table counts code
points or UTF-16 units.

## `kitchen-sink.pages`

Saved by Pages 15.4. Contains headings 1–3, a bulleted and a numbered list, a footnote
with a comment, a body comment with a reply, one tracked insertion and one tracked
deletion, and a long run of body paragraphs, bullets and headings.

Still wanted, in a later sample: a text box with text and a comment, a figure caption,
a generated table of contents, and bold runs that cross paragraph breaks. For the
batch-deletion test, also a copy edited with `import --replace-section` that has been
opened and saved by Pages.
