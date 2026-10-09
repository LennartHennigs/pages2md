"""The Claude skills in .claude/skills/ must keep matching the command line.

Every `pages2md.py` / `pages_edit.py` command quoted in a skill is run (as a dry run, on a
copy of a sample) and must not be rejected by argparse: a flag that was renamed or removed
makes the skill teach a command that no longer exists. Pages and `osascript` are never
touched, so `index` is skipped.
"""
import glob, os, re, shlex, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".claude", "skills")
SAMPLE = os.path.join(ROOT, "tests", "samples", "kitchen-sink.pages")
EXPECTED = {"pages-read", "pages-edit"}
MAX_DESCRIPTION = 300          # the description is the only part always in context
SCRIPT = re.compile(r"\b(pages2md|pages_edit)\.py\b")


def skill_files():
    return sorted(glob.glob(os.path.join(SKILLS, "*", "*.md")))


def commands(text):
    """Command lines from fenced code blocks, with `\\` continuations joined."""
    out, in_block, buf = [], False, ""
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_block, buf = not in_block, ""
            continue
        if not in_block:
            continue
        line = line.strip()
        if buf:
            line = buf + " " + line
            buf = ""
        if line.endswith("\\"):
            buf = line[:-1].strip()
            continue
        if SCRIPT.search(line) and not line.startswith("#"):
            out.append(line)
    return out


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    return dict(re.match(r"(\w+):\s*(.*)", l).groups()
                for l in m.group(1).splitlines() if re.match(r"\w+:", l))


class Layout(unittest.TestCase):
    def test_both_skills_exist(self):
        found = {os.path.basename(os.path.dirname(p)) for p in
                 glob.glob(os.path.join(SKILLS, "*", "SKILL.md"))}
        self.assertEqual(found, EXPECTED)

    def test_frontmatter(self):
        for name in sorted(EXPECTED):
            path = os.path.join(SKILLS, name, "SKILL.md")
            with self.subTest(skill=name):
                self.assertTrue(os.path.exists(path), path)
                with open(path, encoding="utf-8") as fh:
                    meta = frontmatter(fh.read())
                self.assertEqual(meta.get("name"), name)
                desc = meta.get("description", "")
                self.assertTrue(desc, "a skill without a description is never triggered")
                self.assertLessEqual(len(desc), MAX_DESCRIPTION)
                self.assertIn(".pages", desc)


class Safety(unittest.TestCase):
    """The read skill must not teach writing; the edit skill must teach the guards."""

    def read(self, rel):
        with open(os.path.join(SKILLS, rel), encoding="utf-8") as fh:
            return " ".join(fh.read().split()).lower()     # unwrapped, case-folded

    def test_read_skill_never_writes(self):
        text = self.read("pages-read/SKILL.md")
        self.assertNotIn("--write", text)
        self.assertNotIn("pages_edit.py replace", text)

    def test_edit_skill_teaches_the_guards(self):
        text = self.read("pages-edit/SKILL.md")
        for needle in ("dry run", "--expect", "fingerprint", "--write",
                       "opened in real pages", "--replace-section"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_no_skill_suggests_skipping_the_backup(self):
        for path in skill_files():
            with open(path, encoding="utf-8") as fh:
                for cmd in commands(fh.read()):
                    with self.subTest(path=path, cmd=cmd):
                        self.assertNotIn("--no-backup", cmd)

    def test_every_write_example_has_a_fingerprint_guard_or_is_not_a_replace(self):
        # `replace --write` without `--expect` is the one write that is easy to aim at
        # a document that moved on, so the skill's own examples must show the guard.
        for path in skill_files():
            with open(path, encoding="utf-8") as fh:
                for cmd in commands(fh.read()):
                    if "--write" in cmd and re.search(r"\b(replace|insert|retag)\b", cmd):
                        with self.subTest(path=path, cmd=cmd):
                            self.assertIn("--expect", cmd)


class Commands(unittest.TestCase):
    """Run every quoted command; argparse exits 2 on an unknown flag or missing argument."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.doc = os.path.join(self.tmp.name, "report.pages")
        shutil.copy(SAMPLE, self.doc)

    def argv(self, cmd):
        words = shlex.split(cmd, comments=True)
        while words and not SCRIPT.search(words[0]):
            words.pop(0)                       # drop `uv run python`, `python3`
        words[0] = os.path.join(ROOT, os.path.basename(words[0]))
        words = [w for w in words if w != "--write"]      # dry runs only
        return [sys.executable] + [self.doc if w.endswith(".pages") else w for w in words]

    def test_quoted_commands_are_accepted_by_the_parsers(self):
        seen = 0
        for path in skill_files():
            with open(path, encoding="utf-8") as fh:
                cmds = commands(fh.read())
            for cmd in cmds:
                if re.search(r"\bindex\b", cmd):            # would call osascript
                    continue
                seen += 1
                with self.subTest(path=os.path.relpath(path, ROOT), cmd=cmd):
                    res = subprocess.run(self.argv(cmd), capture_output=True, text=True,
                                         cwd=self.tmp.name)
                    self.assertNotEqual(res.returncode, 2, res.stderr)
        self.assertGreater(seen, 10, "the skills should quote real commands")


if __name__ == "__main__":
    unittest.main()
