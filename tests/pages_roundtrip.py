#!/usr/bin/env python3
"""Does Pages accept what pages_edit writes?  (macOS, Pages installed.)

The unit tests check what the tools read back from their own output. They cannot
show that Pages opens a written file and keeps everything in it. This does:

    uv run python tests/pages_roundtrip.py all  OUTDIR     # prepare, run Pages, check, report
    uv run python tests/pages_roundtrip.py prepare OUTDIR  # only write the edited copies
    uv run python tests/pages_roundtrip.py run  OUTDIR     # drive Pages over a prepared pack
    uv run python tests/pages_roundtrip.py check OUTDIR    # compare, write OUTDIR/report.md

For each case it copies a real sample (tests/samples/), applies one write from
pages_edit to the copy, has Pages open that copy and *save it as a new file* (so
Pages' own serializer runs), and reads the saved file back with pages2md. The two
renderings -- Markdown with changes accepted and marked, headings, paragraph-style
census, comments -- must match. Page and character counts come from Pages itself:
a case that only deletes must not grow in pages (the documented corruption doubles
the page count and strips every heading's style).

Each sample has a *control*, saved without any edit, so that anything Pages itself
normalises shows up there and not as a false alarm in an edited case.

Without macOS or without Pages run `prepare`, open each file in OUTDIR/edited/ in
Pages, choose File > Save As, save it into OUTDIR/saved/ under the same name, then
run `check`. Paste report.md back.

NOT YET RUN AGAINST A REAL PAGES: the AppleScript below follows Pages' scripting
dictionary but has only been exercised with a stand-in. If `run` reports a script
error, use the manual route and say what the error was.
"""
import collections, difflib, io, json, os, shutil, subprocess, sys, tempfile, time
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pages2md as P
import pages_edit as E

SAMPLES = os.path.join(HERE, "samples")
SCRIPT = '''on run argv
  set inPath to item 1 of argv
  set outPath to item 2 of argv
  with timeout of %d seconds
    tell application "Pages"
      set d to open POSIX file inPath
      try
        set pc to count of pages of d
        set cc to count of characters of body text of d
        save d in POSIX file outPath
        close d saving no
      on error e
        try
          close d saving no
        end try
        error e
      end try
      return (pc as text) & " " & (cc as text)
    end tell
  end timeout
end run'''


# ------------------------------------------------------------------ cases
def cli(path, *args):
    """Run pages_edit exactly as a user would, writing to `path`."""
    with mock.patch.object(E, "pages_has_open", return_value=False), \
            redirect_stdout(io.StringIO()):
        E.main([*args, "--write", "--no-backup", path])


def delete_run(path, first_text, count):
    """Delete `count` consecutive paragraphs from the one containing `first_text`,
    in a single write -- the scenario behind the documented corruption."""
    doc = E.Document(path)
    raw = doc.text()[0]
    starts = doc.paragraph_starts(0, len(raw))
    first = doc.paragraph_bounds(raw.index(first_text))[0]
    i = starts.index(first)
    if i + count > len(starts):
        raise ValueError(f"only {len(starts) - i} paragraphs from {first_text!r}")
    removed = doc.clear_range(starts[i], starts[i + count - 1] + 1)
    assert removed == count, (removed, count)
    doc.save(path)


def delete_nested_item(path):
    """Delete the level-1 item "Is" of the nested list, last occurrence."""
    doc = E.Document(path)
    doc.delete_paragraph(doc.text()[0].rindex("Is \nA bulleted\nlist"))
    doc.save(path)


def import_markdown(path):
    md = os.path.join(os.path.dirname(path), "import-" + os.path.basename(path) + ".md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("## Added section\n\nA paragraph with **bold** and *italic*.\n\n"
                 "- first bullet\n- second bullet\n")
    cli(path, "import", md, "--after", "get started")
    os.remove(md)


def import_before_heading(path):
    md = os.path.join(os.path.dirname(path), "import-" + os.path.basename(path) + ".md")
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("## Imported heading\n\nOne paragraph.\n")
    cli(path, "import", md, "--before", "What It Is")
    os.remove(md)


F, K, S, EM = "formatting", "kitchen-sink", "sample-content", "emoji"
Case = collections.namedtuple("Case", "name sample kind mutate what look")
CASES = [
    Case("replace-plain", F, "edit", lambda p: cli(p, "replace", "-f", "dolor", "-r", "dolorem"),
         "replace one word in italic text", "the word changed, still italic"),
    Case("replace-tracked", F, "edit", lambda p: cli(p, "replace", "-f", "ipsum", "-r", "ipse", "--track"),
         "replace as a native tracked change",
         "a tracked change in the review pane: 'ipsum' deleted, 'ipse' inserted"),
    Case("replace-all-with-footnote", S, "edit",
         lambda p: cli(p, "replace", "-f", "Design Thinking", "-r", "DT", "--all"),
         "replace in the body and in a footnote at once", "headings and footnote text read 'DT'"),
    Case("footnote-edit", S, "edit",
         lambda p: cli(p, "replace", "-f", "Because Design Thinking", "-r", "Since Design Thinking", "--where", "notes"),
         "edit text inside a footnote", "footnote 2 begins 'Since'"),
    Case("emoji-edit", EM, "edit", lambda p: cli(p, "replace", "-f", "😀😀 Bold", "-r", "🎉 Bold"),
         "replace text next to emoji (UTF-16 offsets)", "'Bold' still bold, comment still on 'Commented'"),
    Case("insert-after-bold", S, "edit",
         lambda p: cli(p, "insert", "--after", "Scoping a request", "--text", "Inserted by the round-trip test.", "--style", "Body 1"),
         "insert a paragraph after bold text", "the new paragraph is plain, not bold"),
    Case("import-markdown", S, "edit", import_markdown,
         "import a heading, emphasis and bullets", "a Heading 2, a bold/italic line, two bullets"),
    Case("insert-before-anchored-heading", S, "edit",
         lambda p: cli(p, "insert", "--before", "What It Is", "--text", "Inserted before the heading.", "--style", "Body 1"),
         "insert before a heading that an object anchor leads",
         "one new paragraph before 'What It Is', no empty paragraph, the heading still a Heading 2"),
    Case("import-before-anchored-heading", S, "edit", import_before_heading,
         "import before a heading that an object anchor leads", "two new paragraphs, no empty one"),
    Case("retag", S, "edit", lambda p: cli(p, "retag", "--on", "What It Is", "--style", "Heading 3"),
         "change a heading's style", "'What It Is' is a Heading 3"),
    Case("format-bold", F, "edit", lambda p: cli(p, "format", "--on", "Nemo enim", "--bold"),
         "make text bold", "'Nemo enim' bold"),
    Case("format-plain", F, "edit",
         lambda p: cli(p, "format", "--on", "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed", "--plain"),
         "clear all character formatting over a stretch of text",
         "'Lorem ... sed' has no italic, bold, strikethrough or underline left"),
    Case("comment-add", K, "edit",
         lambda p: cli(p, "comment", "add", "--on", "Lorem", "--text", "Added by the round-trip test."),
         "add a review comment", "a comment on 'Lorem'"),
    Case("comment-reply", K, "edit",
         lambda p: cli(p, "comment", "reply", "--on", "Body", "--text", "A reply."),
         "reply in a thread", "the thread on 'Body' has a third message"),
    Case("comment-delete-in-footnote", K, "edit",
         lambda p: cli(p, "comment", "delete", "--on", "about"),
         "delete a comment that lives in a footnote", "the footnote comment is gone, the body one is not"),
    Case("delete-one", S, "delete", lambda p: cli(p, "delete-paragraph", "--on", "I hope this guide"),
         "delete one paragraph", "one paragraph fewer, nothing else moved"),
    Case("delete-nested-item", F, "delete", delete_nested_item,
         "delete a nested list item", "'A bulleted' is still indented one level, 'list' two"),
    Case("delete-3-guide", S, "delete", lambda p: delete_run(p, "Planning a session", 3),
         "delete three consecutive paragraphs (the supported limit)", "three paragraphs fewer"),
    Case("delete-9-formatting", F, "delete", lambda p: delete_run(p, "Headline", 9),
         "delete nine consecutive paragraphs (documented as safe)", "headings keep their style"),
    Case("delete-12-formatting", F, "delete", lambda p: delete_run(p, "Headline", 12),
         "delete twelve consecutive paragraphs: the documented corruption",
         "headings keep their style; the page count does not roughly double"),
]


def control_name(sample):
    return "control" if sample == F else "control-" + sample


def all_cases():
    out = [Case(control_name(s), s, "control", lambda p: None, "no edit, only Pages' own save",
                "nothing differs from the original") for s in (F, K, S, EM)]
    return out + CASES


# ------------------------------------------------------------- snapshots
_SNAPSHOTS = {}


def snapshot(path):
    """What must survive a round trip, as plain comparable values (cached per file)."""
    st = os.stat(path)
    key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    if key not in _SNAPSHOTS:
        _SNAPSHOTS[key] = _snapshot(path)
    return _SNAPSHOTS[key]


def _snapshot(path):
    doc = P.PagesDoc(path)
    paras = doc.all_paragraphs()
    return {
        "markdown": P.render_markdown(doc, paras),
        "markdown with changes marked": P.render_markdown(doc, doc.all_paragraphs("mark")),
        "headings": [(lvl, text) for lvl, text, _off in P.outline(path)],
        "paragraph styles": sorted(collections.Counter(
            p["semantic"] for p in paras if p["raw"].strip()).items()),
        "comments": [(c["handle"], c["quote"],
                      tuple(m["text"].strip() for m in c["thread"]))
                     for c in doc.comments()],
    }


def describe_difference(key, was, now):
    if isinstance(was, str):
        diff = [l for l in difflib.unified_diff(was.splitlines(), now.splitlines(),
                                                "edited", "after Pages", n=0, lineterm="")
                if not l.startswith(("---", "+++", "@@"))]
        return f"{key} differs: " + " | ".join(diff[:6]) + (" ..." if len(diff) > 6 else "")
    return f"{key} differ: {str(was)[:160]} -> {str(now)[:160]}"


# ----------------------------------------------------------------- stages
def prepare(outdir, only=None):
    """Write input/ and edited/ for each case; return the case names."""
    cases = [c for c in all_cases() if only is None or c.name in only]
    for sub in ("input", "edited", "saved"):
        shutil.rmtree(os.path.join(outdir, sub), ignore_errors=True)
        os.makedirs(os.path.join(outdir, sub))
    manifest = {}
    for c in cases:
        src = os.path.join(SAMPLES, c.sample + ".pages")
        inp = os.path.join(outdir, "input", c.name + ".pages")
        edited = os.path.join(outdir, "edited", c.name + ".pages")
        shutil.copy(src, inp)
        shutil.copy(src, edited)
        c.mutate(edited)
        manifest[c.name] = dict(sample=c.sample, kind=c.kind, what=c.what, look=c.look,
                                control=control_name(c.sample))
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    return [c.name for c in cases]


def load_manifest(outdir):
    with open(os.path.join(outdir, "manifest.json"), encoding="utf-8") as fh:
        return json.load(fh)


def load_run(outdir):
    try:
        with open(os.path.join(outdir, "run.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def save_run(outdir, run):
    with open(os.path.join(outdir, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=2)


def load_counts(outdir):
    return {n: {"pages": r["pages"], "chars": r["chars"]}
            for n, r in load_run(outdir).items() if r.get("status") == "ok"}


def save_counts(outdir, counts):
    save_run(outdir, {n: dict(status="ok", **c) for n, c in counts.items()})


def run(outdir, timeout=300, pause=2, only=None):
    """Have Pages open and save-as every edited copy, one at a time."""
    manifest = load_manifest(outdir)
    results = load_run(outdir) if only else {}
    saved = os.path.join(outdir, "saved")
    os.makedirs(saved, exist_ok=True)
    for name in manifest:
        if only and name not in only:
            continue
        src = os.path.abspath(os.path.join(outdir, "edited", name + ".pages"))
        dst = os.path.abspath(os.path.join(saved, name + ".pages"))
        if os.path.exists(dst):
            os.remove(dst)
        print(f"  {name} ...", end=" ", flush=True)
        try:
            res = subprocess.run(["osascript", "-e", SCRIPT % timeout, src, dst],
                                 capture_output=True, text=True, timeout=timeout + 30)
            if res.returncode:
                results[name] = dict(status="error", detail=res.stderr.strip()[:400])
            else:
                pages, chars = (int(x) for x in res.stdout.split())
                results[name] = dict(status="ok", pages=pages, chars=chars)
        except subprocess.TimeoutExpired:
            results[name] = dict(status="timeout", detail=f"no answer in {timeout}s")
        except (ValueError, OSError) as exc:
            results[name] = dict(status="error", detail=str(exc)[:400])
        print(results[name]["status"])
        save_run(outdir, results)
        time.sleep(pause)           # repeated open/save/close cycles make Pages sluggish
    return results


Result = collections.namedtuple("Result", "name ok problems notes")


def check(outdir):
    """Compare each saved file with its edited copy (a control: with the original)."""
    manifest = load_manifest(outdir)
    runlog, counts = load_run(outdir), load_counts(outdir)
    results = []
    for name, info in manifest.items():
        problems, notes = [], []
        saved = os.path.join(outdir, "saved", name + ".pages")
        status = runlog.get(name, {})
        if status.get("status") == "timeout":
            problems.append("Pages timed out: " + status.get("detail", ""))
        elif status.get("status") == "error":
            problems.append("Pages/osascript failed: " + status.get("detail", ""))
        if not os.path.exists(saved):
            if not problems:
                problems.append("not saved by Pages (no file in saved/)")
            results.append(Result(name, False, problems, notes))
            continue
        try:
            want = snapshot(os.path.join(outdir, "input" if info["kind"] == "control"
                                         else "edited", name + ".pages"))
            got = snapshot(saved)
        except Exception as exc:                  # Pages wrote something we cannot read
            results.append(Result(name, False, [f"cannot read the saved file: {exc!r}"], notes))
            continue
        problems += [describe_difference(k, want[k], got[k]) for k in want if want[k] != got[k]]
        if info["kind"] != "control":
            # what we wrote must itself be well-formed, whatever the renderings say
            problems += [f"our write has ill-formed tables: {p}" for p in
                         E.table_problems(E.Document(os.path.join(outdir, "edited", name + ".pages")))]
        irregular = E.table_problems(E.Document(saved))
        if irregular:
            notes.append(f"Pages' own file has {len(irregular)} table irregularit"
                         f"{'y' if len(irregular) == 1 else 'ies'}: {irregular[0][:80]}")
        mine, ctrl = counts.get(name), counts.get(info["control"])
        if mine:
            notes.append(f"{mine['pages']} pages, {mine['chars']} chars")
        if mine and ctrl and info["kind"] != "control":
            if info["kind"] == "delete" and mine["pages"] > ctrl["pages"]:
                problems.append(f"pages grew from {ctrl['pages']} to {mine['pages']} "
                                "although only paragraphs were deleted")
            elif info["kind"] == "edit" and abs(mine["pages"] - ctrl["pages"]) > 1:
                problems.append(f"pages {mine['pages']} vs {ctrl['pages']} in its control")
        results.append(Result(name, not problems, problems, notes))
    return results


def write_report(outdir, results):
    manifest = load_manifest(outdir)
    lines = ["# Pages round-trip report", "",
             f"{sum(r.ok for r in results)} of {len(results)} cases passed.", "",
             "| case | result | what it does | detail |", "| --- | --- | --- | --- |"]
    for r in results:
        detail = "; ".join(r.problems or r.notes).replace("|", "\\|")
        lines.append(f"| {r.name} | {'pass' if r.ok else 'FAIL'} | "
                     f"{manifest[r.name]['what']} | {detail} |")
    lines += ["", "## If something failed", "",
              "Paste this file back, and say which Pages version made the files.",
              "For a FAIL, the files are in `edited/` (what we wrote), `saved/` (what Pages",
              "wrote back) and `input/` (the sample). Open `edited/<case>.pages` in Pages and",
              "look at: "]
    lines += [f"- **{n}**: {m['look']}" for n, m in manifest.items() if m["kind"] != "control"]
    with open(os.path.join(outdir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return "\n".join(lines)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ("all", "prepare", "run", "check"):
        sys.exit(__doc__)
    stage, outdir, only = args[0], os.path.abspath(args[1]), None
    if len(args) > 2:
        only = args[2].split(",")
    if stage in ("all", "prepare"):
        names = prepare(outdir, only)
        print(f"prepared {len(names)} cases in {outdir}")
    if stage in ("all", "run"):
        if sys.platform != "darwin":
            sys.exit("`run` needs macOS with Pages. Use the manual route: open each "
                     f"file in {outdir}/edited/ in Pages, File > Save As into "
                     f"{outdir}/saved/ under the same name, then `check`.")
        print("asking Pages (one document at a time) ...")
        run(outdir, only=only)
    if stage in ("all", "check"):
        results = check(outdir)
        print(write_report(outdir, results))
        sys.exit(0 if all(r.ok for r in results) else 1)


if __name__ == "__main__":
    main()
