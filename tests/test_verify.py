"""履歴を含めた検証に対する検査。

このツールの存在理由がここにある。ファイルから消しただけでは
消えないという事実を、実際に git リポジトリを作って確かめる。
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from publish_guard.verify import verify


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


class RepoFixture:
    """テスト用の git リポジトリを作る。"""

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

    def test_ファイルから消しても履歴から見つける(self):
        # これがこのツールの中心。編集コミットを積んでも消えない。
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("初期")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("組織名を差し替える")

        report = verify(self.repo.path, ["AcmeCorp"])
        self.assertFalse(report.ok)
        self.assertTrue(any(f.matched == "AcmeCorp" for f in report.findings))

    def test_履歴を見なければ見逃す(self):
        # --no-history の挙動。これがこのツールを使う意味を裏から示す。
        # HEAD の tree だけを見れば消えたように見えてしまう。
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("初期")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("組織名を差し替える")

        self.assertTrue(verify(self.repo.path, ["AcmeCorp"], history=False).ok)
        self.assertFalse(verify(self.repo.path, ["AcmeCorp"], history=True).ok)

    def test_現在のブランチだけの走査でも祖先は辿る(self):
        # all_refs=False は「他の ref を見ない」であって
        # 「履歴を見ない」ではない。混同しやすいので明示する。
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("初期")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("組織名を差し替える")

        self.assertFalse(verify(self.repo.path, ["AcmeCorp"], all_refs=False).ok)

    def test_最初から無ければ検出しない(self):
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("初期")
        self.assertTrue(verify(self.repo.path, ["AcmeCorp"]).ok)

    def test_履歴のみかどうかを区別する(self):
        self.repo.write("config.json", '{"org": "AcmeCorp"}')
        self.repo.commit("初期")
        self.repo.write("config.json", '{"org": "Example"}')
        self.repo.commit("組織名を差し替える")

        report = verify(self.repo.path, ["AcmeCorp"])
        blob_findings = [f for f in report.findings if f.location == "config.json"]
        self.assertTrue(blob_findings)
        # config.json は現在も存在するので history_only ではない
        self.assertFalse(blob_findings[0].in_history_only)

    def test_削除されたファイルは履歴のみと印をつける(self):
        self.repo.write("secret-notes.md", "AcmeCorp の設定メモ")
        self.repo.commit("メモを追加")
        (self.repo.path / "secret-notes.md").unlink()
        self.repo.commit("メモを削除")

        report = verify(self.repo.path, ["AcmeCorp"])
        self.assertFalse(report.ok)
        self.assertTrue(all(f.in_history_only for f in report.findings))


class VariantTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoFixture()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_パーセントエンコード形を見つける(self):
        # 素の文字列は消したがURLの中に残っている、という状況。
        self.repo.write("test.js", 'expect(url).toMatch(/%E5%A3%B2%E4%B8%8A%E9%AB%98/)')
        self.repo.commit("テストを追加")

        report = verify(self.repo.path, ["売上高"])
        self.assertFalse(report.ok)
        # 報告は元の語で行う。エンコード形だけ見せても人間に伝わらない。
        self.assertEqual(report.findings[0].term, "売上高")

    def test_大文字の変種を見つける(self):
        self.repo.write("run.sh", 'VERIFIED_ACMECORP_WINDOW_ID=1')
        self.repo.commit("スクリプトを追加")
        self.assertFalse(verify(self.repo.path, ["acmecorp"]).ok)

    def test_正規表現エスケープ形を見つける(self):
        self.repo.write("test.js", 'assert.match(s, /automation\\.once\\.json/)')
        self.repo.commit("テストを追加")
        self.assertFalse(verify(self.repo.path, ["automation.once.json"]).ok)


class MetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoFixture()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_コミットメッセージからも見つける(self):
        # ファイルが綺麗でも、メッセージに残っていることがある。
        self.repo.write("readme.md", "clean")
        self.repo.commit("AcmeCorp 向けの設定を追加")

        report = verify(self.repo.path, ["AcmeCorp"])
        self.assertFalse(report.ok)
        self.assertTrue(
            any(f.location == "コミットメタデータ" for f in report.findings)
        )

    def test_メタデータ検査を切れる(self):
        self.repo.write("readme.md", "clean")
        self.repo.commit("AcmeCorp 向けの設定を追加")
        self.assertTrue(verify(self.repo.path, ["AcmeCorp"], check_metadata=False).ok)


class EmptyInputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = RepoFixture()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_禁止語が空なら常にOK(self):
        self.repo.write("a.txt", "AcmeCorp")
        self.repo.commit("初期")
        self.assertTrue(verify(self.repo.path, []).ok)


if __name__ == "__main__":
    unittest.main()
