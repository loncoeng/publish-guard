"""Get every file's contents out of a git repository, history included.

Looking at the working tree is pointless on its own. Remove a value from a
file and it is still in the commits before that one, readable with
`git log -p`. Deciding whether something can be published means looking at
every blob in every commit.

The same blob appears in many commits, so they are deduplicated by SHA.
Without that, the work grows with the number of commits for no reason.
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
        raise NotAGitRepository(message or f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", "replace")


def is_git_repository(repo: Path) -> bool:
    try:
        _git(repo, "rev-parse", "--git-dir")
        return True
    except NotAGitRepository:
        return False


def commits(repo: Path, *, all_refs: bool = True) -> list[str]:
    """The commits to scan. By default, everything reachable from any ref."""
    args = ["rev-list", "--all"] if all_refs else ["rev-list", "HEAD"]
    return _git(repo, *args).split()


def blobs(repo: Path, *, all_refs: bool = True, history: bool = True) -> list[Blob]:
    """The blobs, deduplicated by SHA.

    With history=False only HEAD's tree is read. Faster, and it finds nothing
    left behind in the history — for a look before committing, and nothing else.
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
    """Read a blob as text. None if it is binary."""
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
    """Commit messages, author names and email addresses, all together.

    File contents can be spotless while a commit message still says it. A
    message like "drop the fields that give the industry away" hands over
    exactly what was being hidden.
    """
    args = ["log", "--format=%an%n%ae%n%cn%n%ce%n%s%n%b"]
    if all_refs:
        args.insert(1, "--all")
    return _git(repo, *args)


def working_tree_files(repo: Path) -> list[str]:
    return [line for line in _git(repo, "ls-files").splitlines() if line]
