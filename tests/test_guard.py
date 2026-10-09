"""The "Pages has it open" guard must not break writing off macOS."""
import os, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pages2md as P


class PagesHasOpen(unittest.TestCase):
    def test_no_osascript_means_nothing_to_conflict_with(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("osascript")):
            self.assertFalse(P.pages_has_open("/tmp/x.pages"))

    def test_pages_not_running(self):
        res = mock.Mock(returncode=0, stdout="NOTRUNNING\n")
        with mock.patch("subprocess.run", return_value=res):
            self.assertFalse(P.pages_has_open("/tmp/x.pages"))

    def test_open_document_is_detected(self):
        path = os.path.realpath(__file__)
        res = mock.Mock(returncode=0, stdout=f"/other.pages|{path}\n")
        with mock.patch("subprocess.run", return_value=res):
            self.assertTrue(P.pages_has_open(path))


if __name__ == "__main__":
    unittest.main()
