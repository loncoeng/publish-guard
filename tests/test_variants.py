"""別表記の展開に対する検査。

ここが壊れると、素の文字列だけを消して安心する状態に戻る。
実際にそれで取りこぼしたケースを、そのままテストにしてある。
"""

from __future__ import annotations

import unittest

from publish_guard.variants import expand, variants


class JapaneseTest(unittest.TestCase):
    def test_パーセントエンコード形を含む(self):
        # Sheets API の URL では日本語のシート名がこの形で現れる。
        # テストの期待値にもこの形で書かれていて、素の文字列を
        # 置換しただけでは残った。
        self.assertIn("%E5%A3%B2%E4%B8%8A%E9%AB%98", variants("売上高"))

    def test_小文字のパーセントエンコード形も含む(self):
        # エンコードする実装によって %E7 と %e7 が揺れる。
        self.assertIn("%e5%a3%b2%e4%b8%8a%e9%ab%98", variants("売上高"))

    def test_JSONのユニコードエスケープ形を含む(self):
        # ensure_ascii で書き出された JSON では目視で見つからない。
        self.assertIn("\\u58f2\\u4e0a\\u9ad8", variants("売上高"))

    def test_元の文字列自体も含む(self):
        self.assertIn("売上高", variants("売上高"))

    def test_日本語には大文字小文字の変種を作らない(self):
        # 意味のない候補を増やすと走査が遅くなるだけ。
        self.assertEqual(
            len([v for v in variants("売上高") if v == "売上高"]), 1
        )


class AsciiTest(unittest.TestCase):
    def test_大文字小文字の変種を含む(self):
        # 社名は環境変数で ACMECORP、コメントで Acmecorp のように現れる。
        got = variants("acmecorp")
        for expected in ("acmecorp", "ACMECORP", "Acmecorp"):
            self.assertIn(expected, got)

    def test_内部の大文字は復元できない(self):
        # acmecorp から AcmeCorp は導けない。語の境界がどこか分からないため。
        # キャメルケースで書かれる可能性があるなら、設定に両方書く必要がある。
        self.assertNotIn("AcmeCorp", variants("acmecorp"))

    def test_ASCIIにはパーセントエンコード形を作らない(self):
        # ASCII はエンコードしても変わらないので候補を増やさない。
        self.assertNotIn("%61%63%6d%65%63%6f%72%70", variants("acmecorp"))


class RegexEscapeTest(unittest.TestCase):
    def test_ドットをエスケープした形を含む(self):
        # 正規表現リテラルの中ではこう書かれている。
        self.assertIn("automation\\.once\\.json", variants("automation.once.json"))

    def test_ドットが無ければエスケープ形は作らない(self):
        self.assertNotIn("acme\\.", variants("acme"))

    def test_ホスト名でもエスケープ形を作る(self):
        self.assertIn("admin\\.acme\\.example", variants("admin.acme.example"))


class OrderingTest(unittest.TestCase):
    def test_長い順に返す(self):
        got = variants("売上高")
        self.assertEqual(got, sorted(got, key=len, reverse=True))

    def test_expandも長い順に返す(self):
        # 置換に使うとき、短い語を先に当てると長い語が壊れる。
        # 「平均売上高」を「売上高」より先に処理する必要がある。
        got = expand(["売上高", "平均売上高"])
        self.assertEqual(got, sorted(got, key=len, reverse=True))

    def test_expandは重複を除く(self):
        got = expand(["acme", "acme"])
        self.assertEqual(len(got), len(set(got)))


class EdgeCaseTest(unittest.TestCase):
    def test_空文字は候補に残さない(self):
        self.assertNotIn("", variants(""))
        self.assertNotIn("", expand(["", "acme"]))

    def test_日本語とASCIIが混ざる語も扱える(self):
        got = variants("Acme売上高")
        self.assertIn("Acme売上高", got)
        self.assertTrue(any(v.startswith("%") or "\\u" in v for v in got))


if __name__ == "__main__":
    unittest.main()
