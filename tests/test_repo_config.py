"""The repository's CI workflow and the importable ruleset for `main` must agree.

A required status check that no workflow ever reports blocks every merge, so the ruleset's
check name has to be the CI job's name. GitHub cannot be asked from here, so this pins what
can be checked locally.
"""
import json, os, re, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "tests.yml")
RULESET = os.path.join(ROOT, ".github", "rulesets", "main.json")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Ruleset(unittest.TestCase):
    def setUp(self):
        self.rules = json.loads(read(RULESET))
        self.by_type = {r["type"]: r for r in self.rules["rules"]}

    def test_targets_the_default_branch(self):
        self.assertEqual(self.rules["target"], "branch")
        self.assertEqual(self.rules["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])

    def test_no_force_push_and_no_deletion(self):
        self.assertIn("non_fast_forward", self.by_type)
        self.assertIn("deletion", self.by_type)

    def test_requires_the_ci_check(self):
        params = self.by_type["required_status_checks"]["parameters"]
        contexts = [c["context"] for c in params["required_status_checks"]]
        self.assertEqual(contexts, ["tests"])

    def test_imports_disabled_until_someone_turns_it_on(self):
        self.assertEqual(self.rules["enforcement"], "disabled")


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = read(WORKFLOW)

    def test_the_job_is_named_like_the_required_check(self):
        self.assertRegex(self.text, r"(?m)^  tests:\n    name: tests$")

    def test_runs_on_pull_requests_without_path_filters(self):
        # a path filter would leave the required check "expected" forever on other PRs
        self.assertRegex(self.text, r"(?m)^  pull_request:")
        self.assertNotIn("paths:", self.text)
        self.assertNotIn("paths-ignore:", self.text)

    def test_uses_the_pinned_python_and_runs_the_suite(self):
        self.assertIn("python-version-file: .python-version", self.text)
        self.assertIn("python -m unittest discover -s tests", self.text)

    def test_least_privilege(self):
        self.assertRegex(self.text, r"(?m)^permissions:\n  contents: read$")

    def test_the_pinned_python_matches_the_project(self):
        pinned = read(os.path.join(ROOT, ".python-version")).strip()
        self.assertRegex(read(os.path.join(ROOT, "pyproject.toml")),
                         r'requires-python = ">=%s"' % re.escape(pinned))


if __name__ == "__main__":
    unittest.main()
