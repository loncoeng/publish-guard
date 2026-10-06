"""Tests for turning up candidates.

scan suggests rather than judges, which makes "do not bury it in noise" the
harder half of the job — two hundred candidates is a list nobody reads, and
something gets missed anyway. The exclusion decisions are pinned here.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from publish_guard.scan import _keep, _looks_like_placeholder, scan, should_skip

# An invented identifier, shaped like a Drive folder id. Not a real one.
FOLDER_ID = "1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo"


class PlaceholderTest(unittest.TestCase):
    def test_anything_containing_example_is_not_a_candidate(self):
        self.assertTrue(_looks_like_placeholder("admin.example.com"))
        self.assertTrue(_looks_like_placeholder("reader@example-project.iam.gserviceaccount.com"))

    def test_all_caps_placeholders_are_not_candidates(self):
        self.assertTrue(_looks_like_placeholder("EXAMPLE_SPREADSHEET_ID"))
        self.assertTrue(_looks_like_placeholder("YOUR_ACCOUNT_ID"))

    def test_anything_starting_with_replace_with_is_not_a_candidate(self):
        self.assertTrue(_looks_like_placeholder("replace-with-owner@example.com"))

    def test_values_that_look_real_are_kept(self):
        self.assertFalse(_looks_like_placeholder(FOLDER_ID))
        self.assertFalse(_looks_like_placeholder("admin.acme.jp"))


class OpaqueIdFilterTest(unittest.TestCase):
    """Long strings turn up by the hundred. Only the identifier-shaped ones stay."""

    def test_a_long_string_mixing_letters_and_digits_is_a_candidate(self):
        self.assertTrue(_keep("opaque-id", FOLDER_ID))

    def test_a_long_run_of_only_letters_is_not(self):
        # Long function and constant names in here make the list useless.
        self.assertFalse(_keep("opaque-id", "dateAvailabilityRetryDelay"))

    def test_a_long_run_of_only_digits_is_not(self):
        self.assertFalse(_keep("opaque-id", "12345678901234567890"))

    def test_a_camel_case_identifier_is_not(self):
        self.assertFalse(_keep("opaque-id", "limitedResumeAfterDuplicate"))


class HostFilterTest(unittest.TestCase):
    def test_well_known_hosts_are_not_candidates(self):
        self.assertFalse(_keep("host", "github.com"))
        self.assertFalse(_keep("host", "docs.google.com"))

    def test_placeholder_hosts_are_not_candidates(self):
        self.assertFalse(_keep("host", "example.com"))
        self.assertFalse(_keep("host", "localhost"))

    def test_an_unfamiliar_host_is_a_candidate(self):
        self.assertTrue(_keep("host", "admin.acme.jp"))


class ScanRepoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="publish-guard-scan-"))
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.com")

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.path), *args], check=True, capture_output=True)

    def _write(self, name: str, text: str) -> None:
        with io.open(self.path / name, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)

    def test_a_credential_is_found(self):
        self._write("config.sh", "export TOKEN=ghp_0123456789abcdefghijklmnopqrstuvwxyz")
        self._commit("add the configuration")
        report = scan(self.path)
        self.assertIn("secret", report.categories)

    def test_a_home_directory_path_is_found(self):
        self._write("worker.service", "WorkingDirectory=/home/acme-operator/app")
        self._commit("add the unit file")
        report = scan(self.path)
        self.assertIn("home-path", report.categories)
        self.assertIn("/home/acme-operator", report.categories["home-path"])

    def test_a_value_from_a_deleted_file_is_still_found(self):
        self._write("notes.md", f"folder id: {FOLDER_ID}")
        self._commit("add the notes")
        (self.path / "notes.md").unlink()
        self._commit("remove the notes")

        report = scan(self.path)
        found = report.categories["opaque-id"][FOLDER_ID]
        self.assertTrue(found.history_only)

    def test_without_the_history_a_deleted_value_does_not_appear(self):
        self._write("notes.md", f"folder id: {FOLDER_ID}")
        self._commit("add the notes")
        (self.path / "notes.md").unlink()
        self._commit("remove the notes")

        report = scan(self.path, history=False)
        self.assertNotIn(FOLDER_ID, report.categories.get("opaque-id", {}))

    def test_a_repository_of_placeholders_produces_no_candidates(self):
        self._write(".env.example", "API_URL=https://api.example.com\nOWNER=replace-with-owner@example.com")
        self._commit("add the template")
        report = scan(self.path)
        self.assertEqual(report.total, 0)


class SkipPathTest(unittest.TestCase):
    """How the exclusion patterns match."""

    def test_it_matches_a_filename(self):
        self.assertTrue(should_skip("package-lock.json", ("package-lock.json",)))

    def test_it_matches_a_filename_further_down(self):
        # Excluding only the one at the root and leaving web/'s in is not what
        # anybody means by it.
        self.assertTrue(should_skip("web/package-lock.json", ("package-lock.json",)))

    def test_it_matches_a_glob_over_the_whole_path(self):
        self.assertTrue(should_skip("vendor/lib/a.js", ("vendor/*",)))
        self.assertTrue(should_skip("docs/old/notes.md", ("docs/**",)))

    def test_it_matches_an_extension_glob(self):
        self.assertTrue(should_skip("data/dump.csv", ("*.csv",)))

    def test_what_does_not_match_is_kept(self):
        self.assertFalse(should_skip("src/index.js", ("package-lock.json", "*.csv")))

    def test_no_patterns_excludes_nothing(self):
        self.assertFalse(should_skip("package-lock.json", ()))


class LockfileSkipTest(unittest.TestCase):
    """Lockfiles are left out by default.

    This went in because of an actual problem. Scanning a JavaScript
    repository gave 197 candidates, 155 of them integrity hashes out of
    package-lock.json, and the ones worth looking at were buried deep enough
    that the scan was no use for an audit.
    """

    LOCK = '{"packages":{"":{"dependencies":{}}},"integrity":"sha512-7nwRJhN1HWpVmJm511pBHUxPLtp0BUISzlBplORYSmTclCnJvQq2tKu"}'

    def setUp(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="publish-guard-skip-"))
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.com")

    def tearDown(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.path), *args], check=True, capture_output=True)

    def _write(self, name: str, text: str) -> None:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with io.open(target, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def _commit(self, message: str) -> None:
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)

    def test_a_lockfile_produces_no_candidates_by_default(self):
        self._write("package-lock.json", self.LOCK)
        self._commit("add a dependency")
        report = scan(self.path)
        self.assertEqual(report.total, 0)

    def test_what_was_excluded_is_counted_and_reported(self):
        # Skip it quietly and the user believes everything was looked at.
        self._write("package-lock.json", self.LOCK)
        self._commit("add a dependency")
        report = scan(self.path)
        self.assertEqual(report.blobs_skipped, 1)
        self.assertIn("package-lock.json", report.skipped_paths)

    def test_lockfiles_are_scanned_when_asked_for(self):
        self._write("package-lock.json", self.LOCK)
        self._commit("add a dependency")
        report = scan(self.path, skip_lockfiles=False)
        self.assertGreater(report.total, 0)
        self.assertEqual(report.blobs_skipped, 0)

    def test_excluding_a_lockfile_does_not_exclude_everything_else(self):
        self._write("package-lock.json", self.LOCK)
        self._write("notes.md", f"folder id: {FOLDER_ID}")
        self._commit("add both")
        report = scan(self.path)
        self.assertIn(FOLDER_ID, report.categories["opaque-id"])

    def test_an_explicit_exclusion_takes_effect(self):
        self._write("notes.md", f"folder id: {FOLDER_ID}")
        self._commit("add the notes")
        report = scan(self.path, exclude=("notes.md",))
        self.assertEqual(report.total, 0)
        self.assertEqual(report.blobs_skipped, 1)

    def test_an_exclusion_does_not_stop_the_history_being_read(self):
        # An exclusion is per file, not a decision to ignore the history.
        self._write("keep.md", f"folder id: {FOLDER_ID}")
        self._write("drop.md", "x")
        self._commit("add both")
        (self.path / "keep.md").unlink()
        self._commit("remove one")
        report = scan(self.path, exclude=("drop.md",))
        self.assertTrue(report.categories["opaque-id"][FOLDER_ID].history_only)


if __name__ == "__main__":
    unittest.main()
