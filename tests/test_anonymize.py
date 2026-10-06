"""tests/anonymize_author.py changes the name and nothing else."""
import io, os, sys, tempfile, unittest, zipfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import anonymize_author as A
import iwa_codec as C
import pages2md as P

SAMPLE = os.path.join(HERE, "samples", "formatting.pages")


@unittest.skipUnless(os.path.exists(SAMPLE), "formatting sample not present")
class Anonymize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out.pages")

    def test_author_changes_and_the_text_does_not(self):
        n = A.replace_string(SAMPLE, self.out, "Sample Author", "A Much Longer Author Name")
        self.assertEqual(n, 1)
        authors = {m["author"] for c in P.PagesDoc(self.out).comments() for m in c["thread"]}
        self.assertEqual(authors, {"A Much Longer Author Name"})
        self.assertEqual(P.render_markdown(None, P.PagesDoc(self.out).all_paragraphs()),
                         P.render_markdown(None, P.PagesDoc(SAMPLE).all_paragraphs()))

    def test_every_other_file_is_unchanged_and_the_codec_still_round_trips(self):
        A.replace_string(SAMPLE, self.out, "Sample Author", "X")
        with zipfile.ZipFile(SAMPLE) as a, zipfile.ZipFile(self.out) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for name in a.namelist():
                if "AnnotationAuthorStorage" not in name:
                    self.assertEqual(a.read(name), b.read(name), name)
            for name in (n for n in b.namelist() if n.endswith(".iwa")):
                payload = C.iwa_decode(b.read(name))
                self.assertEqual(C.pack_archives(C.archives(payload)), payload)

    def test_a_name_that_is_not_there_changes_nothing(self):
        self.assertEqual(A.replace_string(SAMPLE, self.out, "Nobody At All", "X"), 0)
        with zipfile.ZipFile(SAMPLE) as a, zipfile.ZipFile(self.out) as b:
            self.assertTrue(all(a.read(n) == b.read(n) for n in a.namelist()))

    def test_command_line(self):
        out = io.StringIO()
        with redirect_stdout(out):
            A.main([SAMPLE, self.out, "Sample Author", "Y"])
        self.assertEqual(out.getvalue(), "replaced 1\n")


if __name__ == "__main__":
    unittest.main()
