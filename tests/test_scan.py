"""候補の洗い出しに対する検査。

scan は判定ではなく提案なので、「見逃さないこと」より
「ノイズで埋もれさせないこと」の方が難しい。候補が200件出れば
人間は読まなくなり、結局見落とす。除外の判断をここで固定する。
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from publish_guard.scan import _keep, _looks_like_placeholder, scan


class PlaceholderTest(unittest.TestCase):
    def test_exampleを含むものは候補にしない(self):
        self.assertTrue(_looks_like_placeholder("admin.example.com"))
        self.assertTrue(_looks_like_placeholder("reader@example-project.iam.gserviceaccount.com"))

    def test_全大文字のプレースホルダを候補にしない(self):
        self.assertTrue(_looks_like_placeholder("EXAMPLE_SPREADSHEET_ID"))
        self.assertTrue(_looks_like_placeholder("YOUR_ACCOUNT_ID"))

    def test_replace_withで始まるものを候補にしない(self):
        self.assertTrue(_looks_like_placeholder("replace-with-owner@example.com"))

    def test_本物らしい値は候補に残す(self):
        self.assertFalse(_looks_like_placeholder("1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo"))
        self.assertFalse(_looks_like_placeholder("admin.acme.jp"))


class OpaqueIdFilterTest(unittest.TestCase):
    """長い文字列は大量に出る。識別子らしいものだけを残す。"""

    def test_英数字が混ざる長い文字列は候補にする(self):
        self.assertTrue(_keep("opaque-id", "1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo"))

    def test_英字だけの長い語は候補にしない(self):
        # 長い関数名や定数名がここに入ると、候補が使い物にならなくなる。
        self.assertFalse(_keep("opaque-id", "dateAvailabilityRetryDelay"))

    def test_数字だけの長い文字列は候補にしない(self):
        self.assertFalse(_keep("opaque-id", "12345678901234567890"))

    def test_キャメルケースの識別子は候補にしない(self):
        self.assertFalse(_keep("opaque-id", "limitedResumeAfterDuplicate"))


class HostFilterTest(unittest.TestCase):
    def test_よく知られたホストは候補にしない(self):
        self.assertFalse(_keep("host", "github.com"))
        self.assertFalse(_keep("host", "docs.google.com"))

    def test_プレースホルダのホストは候補にしない(self):
        self.assertFalse(_keep("host", "example.com"))
        self.assertFalse(_keep("host", "localhost"))

    def test_見慣れないホストは候補にする(self):
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

    def test_秘密情報を検出する(self):
        self._write("config.sh", "export TOKEN=ghp_0123456789abcdefghijklmnopqrstuvwxyz")
        self._commit("設定を追加")
        report = scan(self.path)
        self.assertIn("secret", report.categories)

    def test_ホームディレクトリのパスを検出する(self):
        self._write("worker.service", "WorkingDirectory=/home/acme-operator/app")
        self._commit("ユニットを追加")
        report = scan(self.path)
        self.assertIn("home-path", report.categories)
        self.assertIn("/home/acme-operator", report.categories["home-path"])

    def test_削除済みファイルの値も履歴から検出する(self):
        self._write("notes.md", "folder id: 1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo")
        self._commit("メモを追加")
        (self.path / "notes.md").unlink()
        self._commit("メモを削除")

        report = scan(self.path)
        found = report.categories["opaque-id"]["1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo"]
        self.assertTrue(found.history_only)

    def test_履歴を見なければ削除済みの値は出ない(self):
        self._write("notes.md", "folder id: 1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo")
        self._commit("メモを追加")
        (self.path / "notes.md").unlink()
        self._commit("メモを削除")

        report = scan(self.path, history=False)
        self.assertNotIn("1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo", report.categories.get("opaque-id", {}))

    def test_プレースホルダだけのリポジトリでは候補が出ない(self):
        self._write(".env.example", "API_URL=https://api.example.com\nOWNER=replace-with-owner@example.com")
        self._commit("雛形を追加")
        report = scan(self.path)
        self.assertEqual(report.total, 0)


if __name__ == "__main__":
    unittest.main()
