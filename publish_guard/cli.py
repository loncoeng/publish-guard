"""publish-guard のコマンドライン。

  publish-guard scan   <repo>                   候補を洗い出す
  publish-guard verify <repo> --config <file>   禁止語が消えたか検証する

verify は見つかったら exit 1 を返す。CI に置いて、公開前に人間が
忘れても止まるようにするための仕様である。
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from . import gitrepo, scan as scan_mod
from .verify import verify as run_verify


class ConfigError(Exception):
    """設定ファイルが読めない。利用者に見せる文言をそのまま持つ。"""


def _load_terms(config_path: Path) -> list[str]:
    if not config_path.exists():
        raise ConfigError(
            f"設定ファイルが見つかりません: {config_path}\n"
            "examples/publish-guard.toml を複製して使ってください。"
        )
    try:
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"設定ファイルの書式が壊れています: {config_path}\n{error}") from error
    except OSError as error:
        raise ConfigError(f"設定ファイルを読めません: {config_path}\n{error}") from error

    terms = data.get("forbidden", {}).get("terms", [])
    if not isinstance(terms, list) or not all(isinstance(t, str) for t in terms):
        raise ConfigError("[forbidden] terms は文字列の配列にしてください")
    return [t for t in terms if t]


def _cmd_verify(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not gitrepo.is_git_repository(repo):
        print(f"{repo} は git リポジトリではありません", file=sys.stderr)
        return 2

    try:
        terms = _load_terms(Path(args.config))
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    if not terms:
        print("禁止語が設定されていません", file=sys.stderr)
        return 2

    report = run_verify(repo, terms, all_refs=not args.current_branch, history=not args.no_history)

    print(f"走査: {report.commits_scanned} コミット / {report.blobs_scanned} blob / 禁止語 {len(terms)} 件")

    if report.ok:
        print("検出なし。設定した語は履歴のどこにも残っていません。")
        return 0

    grouped = report.by_term()
    print(f"\n検出: {len(report.findings)} 件 / {len(grouped)} 語\n")
    for term, findings in sorted(grouped.items()):
        print(f"  {term}")
        shown = {}
        for f in findings:
            shown.setdefault(f.matched, []).append(f)
        for matched, items in shown.items():
            note = "" if matched == term else f"  ← 別表記"
            print(f"    {matched}{note}")
            for item in items[:5]:
                mark = "履歴のみ" if item.in_history_only else "現在も存在"
                print(f"      {item.location}  ({mark})")
            if len(items) > 5:
                print(f"      ... 他 {len(items) - 5} 箇所")
        print()

    if any(f.in_history_only for f in report.findings):
        print("「履歴のみ」の検出があります。ファイルを編集しても消えません。")
        print("git push --force でも GitHub 側のオブジェクトは残ります。")
        print("確実に消すには、リポジトリを作り直してください。")

    return 1


def _cmd_scan(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if not gitrepo.is_git_repository(repo):
        print(f"{repo} は git リポジトリではありません", file=sys.stderr)
        return 2

    report = scan_mod.scan(repo, all_refs=not args.current_branch, history=not args.no_history)
    print(f"走査: {report.commits_scanned} コミット / {report.blobs_scanned} blob")

    if report.total == 0:
        print("候補は見つかりませんでした。")
        return 0

    print(f"\n候補: {report.total} 件\n")
    order = [name for name, _, _ in scan_mod.RULES]
    for category in order:
        bucket = report.categories.get(category)
        if not bucket:
            continue
        print(f"[{category}] {scan_mod.describe(category)}")
        for value in sorted(bucket)[: args.limit]:
            candidate = bucket[value]
            where = sorted(candidate.locations)[:3]
            mark = " (履歴のみ)" if candidate.history_only else ""
            print(f"  {value}{mark}")
            print(f"    {', '.join(where)}" + (" ..." if len(candidate.locations) > 3 else ""))
        if len(bucket) > args.limit:
            print(f"  ... 他 {len(bucket) - args.limit} 件 (--limit で調整)")
        print()

    print("これは判定ではなく候補です。消すかどうかは中身を見て決めてください。")
    print("消すと決めたら publish-guard.toml に書き、verify で確認します。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish-guard",
        description="公開前のリポジトリから、委託元を特定しうる値を洗い出し、消えたことを検証する",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="消し忘れそうな値の候補を洗い出す")
    p_scan.add_argument("repo", nargs="?", default=".", help="対象リポジトリ (既定: カレント)")
    p_scan.add_argument("--no-history", action="store_true",
                        help="HEAD の tree だけを走査する。速いが履歴に残ったものは見つからない")
    p_scan.add_argument("--current-branch", action="store_true",
                        help="全 ref ではなく現在のブランチの履歴だけを走査する")
    p_scan.add_argument("--limit", type=int, default=20, help="カテゴリごとの表示件数 (既定: 20)")
    p_scan.set_defaults(func=_cmd_scan)

    p_verify = sub.add_parser("verify", help="禁止語が履歴を含めて消えたか検証する")
    p_verify.add_argument("repo", nargs="?", default=".", help="対象リポジトリ (既定: カレント)")
    p_verify.add_argument("-c", "--config", default="publish-guard.toml", help="設定ファイル")
    p_verify.add_argument("--no-history", action="store_true",
                          help="HEAD の tree だけを走査する。公開前の最終確認では使わないこと")
    p_verify.add_argument("--current-branch", action="store_true",
                          help="全 ref ではなく現在のブランチの履歴だけを走査する")
    p_verify.set_defaults(func=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
