"""禁止語が本当に消えたかを、履歴を含めて検証する。

置換を実行するのはこのツールの仕事ではない。何を消すべきかは
プロジェクトを知らないと判断できないため、そこは人間が決める。
このツールが担うのは「決めたものが本当に消えたか」の確認だけで、
そこは機械の方が確実にできる。

scan と違い、こちらには除外の仕組みを置いていない。scan の除外は
候補が埋もれるのを防ぐためのもので、外しても失うのは提案の精度でしかない。
verify は公開を止める門なので、そこに除外を作ると門の意味がなくなる。

ロックファイルも例外にしない。中身はチェックサムばかりだが、
`@acmecorp/internal-ui` のような私設レジストリのパッケージ名や、
社内ミラーの URL が入ることがある。**委託元が割れる形としては、
むしろ典型的な部類に入る**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gitrepo
from .variants import expand, variants


@dataclass
class Finding:
    term: str          # 設定に書かれた元の語
    matched: str       # 実際に見つかった表記
    location: str      # ファイルパス、または "コミットメタデータ"
    in_history_only: bool


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    commits_scanned: int = 0
    blobs_scanned: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def by_term(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = {}
        for f in self.findings:
            grouped.setdefault(f.term, []).append(f)
        return grouped


def _variant_owner(terms: list[str]) -> dict[str, str]:
    """展開後の表記から、元の語を引けるようにする。

    報告のときに「%E5%A3%B2%E4%B8%8A%E9%AB%98 が見つかりました」だけでは
    人間に伝わらない。元の語と対にして示す必要がある。
    """
    owner: dict[str, str] = {}
    for term in terms:
        for v in variants(term):
            # 同じ表記が複数の語から生成されたときは、長い語を優先する。
            # 「売上高」と「平均売上高」なら後者の方が情報量が多い。
            if v not in owner or len(term) > len(owner[v]):
                owner[v] = term
    return owner


def verify(
    repo: Path,
    terms: list[str],
    *,
    all_refs: bool = True,
    history: bool = True,
    check_metadata: bool = True,
) -> Report:
    """repo から terms（とその別表記）を探す。既定は全履歴。"""
    report = Report()
    if not terms:
        return report

    owner = _variant_owner(terms)
    needles = expand(terms)

    head_paths = set(gitrepo.working_tree_files(repo))
    report.commits_scanned = len(gitrepo.commits(repo, all_refs=all_refs)) if history else 1

    for blob in gitrepo.blobs(repo, all_refs=all_refs, history=history):
        text = gitrepo.read_blob(repo, blob.sha)
        if text is None:
            continue
        report.blobs_scanned += 1
        for needle in needles:
            if needle in text:
                report.findings.append(
                    Finding(
                        term=owner.get(needle, needle),
                        matched=needle,
                        location=blob.path,
                        in_history_only=blob.path not in head_paths,
                    )
                )

    if check_metadata and history:
        meta = gitrepo.metadata(repo, all_refs=all_refs)
        for needle in needles:
            if needle in meta:
                report.findings.append(
                    Finding(
                        term=owner.get(needle, needle),
                        matched=needle,
                        location="コミットメタデータ",
                        in_history_only=False,
                    )
                )

    return report
