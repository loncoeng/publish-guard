"""消し忘れそうな値の候補を、履歴を含めて洗い出す。

これは判定ではなく提案である。「売上高」が業務固有の指標名で、
「データインポート」が一般的な UI ラベルだという区別は、その
プロジェクトを知らないとつかない。機械にできるのは「人間なら
見落としそうな形をしたもの」を集めて並べるところまでで、
消すかどうかは人間が決める。

裏を返せば、機械の方が確実な部分もある。40文字のランダム文字列や、
ホームディレクトリに埋め込まれたユーザー名は、目視ではまず気付かない。
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import gitrepo

# 依存関係のロックファイル。既定で scan の対象から外す。
#
# 中身は機械が書いたチェックサムの羅列で、識別子の形をしている。
# package-lock.json 一つで opaque-id が百数十件出て、本当に見るべき
# 候補がその中に埋もれる。候補が200件並べば人間は読まなくなり、
# 除外しないことがかえって見落としを生む。
#
# 外すのは scan だけで、verify には効かせない。詳しくは verify.py。
DEFAULT_SKIP = (
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lock", "bun.lockb", "deno.lock",
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum",
    "gradle.lockfile", "packages.lock.json", "mix.lock", "flake.lock",
    "pubspec.lock", "Podfile.lock",
)

# プレースホルダとして広く使われるもの。候補から外す。
PLACEHOLDER_HOSTS = {
    "example.com", "example.org", "example.net", "localhost",
    "test.com", "invalid", "example-project",
}
COMMON_HOSTS = {
    "github.com", "api.github.com", "raw.githubusercontent.com",
    "google.com", "www.google.com", "accounts.google.com",
    "docs.google.com", "drive.google.com", "sheets.googleapis.com",
    "www.googleapis.com", "developers.google.com", "cloud.google.com",
    "npmjs.com", "www.npmjs.com", "pypi.org", "nodejs.org",
    "python.org", "docs.python.org", "developer.mozilla.org",
    "opensource.org", "spdx.org", "creativecommons.org",
}

RULES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "secret",
        "秘密情報の形をしたもの。見つかったら公開の可否以前に無効化する",
        re.compile(
            r"BEGIN [A-Z ]*PRIVATE KEY"
            r"|sk-[A-Za-z0-9]{20,}"
            r"|AIza[0-9A-Za-z_\-]{30,}"
            r"|gh[pousr]_[A-Za-z0-9]{30,}"
            r"|AKIA[0-9A-Z]{16}"
            r"|xox[baprs]-[A-Za-z0-9\-]{20,}"
        ),
    ),
    (
        "email",
        "実在しそうなメールアドレス",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ),
    (
        "home-path",
        "ホームディレクトリのパス。ユーザー名が埋まっていることが多い",
        re.compile(r"/home/[A-Za-z0-9._\-]+|/Users/[A-Za-z0-9._\-]+|C:\\\\?Users\\\\?[A-Za-z0-9._\-]+"),
    ),
    (
        "uuid",
        "UUID。リソースの識別子であることが多い",
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    ),
    (
        "opaque-id",
        "20文字以上のランダムな識別子。スプレッドシートIDやフォルダIDの形",
        re.compile(r"\b[A-Za-z0-9_\-]{20,}\b"),
    ),
    (
        "host",
        "外部ホスト名。対象システムを特定しうる",
        re.compile(r"https?://([A-Za-z0-9.\-]+)"),
    ),
]


@dataclass
class Candidate:
    value: str
    locations: set[str] = field(default_factory=set)
    history_only: bool = True


@dataclass
class ScanReport:
    categories: dict[str, dict[str, Candidate]] = field(default_factory=lambda: defaultdict(dict))
    blobs_scanned: int = 0
    commits_scanned: int = 0
    # 除外したものは必ず数えて表に出す。黙って飛ばすと、利用者は
    # 全部を見たつもりになる。見ていない範囲があることは、
    # 見ていない本人に伝わらなければ意味がない。
    skipped_paths: set[str] = field(default_factory=set)
    blobs_skipped: int = 0

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.categories.values())


def should_skip(path: str, patterns: tuple[str, ...]) -> bool:
    """パスが除外パターンに当たるか。

    パターンはファイル名にもパス全体にも当てる。`package-lock.json` と
    書いたときに、リポジトリ直下のものだけが外れて `web/package-lock.json`
    が残る、という挙動は意図に反する。
    """
    name = path.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in patterns
    )


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    if any(p in lowered for p in ("example", "placeholder", "your_", "your-", "dummy", "sample")):
        return True
    if lowered.startswith("replace-with") or lowered.startswith("replace_with"):
        return True
    # EXAMPLE_SPREADSHEET_ID のような全大文字のプレースホルダ
    if value.isupper() and "_" in value:
        return True
    return False


def _keep(category: str, value: str) -> bool:
    # プレースホルダの判定は全カテゴリに効かせる。ここを分岐の中に
    # 入れると、host のようにサブドメインが付いた形
    # (api.example.com) を取りこぼす。
    if _looks_like_placeholder(value):
        return False

    if category == "host":
        return value not in PLACEHOLDER_HOSTS and value not in COMMON_HOSTS

    if category == "opaque-id":
        # 英字だけ・数字だけの長い語は識別子ではなく普通の単語や定数のことが多い。
        if value.isalpha() or value.isdigit():
            return False
        # camelCase の長い関数名などを除く。識別子は大小と数字が混ざる。
        has_digit = any(c.isdigit() for c in value)
        has_alpha = any(c.isalpha() for c in value)
        if not (has_digit and has_alpha):
            return False

    return True


def scan(
    repo: Path,
    *,
    all_refs: bool = True,
    history: bool = True,
    exclude: tuple[str, ...] = (),
    skip_lockfiles: bool = True,
) -> ScanReport:
    report = ScanReport()
    head_paths = set(gitrepo.working_tree_files(repo))
    report.commits_scanned = len(gitrepo.commits(repo, all_refs=all_refs)) if history else 1
    patterns = tuple(exclude) + (DEFAULT_SKIP if skip_lockfiles else ())

    for blob in gitrepo.blobs(repo, all_refs=all_refs, history=history):
        if patterns and should_skip(blob.path, patterns):
            report.blobs_skipped += 1
            report.skipped_paths.add(blob.path)
            continue
        text = gitrepo.read_blob(repo, blob.sha)
        if text is None:
            continue
        report.blobs_scanned += 1
        in_head = blob.path in head_paths
        for category, _desc, pattern in RULES:
            for match in pattern.finditer(text):
                value = match.group(1) if pattern.groups else match.group(0)
                if not _keep(category, value):
                    continue
                bucket = report.categories[category]
                candidate = bucket.setdefault(value, Candidate(value=value))
                candidate.locations.add(blob.path)
                if in_head:
                    candidate.history_only = False

    return report


def describe(category: str) -> str:
    for name, desc, _ in RULES:
        if name == category:
            return desc
    return ""
