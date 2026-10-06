"""Tests for verification across the history.

This is where the reason for the tool lives: that removing something from a
file does not remove it. Checked against an actual git repository.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from publish_guard.verify import verify

# A non-ASCII term and the percent-encoded form of it, both written out by
# hand. Computing the second one with urllib.parse.quote would be asking the
# implementation to agree with itself; a literal is what makes this a test.
NON_ASCII_TERM = "取引先"
NON_ASCII_PERCENT = "%E5%8F%96%E5%BC%95%E5%85%88"


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


class RepoFixture:
    """A git repository to test against."""

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="publish-guard-test-"))
        git(self.path, "init", "-q", "-b", "main")
        git(self.path, "config", "user.name", "Test")
        git(self.path, "config", "user.email", "test@example.com")

    def write(self, name: str, text: str) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with io.open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def commit(self, message: str) -> None:
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", message)

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


class HistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoFixture()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_removed_from_the_file_but_found_in_the_history(self):
        # The middle of this tool. Committing the edit does not remove it.
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("initial")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("swap the organisation name out")

        report = verify(self.repo.path, ["AcmeCorp"])
        self.assertFalse(report.ok)
        self.assertTrue(any(f.matched == "AcmeCorp" for f in report.findings))

    def test_not_reading_the_history_misses_it(self):
        # What --no-history does, which is the argument for the tool read
        # backwards: HEAD's tree alone makes it look gone.
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("initial")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("swap the organisation name out")

        self.assertTrue(verify(self.repo.path, ["AcmeCorp"], history=False).ok)
        self.assertFalse(verify(self.repo.path, ["AcmeCorp"], history=True).ok)

    def test_scanning_one_branch_still_walks_its_ancestors(self):
        # all_refs=False means "do not read the other refs", not "do not read
        # the history". The two are easy to confuse, so it is stated here.
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("initial")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("swap the organisation name out")

        self.assertFalse(verify(self.repo.path, ["AcmeCorp"], all_refs=False).ok)

    def test_what_was_never_there_is_not_reported(self):
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("initial")
        self.assertTrue(verify(self.repo.path, ["AcmeCorp"]).ok)

    def test_history_only_is_distinguished_from_still_present(self):
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("initial")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("swap the organisation name out")

        report = verify(self.repo.path, ["AcmeCorp"])
        blob_findings = [f for f in report.findings if f.location == "config.json"]
        self.assertTrue(blob_findings)
        # config.json is still there, so this one is not history-only.
        self.assertFalse(blob_findings[0].in_history_only)

    def test_a_deleted_file_is_marked_history_only(self):
        self.repo.write("secret-notes.md", "notes on the AcmeCorp setup")
        self.repo.commit("add the notes")
        (self.repo.path / "secret-notes.md").unlink()
        self.repo.commit("remove the notes")

        report = verify(self.repo.path, ["AcmeCorp"])
        self.assertFalse(report.ok)
        self.assertTrue(all(f.in_history_only for f in report.findings))


class VariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoFixture()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_the_percent_encoded_form_is_found(self):
        # The plain string was removed and it is still inside a URL.
        self.repo.write("test.js", f'expect(url).toMatch(/{NON_ASCII_PERCENT}/)')
        self.repo.commit("add a test")

        report = verify(self.repo.path, [NON_ASCII_TERM])
        self.assertFalse(report.ok)
        # Reported under the original term. The encoded form alone tells a
        # person nothing.
        self.assertEqual(report.findings[0].term, NON_ASCII_TERM)

    def test_an_upper_case_variant_is_found(self):
        self.repo.write("run.sh", 'VERIFIED_ACMECORP_WINDOW_ID=1')
        self.repo.commit("add a script")
        self.assertFalse(verify(self.repo.path, ["acmecorp"]).ok)

    def test_a_regex_escaped_form_is_found(self):
        self.repo.write("test.js", 'assert.match(s, /automation\\.once\\.json/)')
        self.repo.commit("add a test")
        self.assertFalse(verify(self.repo.path, ["automation.once.json"]).ok)


class MetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoFixture()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_a_commit_message_is_searched_too(self):
        # The files can be spotless while the message still says it.
        self.repo.write("readme.md", "clean")
        self.repo.commit("add the configuration for AcmeCorp")

        report = verify(self.repo.path, ["AcmeCorp"])
        self.assertFalse(report.ok)
        self.assertTrue(
            any(f.location == "commit metadata" for f in report.findings)
        )

    def test_the_metadata_check_can_be_turned_off(self):
        self.repo.write("readme.md", "clean")
        self.repo.commit("add the configuration for AcmeCorp")
        self.assertTrue(verify(self.repo.path, ["AcmeCorp"], check_metadata=False).ok)


class EmptyInputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoFixture()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_no_forbidden_terms_is_always_ok(self):
        self.repo.write("a.txt", "AcmeCorp")
        self.repo.commit("initial")
        self.assertTrue(verify(self.repo.path, []).ok)


if __name__ == "__main__":
    unittest.main()
