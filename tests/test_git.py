"""The history commands on a machine where git is missing, signs every commit, or fails."""
import io, os, shutil, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pages_edit as E

SAMPLE = os.path.join(HERE, "samples", "formatting.pages")


def cli(*args):
    out = io.StringIO()
    try:
        with mock.patch.object(E, "pages_has_open", return_value=False), redirect_stdout(out):
            E.main(list(args))
    except SystemExit as exc:
        return str(exc.code), out.getvalue()
    return None, out.getvalue()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "d.pages")
        shutil.copy(SAMPLE, self.path)


class SigningIsOn(Case):
    """commit.gpgsign=true with a signer that always fails: every plain `git commit` dies."""

    def setUp(self):
        super().setUp()
        cfg = os.path.join(self.tmp.name, "gitconfig")
        with open(cfg, "w") as fh:
            fh.write("[commit]\n\tgpgsign = true\n[tag]\n\tgpgsign = true\n"
                     "[gpg]\n\tprogram = /bin/false\n")
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": cfg, "GIT_CONFIG_SYSTEM": os.devnull})
        env.start()
        self.addCleanup(env.stop)

    def test_the_setup_really_breaks_plain_git(self):
        repo = os.path.join(self.tmp.name, "r")
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.name", "x"], check=True)
        subprocess.run(["git", "-C", repo, "config", "user.email", "x@x"], check=True)
        res = subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", "m"],
                             capture_output=True)
        self.assertNotEqual(res.returncode, 0)

    def test_history_works(self):
        msg, out = cli("config", "--vcs", "on", self.path)
        self.assertIsNone(msg, out)
        msg, out = cli("replace", "-f", "Titel", "-r", "Title", "--write", self.path)
        self.assertIsNone(msg, out)
        _m, history = cli("history", self.path)
        self.assertIn("Baseline", history)

    def test_revert_works(self):
        cli("config", "--vcs", "on", self.path)
        original = open(self.path, "rb").read()
        cli("replace", "-f", "Titel", "-r", "Title", "--write", self.path)
        msg, out = cli("revert", "HEAD~1", self.path)
        self.assertIsNone(msg, out)
        self.assertEqual(open(self.path, "rb").read(), original)


class GitIsMissing(Case):
    def test_a_message_not_a_traceback(self):
        with mock.patch.object(E.subprocess, "run", side_effect=FileNotFoundError("git")):
            msg, _ = cli("config", "--vcs", "on", self.path)
        self.assertIn("git", msg)
        self.assertIn("not installed", msg)

    def test_a_write_with_history_on_refuses_before_changing_anything(self):
        cli("config", "--vcs", "on", self.path)
        before = open(self.path, "rb").read()
        real = subprocess.run

        def no_git(cmd, *a, **kw):
            if cmd and cmd[0] == "git":
                raise FileNotFoundError("git")
            return real(cmd, *a, **kw)

        with mock.patch.object(E.subprocess, "run", side_effect=no_git):
            msg, _ = cli("replace", "-f", "Titel", "-r", "Title", "--write", self.path)
        self.assertIn("not installed", msg)
        self.assertEqual(open(self.path, "rb").read(), before)


class GitFails(Case):
    def test_the_command_that_failed_and_its_error_are_reported(self):
        cli("config", "--vcs", "on", self.path)
        real = subprocess.run

        def broken(cmd, *a, **kw):
            if cmd and cmd[0] == "git" and "commit" in cmd:
                raise subprocess.CalledProcessError(128, cmd, stderr="fatal: disk on fire")
            return real(cmd, *a, **kw)

        with mock.patch.object(E.subprocess, "run", side_effect=broken):
            msg, _ = cli("replace", "-f", "Titel", "-r", "Title", "--write", self.path)
        self.assertIn("git commit", msg)
        self.assertIn("disk on fire", msg)


if __name__ == "__main__":
    unittest.main()
