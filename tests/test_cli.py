"""CLI の入口に対する検査。

利用者が最初にぶつかるのは設定ファイルの間違いで、そこで生の
トレースバックが出ると「壊れているツール」に見える。終了コードも
CI の挙動に直結するので、ここは仕様として固定しておく。
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

    def test_設定ファイルが無ければ案内を出す(self):
        with self.assertRaises(ConfigError) as ctx:
            _load_terms(self.dir / "missing.toml")
        self.assertIn("見つかりません", str(ctx.exception))

    def test_書式が壊れていれば場所を示す(self):
        path = self._write('[forbidden]\nterms = [ broken\n')
        with self.assertRaises(ConfigError) as ctx:
            _load_terms(path)
        self.assertIn("書式", str(ctx.exception))

    def test_termsが配列でなければ弾く(self):
        path = self._write('[forbidden]\nterms = "AcmeCorp"\n')
        with self.assertRaises(ConfigError):
            _load_terms(path)

    def test_空文字は落とす(self):
        path = self._write('[forbidden]\nterms = ["AcmeCorp", ""]\n')
        self.assertEqual(_load_terms(path), ["AcmeCorp"])

    def test_正常な設定を読める(self):
        path = self._write('[forbidden]\nterms = ["AcmeCorp", "売上高"]\n')
        self.assertEqual(_load_terms(path), ["AcmeCorp", "売上高"])


class ExitCodeTest(unittest.TestCase):
    """CI に置く以上、終了コードは仕様である。"""

    @staticmethod
    def _run(argv: list[str]) -> int:
        # main は人間向けに標準出力へ書く。テストの出力に混ざると
        # 何が失敗したのか読めなくなるので捨てる。
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
        self._git("commit", "-q", "-m", "初期")

    def tearDown(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def _config(self, terms: str) -> Path:
        path = self.dir / "config.toml"
        with io.open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write("[forbidden]\nterms = [" + terms + "]\n")
        return path

    def test_検出なしなら0(self):
        code = self._run(["verify", str(self.repo), "-c", str(self._config('"Nothing"'))])
        self.assertEqual(code, 0)

    def test_検出ありなら1(self):
        code = self._run(["verify", str(self.repo), "-c", str(self._config('"AcmeCorp"'))])
        self.assertEqual(code, 1)

    def test_設定ファイルが無ければ2(self):
        code = self._run(["verify", str(self.repo), "-c", str(self.dir / "missing.toml")])
        self.assertEqual(code, 2)

    def test_gitリポジトリでなければ2(self):
        code = self._run(["verify", str(self.dir), "-c", str(self._config('"AcmeCorp"'))])
        self.assertEqual(code, 2)

    def test_scanはgitリポジトリでなければ2(self):
        self.assertEqual(self._run(["scan", str(self.dir)]), 2)


if __name__ == "__main__":
    unittest.main()
