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

from publish_guard.scan import _keep, _looks_like_placeholder, scan, should_skip


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


class SkipPathTest(unittest.TestCase):
    """除外パターンの当たり方。"""

    def test_ファイル名で当たる(self):
        self.assertTrue(should_skip("package-lock.json", ("package-lock.json",)))

    def test_深い階層のファイル名にも当たる(self):
        # 直下のものだけ外れて web/ の下が残る、という挙動は意図に反する。
        self.assertTrue(should_skip("web/package-lock.json", ("package-lock.json",)))

    def test_パス全体のグロブで当たる(self):
        self.assertTrue(should_skip("vendor/lib/a.js", ("vendor/*",)))
        self.assertTrue(should_skip("docs/old/notes.md", ("docs/**",)))

    def test_拡張子のグロブで当たる(self):
        self.assertTrue(should_skip("data/dump.csv", ("*.csv",)))

    def test_当たらないものは残す(self):
        self.assertFalse(should_skip("src/index.js", ("package-lock.json", "*.csv")))

    def test_パターンが空なら何も外さない(self):
        self.assertFalse(should_skip("package-lock.json", ()))


class LockfileSkipTest(unittest.TestCase):
    """ロックファイルは既定で外す。

    これは実際に困って入れた。JavaScript のリポジトリを scan したら
    候補197件のうち155件が package-lock.json の整合性ハッシュで、
    本当に見るべきものが埋もれて監査に使えなかった。
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

    def test_ロックファイルの中身は既定で候補にしない(self):
        self._write("package-lock.json", self.LOCK)
        self._commit("依存を追加")
        report = scan(self.path)
        self.assertEqual(report.total, 0)

    def test_外したことを数えて残す(self):
        # 黙って飛ばすと、利用者は全部を見たつもりになる。
        self._write("package-lock.json", self.LOCK)
        self._commit("依存を追加")
        report = scan(self.path)
        self.assertEqual(report.blobs_skipped, 1)
        self.assertIn("package-lock.json", report.skipped_paths)

    def test_明示すればロックファイルも走査する(self):
        self._write("package-lock.json", self.LOCK)
        self._commit("依存を追加")
        report = scan(self.path, skip_lockfiles=False)
        self.assertGreater(report.total, 0)
        self.assertEqual(report.blobs_skipped, 0)

    def test_ロックファイルを外しても他のファイルは見る(self):
        self._write("package-lock.json", self.LOCK)
        self._write("notes.md", "folder id: 1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo")
        self._commit("追加")
        report = scan(self.path)
        self.assertIn("1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo", report.categories["opaque-id"])

    def test_指定した除外が効く(self):
        self._write("notes.md", "folder id: 1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo")
        self._commit("メモを追加")
        report = scan(self.path, exclude=("notes.md",))
        self.assertEqual(report.total, 0)
        self.assertEqual(report.blobs_skipped, 1)

    def test_除外しても履歴の走査自体は止まらない(self):
        # 除外はファイル単位であって、履歴を見ないことではない。
        self._write("keep.md", "folder id: 1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo")
        self._write("drop.md", "x")
        self._commit("追加")
        (self.path / "keep.md").unlink()
        self._commit("削除")
        report = scan(self.path, exclude=("drop.md",))
        self.assertTrue(report.categories["opaque-id"]["1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo"].history_only)


if __name__ == "__main__":
    unittest.main()
