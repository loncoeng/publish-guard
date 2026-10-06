"""Tests for the command line entry point.

The first thing a user runs into is a mistake in the config file, and a raw
traceback there makes the tool look broken. The exit codes decide what CI
does, so they are pinned here as the contract they are.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from publish_guard.cli import ConfigError, _load_terms, main

# A non-ASCII term, as the fixture for the percent-encoding and \uXXXX paths.
# It is an ordinary Japanese word, there because that is what a non-ASCII term
# looks like — not because the text around it is Japanese.
NON_ASCII_TERM = "取引先"


class LoadTermsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="publish-guard-cli-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, text: str) -> Path:
        path = self.dir / "config.toml"
        with io.open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return path

    def test_a_missing_config_says_so(self):
        with self.assertRaises(ConfigError) as ctx:
            _load_terms(self.dir / "missing.toml")
        self.assertIn("no config file", str(ctx.exception))

    def test_a_malformed_config_says_where(self):
        path = self._write('[forbidden]\nterms = [ broken\n')
        with self.assertRaises(ConfigError) as ctx:
            _load_terms(path)
        self.assertIn("malformed", str(ctx.exception))

    def test_terms_that_are_not_an_array_are_refused(self):
        path = self._write('[forbidden]\nterms = "AcmeCorp"\n')
        with self.assertRaises(ConfigError):
            _load_terms(path)

    def test_empty_strings_are_dropped(self):
        path = self._write('[forbidden]\nterms = ["AcmeCorp", ""]\n')
        self.assertEqual(_load_terms(path), ["AcmeCorp"])

    def test_a_valid_config_loads(self):
        path = self._write(f'[forbidden]\nterms = ["AcmeCorp", "{NON_ASCII_TERM}"]\n')
        self.assertEqual(_load_terms(path), ["AcmeCorp", NON_ASCII_TERM])


class ExitCodeTest(unittest.TestCase):
    """Put in CI, the exit codes are the contract."""

    @staticmethod
    def _run(argv: list[str]) -> int:
        # main writes to stdout for a person to read. Mixed into the test
        # output it buries what actually failed, so it goes nowhere.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(argv)

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="publish-guard-exit-"))
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.name", "Test")
        self._git("config", "user.email", "test@example.com")
        with io.open(self.repo / "a.txt", "w", encoding="utf-8", newline="") as handle:
            handle.write("AcmeCorp")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "initial")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def _config(self, terms: str) -> Path:
        path = self.dir / "config.toml"
        with io.open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("[forbidden]\nterms = [" + terms + "]\n")
        return path

    def test_nothing_found_is_0(self):
        code = self._run(["verify", str(self.repo), "-c", str(self._config('"Nothing"'))])
        self.assertEqual(code, 0)

    def test_something_found_is_1(self):
        code = self._run(["verify", str(self.repo), "-c", str(self._config('"AcmeCorp"'))])
        self.assertEqual(code, 1)

    def test_a_missing_config_is_2(self):
        code = self._run(["verify", str(self.repo), "-c", str(self.dir / "missing.toml")])
        self.assertEqual(code, 2)

    def test_not_a_git_repository_is_2(self):
        code = self._run(["verify", str(self.dir), "-c", str(self._config('"AcmeCorp"'))])
        self.assertEqual(code, 2)

    def test_scan_on_something_that_is_not_a_repository_is_2(self):
        self.assertEqual(self._run(["scan", str(self.dir)]), 2)


if __name__ == "__main__":
    unittest.main()
