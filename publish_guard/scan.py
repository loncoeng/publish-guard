"""Turn up values you might have forgotten to remove, history included.

This suggests; it does not judge. Whether "monthly churn cohort" is a metric
name specific to one client, while "data import" is an ordinary UI label, is
not something you can tell without knowing the project. What a machine can do
is collect the things shaped like what a person would miss, and lay them out.
Removing them is a decision for a person.

Which cuts the other way too: there are parts a machine is better at. A
forty-character random string, or a username buried in a home directory path,
is something you will not spot by reading.
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import gitrepo

# Dependency lockfiles, left out of scan by default.
#
# They hold machine-written checksums, which are shaped exactly like
# identifiers. One package-lock.json produces a hundred-odd opaque-id hits and
# buries the candidates that matter. Two hundred candidates is a list nobody
# reads, so not excluding these is what actually causes something to be missed.
#
# This applies to scan only, never to verify. See verify.py.
DEFAULT_SKIP = (
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lock", "bun.lockb", "deno.lock",
    "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock",
    "Cargo.lock", "composer.lock", "Gemfile.lock", "go.sum",
    "gradle.lockfile", "packages.lock.json", "mix.lock", "flake.lock",
    "pubspec.lock", "Podfile.lock",
)

# Widely used as placeholders. Left out of the candidates.
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
        "shaped like a credential; revoke it before worrying about publishing",
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
        "an email address that looks like somebody's",
        re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    ),
    (
        "home-path",
        "a home directory path; usually with a username in it",
        re.compile(r"/home/[A-Za-z0-9._\-]+|/Users/[A-Za-z0-9._\-]+|C:\\\\?Users\\\\?[A-Za-z0-9._\-]+"),
    ),
    (
        "uuid",
        "a UUID; usually the identifier of some resource",
        re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    ),
    (
        "opaque-id",
        "a random identifier of 20 characters or more; the shape of a spreadsheet or folder id",
        re.compile(r"\b[A-Za-z0-9_\-]{20,}\b"),
    ),
    (
        "host",
        "an external hostname; can identify the system involved",
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
    # What was skipped is counted and reported, always. Skip something quietly
    # and the user believes everything was looked at. There being a part that
    # was not looked at is worth nothing unless it reaches the person who did
    # not look at it.
    skipped_paths: set[str] = field(default_factory=set)
    blobs_skipped: int = 0

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.categories.values())


def should_skip(path: str, patterns: tuple[str, ...]) -> bool:
    """Whether a path matches one of the exclusion patterns.

    Patterns are matched against the filename as well as the whole path.
    Writing `package-lock.json` and getting only the one at the root excluded,
    leaving `web/package-lock.json` in, is not what anybody means by it.
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
    # All-caps placeholders, as in EXAMPLE_SPREADSHEET_ID
    if value.isupper() and "_" in value:
        return True
    return False


def _keep(category: str, value: str) -> bool:
    # The placeholder check applies to every category. Inside the branch below
    # it would miss the host form with a subdomain on it (api.example.com).
    if _looks_like_placeholder(value):
        return False

    if category == "host":
        return value not in PLACEHOLDER_HOSTS and value not in COMMON_HOSTS

    if category == "opaque-id":
        # A long run of only letters or only digits is usually an ordinary word
        # or a constant, not an identifier.
        if value.isalpha() or value.isdigit():
            return False
        # Leaves out long camelCase function names: an identifier mixes case
        # and digits.
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
