"""Check that the forbidden terms are really gone, history included.

Doing the replacing is not this tool's job. What ought to go cannot be decided
without knowing the project, so a person decides that. All this takes on is
confirming that what was decided actually went — and that part a machine does
more reliably.

Unlike scan, there is no exclusion mechanism here. Excluding things from scan
stops the candidates being buried, and the only cost is the quality of a
suggestion. verify is the gate that stops a repository being published, and a
gate with exceptions in it is not a gate.

Lockfiles are not excused either. Mostly they hold checksums, but a private
registry's package name — `@acmecorp/internal-ui` — or an internal mirror's URL
ends up in them. **As ways of giving a client away, those are about as typical
as it gets.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import gitrepo
from .variants import expand, variants


@dataclass
class Finding:
    term: str          # the term as written in the config
    matched: str       # the form it was actually found in
    location: str      # a file path, or "commit metadata"
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
    """Let an expanded form be traced back to the term it came from.

    "%E5%A3%B2%E4%B8%8A%E9%AB%98 was found" tells a person nothing. It has to
    be reported next to the term it belongs to.
    """
    owner: dict[str, str] = {}
    for term in terms:
        for v in variants(term):
            # Where one form comes from several terms, the longer term wins:
            # between "revenue" and "average revenue", the latter says more.
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
    """Look for terms, and their other forms, in repo. The whole history by default."""
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
                        location="commit metadata",
                        in_history_only=False,
                    )
                )

    return report
