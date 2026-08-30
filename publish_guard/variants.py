"""同じ値が別の表記で現れる、その別表記を列挙する。

素の文字列だけを検索すると取りこぼす。実際に踏んだ例を挙げる。

  日本語のシート名は Sheets API の URL でパーセントエンコードされ、
  テストの期待値にその形で書かれていた。素の文字列を置換しても
  期待値は変わらず、テストが落ちて初めて気付いた。

  ファイル名は正規表現リテラルの中でドットがエスケープされていた。
  "automation.once.json" を置換しても "automation\\.once\\.json" は残った。

  組織名は大文字・キャメルケースの変種でも現れた。ACMECORP / AcmeCorp /
  acmecorp の3通りがあり、小文字だけ消して安心していた。

  JSON に ensure_ascii で書き出された日本語は \\uXXXX 形式になる。
  ファイルを開いても目視では見つからない。

このモジュールは、ひとつの語からこれら全ての表記を機械的に導く。
人間が思い出す方式に頼らないためにある。

導けないものもある。acmecorp から AcmeCorp は導けない。語の境界が
どこにあるか判断できないためで、キャメルケースで書かれる可能性が
あるなら設定に両方書く必要がある。ここは正直に限界としておく。
"""

from __future__ import annotations

import json
import urllib.parse


def _percent(value: str, *, upper: bool) -> str:
    encoded = urllib.parse.quote(value, safe="")
    return encoded if upper else encoded.lower()


def _json_escaped(value: str) -> str:
    # json.dumps は ensure_ascii=True で非ASCIIを \uXXXX にする。
    # 前後の引用符は落として中身だけ返す。
    return json.dumps(value, ensure_ascii=True)[1:-1]


def _regex_escaped(value: str) -> str:
    # 正規表現リテラルの中では . が \. と書かれる。re.escape は
    # 他の記号も潰してしまい実際の書かれ方から離れるため、
    # 現実に遭遇するドットだけを対象にする。
    return value.replace(".", "\\.")


def variants(term: str) -> list[str]:
    """term が現れうる表記を、長い順に返す。

    長い順に返すのは、置換に使うときに部分一致で壊さないため。
    「月間平均売上高」を先に処理しないと、「売上高」の置換で
    前半が取り残された文字列ができあがる。
    """
    found: set[str] = {term}

    has_non_ascii = any(ord(c) > 127 for c in term)

    if has_non_ascii:
        found.add(_percent(term, upper=True))
        found.add(_percent(term, upper=False))
        found.add(_json_escaped(term))
    else:
        # ASCII の語は大文字小文字の揺れで現れる。
        found.add(term.upper())
        found.add(term.lower())
        if term:
            found.add(term[0].upper() + term[1:])

    if "." in term:
        found.add(_regex_escaped(term))
        # ドットを含む ASCII 語は、エスケープ形の大小変種もありうる。
        if not has_non_ascii:
            found.add(_regex_escaped(term.lower()))

    # 空文字は探索対象にならないので落とす。
    found.discard("")
    return sorted(found, key=len, reverse=True)


def expand(terms: list[str]) -> list[str]:
    """複数の語をまとめて展開し、重複を除いて長い順に返す。"""
    seen: set[str] = set()
    for term in terms:
        seen.update(variants(term))
    seen.discard("")
    return sorted(seen, key=len, reverse=True)
