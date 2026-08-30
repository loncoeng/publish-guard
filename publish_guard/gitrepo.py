"""Git リポジトリから、履歴を含む全てのファイル内容を取り出す。

作業ツリーだけを見ても意味がない。ファイルから値を消しても、
それ以前のコミットには残っており `git log -p` で読める。公開する
かどうかを判断するなら、見るべきは全コミットの全 blob である。

同じ blob が複数のコミットに現れるため、SHA で重複を除く。
そうしないとコミット数に比例して無駄に走査することになる。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class NotAGitRepository(Exception):
    pass


@dataclass(frozen=True)
class Blob:
    sha: str
    path: str


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise NotAGitRepository(message or f"git {' '.join(args)} に失敗しました")
    return result.stdout.decode("utf-8", "replace")


def is_git_repository(repo: Path) -> bool:
    try:
        _git(repo, "rev-parse", "--git-dir")
        return True
    except NotAGitRepository:
        return False


def commits(repo: Path, *, all_refs: bool = True) -> list[str]:
    """走査対象のコミットを返す。既定は全ての ref から到達できるもの。"""
    args = ["rev-list", "--all"] if all_refs else ["rev-list", "HEAD"]
    return _git(repo, *args).split()


def blobs(repo: Path, *, all_refs: bool = True, history: bool = True) -> list[Blob]:
    """blob を SHA で重複を除いて返す。

    history=False なら HEAD の tree だけを見る。速いが、履歴に残った
    ものは見つからない。コミット前の下見にだけ使うこと。
    """
    if not history:
        return _head_tree(repo)
    seen: dict[str, Blob] = {}
    for commit in commits(repo, all_refs=all_refs):
        for line in _git(repo, "ls-tree", "-r", commit).splitlines():
            # <mode> <type> <sha>\t<path>
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) < 3 or parts[1] != "blob":
                continue
            sha = parts[2]
            if sha not in seen:
                seen[sha] = Blob(sha=sha, path=path)
    return list(seen.values())


def _head_tree(repo: Path) -> list[Blob]:
    found: list[Blob] = []
    for line in _git(repo, "ls-tree", "-r", "HEAD").splitlines():
        meta, _, path = line.partition("	")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            found.append(Blob(sha=parts[2], path=path))
    return found


def read_blob(repo: Path, sha: str) -> str | None:
    """blob をテキストとして読む。バイナリなら None。"""
    raw = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-p", sha],
        capture_output=True,
    ).stdout
    if b"\x00" in raw[:8192]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def metadata(repo: Path, *, all_refs: bool = True) -> str:
    """コミットメッセージ・著者名・メールアドレスをまとめて返す。

    ファイルの中身が綺麗でも、コミットメッセージに残っていることがある。
    「業種が割れる項目を外す」のようなメッセージは、何を隠したかを
    そのまま教えてしまう。
    """
    args = ["log", "--format=%an%n%ae%n%cn%n%ce%n%s%n%b"]
    if all_refs:
        args.insert(1, "--all")
    return _git(repo, *args)


def working_tree_files(repo: Path) -> list[str]:
    return [line for line in _git(repo, "ls-files").splitlines() if line]
