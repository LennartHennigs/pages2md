# Sample documents

Real files saved by Pages, for the checks in `test_samples.py`. The synthetic fixture
(`fixture.py`) can only test what we already believe about the format; these test the
beliefs themselves.

For every sample:

- Make it in Pages, from the **Blank** template, and save it as a **single file**
  (File ▸ Advanced ▸ File Format: Single File), not a package.
- Copy the real local file, not an iCloud placeholder.
- Use throwaway text only, because the file is committed. Comments carry your Pages
  author name: run `uv run python tests/anonymize_author.py in.pages out.pages "Your Name"
  "Sample Author"` and check `pages2md.py --comments out.pages`. Look for your name in
  the text and in hyperlinks too; the tool does not touch those.
- Write down your Pages version in the commit message.

## `emoji.pages` — offset units (review finding 1.6; result: UTF-16)

Exactly this, in the body. `😀` is one character to Python but two UTF-16 units, so
each checked offset differs by 2 between the two conventions.

1. Paragraph 1 (Body style): `😀😀 Bold word here.` Make only the word **Bold** bold
   with ⌘B.
2. Paragraph 2: `Heading after emoji`, set to the **Heading** paragraph style.
3. Paragraph 3 (Body): `😀😀 a commented word.` Select the word `commented` and add a
   comment (Insert ▸ Comment) with any text.

`uv run python -m unittest tests.test_samples` then says whether each table counts code
points or UTF-16 units.

## `kitchen-sink.pages`

Saved by Pages 15.4. Contains headings 1–3, a bulleted and a numbered list, a footnote
with a comment, a body comment with a reply, one tracked insertion and one tracked
deletion, and a long run of body paragraphs, bullets and headings.

Still wanted, in a later sample: a text box with text and a comment, a figure caption,
a generated table of contents, and bold runs that cross paragraph breaks. For the
batch-deletion test, also a copy edited with `import --replace-section` that has been
opened and saved by Pages.

## `sample-content.pages`

A real document (a German-localised guide, saved by Pages 14.5), not built for testing:
headings 1–2, bold and italic lead-ins, two footnotes (Footnote Text style), two comments
on headings, localised style names ("Text", "Überschrift", "Fußnote"). It found the
footnote paragraph-splitting bug. The comment author's name was replaced with "Sample
Author"; the text and two hyperlinks are unchanged.

## `formatting.pages`

Saved by Pages 15.4: the kitchen-sink document plus a Title ("Titel"), character
formatting (italic, bold italic struck through, bold italic underlined, plain underline),
a hyperlink in the body text (`http://google.de`), and a bulleted list nested three
levels deep (This, then Is and A bulleted one level in, then list two levels in).

## Wanted: `fidelity.pages`

What would let `pages2md.py` cover the rows of README ▸ *Not considered yet*. One document,
throwaway text, English, saved by Pages from the Blank template:

1. A **numbered list nested three levels deep**, then a bullet list nested under a
   numbered item (a bulleted list nested three levels is already in `formatting.pages`).
   Add one numbered list that **starts at 5**, and a second numbered list directly after
   another that **continues** its numbering.
2. A **table** (3 columns × 3 rows, header row, one cell with two lines of text, one
   merged cell).
3. An **image** with a caption (a small PNG), and one image inside a paragraph (inline).
4. A hyperlink on **part of a bold word** (a link on a plain word is in `formatting.pages`).
5. **Inline formatting**: superscript, subscript, a monospace font, a coloured word
   (underline and strikethrough are in `formatting.pages`).
6. **Paragraph styles**: Title, Subtitle, Block Quote, Caption, and one custom style.
7. A **text box** and a **shape** with text, placed on the page.
8. A **header and footer** with a page number.
9. A **section break** and a **page break**, and one **endnote** (Insert ▸ Footnote can be
   switched to endnotes in Document ▸ Footnotes & Endnotes).
10. A line with characters that look like Markdown: `1. not a list`, `# not a heading`,
    `*stars*`, `_under_`, `[brackets]`.

Items 1–4 are the most useful; the rest can come in separate, smaller files.
