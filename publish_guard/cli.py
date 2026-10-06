"""publish-guard's command line.

  publish-guard scan   <repo>                   turn up candidates
  publish-guard verify <repo> --config <file>   check the terms are gone

verify exits 1 when it finds something. That is the point of it: put it in CI
and publishing stops even when the person forgets.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from . import gitrepo, scan as scan_mod
from .verify import verify as run_verify


class ConfigError(Exception):
    """The config file cannot be read. Carries the wording shown to the user."""


def _load_terms(config_path: Path) -> list[str]:
    if not config_path.exists():
        raise ConfigError(
            f"no config file at {config_path}\n"
            "Copy examples/publish-guard.toml and work from that."
        )
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"the config file is malformed: {config_path}\n{error}") from error
    except OSError as error:
        raise ConfigError(f"cannot read the config file: {config_path}\n{error}") from error

    terms = data.get("forbidden", {}).get("terms", [])
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        raise ConfigError("[forbidden] terms must be an array of strings")
    return [t for t in terms if t]


def _cmd_verify(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not gitrepo.is_git_repository(repo):
        print(f"{repo} is not a git repository", file=sys.stderr)
        return 2

    try:
        terms = _load_terms(Path(args.config))
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    if not terms:
        print("no forbidden terms are configured", file=sys.stderr)
        return 2

    report = run_verify(repo, terms, all_refs=not args.current_branch, history=not args.no_history)

    print(
        f"scanned {report.commits_scanned} commits, {report.blobs_scanned} blobs, "
        f"against {len(terms)} terms"
    )

    if report.ok:
        print("Nothing found. None of the configured terms is anywhere in the history.")
        return 0

    grouped = report.by_term()
    print(f"\nFound {len(report.findings)} occurrences of {len(grouped)} terms\n")
    for term, findings in sorted(grouped.items()):
        print(f"  {term}")
        shown = {}
        for f in findings:
            shown.setdefault(f.matched, []).append(f)
        for matched, items in shown.items():
            note = "" if matched == term else f"  ← written differently"
            print(f"    {matched}{note}")
            for item in items[:5]:
                mark = "history only" if item.in_history_only else "still present"
                print(f"      {item.location}  ({mark})")
            if len(items) > 5:
                print(f"      ... and {len(items) - 5} more")
        print()

    if any(f.in_history_only for f in report.findings):
        print("Some of these are in the history only. Editing the file will not remove them.")
        print("Nor will git push --force: the objects stay on GitHub's side.")
        print("To be certain they are gone, recreate the repository.")

    return 1


def _cmd_scan(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not gitrepo.is_git_repository(repo):
        print(f"{repo} is not a git repository", file=sys.stderr)
        return 2

    report = scan_mod.scan(
        repo,
        all_refs=not args.current_branch,
        history=not args.no_history,
        exclude=tuple(args.exclude or ()),
        skip_lockfiles=not args.include_lockfiles,
    )
    print(f"scanned {report.commits_scanned} commits, {report.blobs_scanned} blobs")

    # Say what was skipped, always. Being believed to have looked at everything
    # is the more dangerous outcome.
    if report.blobs_skipped:
        names = sorted(report.skipped_paths)
        shown = ", ".join(names[:3]) + (" ..." if len(names) > 3 else "")
        print(f"skipped {report.blobs_skipped} blobs ({shown})")
        print("      This does not apply to verify: skipped files are still checked for terms.")

    if report.total == 0:
        print("No candidates found.")
        return 0

    print(f"\n{report.total} candidates\n")
    order = [name for name, _, _ in scan_mod.RULES]
    for category in order:
        bucket = report.categories.get(category)
        if not bucket:
            continue
        print(f"[{category}] {scan_mod.describe(category)}")
        for value in sorted(bucket)[: args.limit]:
            candidate = bucket[value]
            where = sorted(candidate.locations)[:3]
            mark = " (history only)" if candidate.history_only else ""
            print(f"  {value}{mark}")
            print(f"    {', '.join(where)}" + (" ..." if len(candidate.locations) > 3 else ""))
        if len(bucket) > args.limit:
            print(f"  ... and {len(bucket) - args.limit} more (--limit to see them)")
        print()

    print("These are candidates, not a verdict. Look at each one before deciding.")
    print("Once you have decided, put it in publish-guard.toml and confirm with verify.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish-guard",
        description="Turn up values that could identify a client in a repository "
                    "about to be published, and verify they are gone",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="turn up values you might have forgotten to remove")
    p_scan.add_argument("repo", nargs="?", default=".", help="the repository (default: the current directory)")
    p_scan.add_argument("--no-history", action="store_true",
                        help="scan only HEAD's tree; faster, but finds nothing left in the history")
    p_scan.add_argument("--current-branch", action="store_true",
                        help="scan the current branch's history rather than every ref")
    p_scan.add_argument("--limit", type=int, default=20, help="how many to show per category (default: 20)")
    p_scan.add_argument("--exclude", action="append", metavar="GLOB",
                        help="paths to leave out; repeatable, and matches both the filename and the whole path")
    p_scan.add_argument("--include-lockfiles", action="store_true",
                        help="also scan dependency lockfiles, which are left out by default")
    p_scan.set_defaults(func=_cmd_scan)

    p_verify = sub.add_parser("verify", help="check the forbidden terms are gone, history included")
    p_verify.add_argument("repo", nargs="?", default=".", help="the repository (default: the current directory)")
    p_verify.add_argument("-c", "--config", default="publish-guard.toml", help="the config file")
    p_verify.add_argument("--no-history", action="store_true",
                          help="scan only HEAD's tree; not for the last check before publishing")
    p_verify.add_argument("--current-branch", action="store_true",
                          help="scan the current branch's history rather than every ref")
    p_verify.set_defaults(func=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
