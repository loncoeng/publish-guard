# publish-guard

[![test](https://github.com/lon-coeng/publish-guard/actions/workflows/test.yml/badge.svg)](https://github.com/lon-coeng/publish-guard/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/publish-guard?color=1C4E93&label=PyPI)](https://pypi.org/project/publish-guard/)

*[日本語版 / Japanese version](README.ja.md)*

Find what you forgot to remove before you publish a repository — **including the parts that are only in the history.**

Built after publishing two repositories derived from client work, and discovering
three separate ways the sanitization had failed. Each of those is now a feature.

---

## The problem

You built something under contract. You want to show it. So you replace the client's
name, the credentials, the resource IDs — and publish.

Three things go wrong, and none of them are visible when you look at the files.

**Editing a file does not remove what it contained.** Every earlier commit still holds
the original. `git log -p` prints it. Anyone who clones gets it.

**The same value appears in more than one form.** A Japanese sheet name is
percent-encoded inside a URL. A filename has its dots escaped inside a regex literal.
An organisation name shows up as `ACME`, `Acme` and `acme`. Non-ASCII text written by
`json.dumps` becomes `\uXXXX` and is invisible to the eye. Replacing the plain string
leaves every one of these behind.

**`git push --force` does not delete anything on GitHub.** The commits become
unreachable, but the objects stay, and a direct SHA still fetches them — while the
Actions run history publishes those SHAs for anyone to read.

## What this does

```
publish-guard scan   <repo>                    surface candidates you may have missed
publish-guard verify <repo> -c <config.toml>   confirm the terms you chose are really gone
```

`verify` walks **every blob in every commit**, not the working tree. It expands each
term into the forms it might actually appear as. It checks commit messages and author
metadata too. It exits `1` when anything is found, so it works in CI.

`scan` does not decide anything. Whether `売上高` is a client-specific metric or a
generic word is a judgement only someone who knows the project can make. What a machine
does better is spotting a 40-character opaque identifier or a username buried in a home
directory path — the things that survive a careful read.

## Install

```sh
pip install publish-guard
```

Python 3.11 or later. No dependencies — `tomllib` landed in the standard library at 3.11.

```sh
publish-guard scan /path/to/repo
```

To run it straight from a clone instead:

```sh
git clone https://github.com/lon-coeng/publish-guard.git
cd publish-guard
python -m publish_guard.cli --help
```

## Use

**1. Find candidates.**

```sh
python -m publish_guard.cli scan /path/to/repo
```

```
[opaque-id] identifiers 20 characters or longer
  1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo
    config/settings.json, docs/setup.md

[home-path] home directory paths, often containing a username
  /home/acme-operator  (history only)
    systemd/worker.service
```

**2. Decide what to remove, and record the decision.**

```toml
# publish-guard.toml
[forbidden]
terms = [
  "AcmeCorp",
  "1Kx9mQ2vTpL7rB4nW8sJfD6yHcE3aZgUo",
  "/home/acme-operator",
  "売上高",
]
```

Write the plain string. The other forms are derived for you.

**3. Remove them however you like, then confirm.**

```sh
python -m publish_guard.cli verify /path/to/repo -c publish-guard.toml
```

```
scanned: 42 commits / 310 blobs / 4 terms

found: 3 results / 1 term

  売上高
    %E5%A3%B2%E4%B8%8A%E9%AB%98  ← other form
      test/import-verification.test.js  (still present)

Some results are history only. Editing the file will not remove them.
git push --force does not remove the objects on GitHub either.
To be certain, recreate the repository.
```

## In CI

```yaml
- name: publish-guard
  run: python -m publish_guard.cli verify . -c publish-guard.toml
```

The point is not to catch it once. It is to keep catching it after you have stopped
thinking about it.

## What it does not do

**It does not replace anything.** Removal is a judgement call, and a tool that guessed
would guess wrong in the direction of leaking. Remove things your own way — this
confirms the result.

**It cannot tell you what is sensitive.** `scan` offers candidates by shape. Only you
know which of them matter.

**`verify` has no exclusions.** `--exclude` belongs to `scan`, where leaving a file out
costs you nothing but the quality of a suggestion. `verify` is the gate that stops a
publish, and a gate with a bypass is not a gate. Lockfiles are no exception there:
their contents are mostly checksums, but a private registry package name like
`@acmecorp/internal-ui`, or an internal mirror URL, lands in them — **as ways a client
gets identified go, that one is fairly typical.**

**It cannot clean history for you.** If something is only in the history, no command
here fixes that. Recreating the repository is the reliable answer, and `verify` tells
you when you need to.

## Options

| | |
|---|---|
| `--no-history` | Scan only the HEAD tree. Fast, and misses exactly what this tool exists to find. For a pre-commit look, not a pre-publish check. |
| `--current-branch` | Walk only the current branch's history instead of every ref. Note this still walks ancestors — it is about refs, not depth. |
| `--limit N` | How many candidates to print per category in `scan` (default 20). |
| `--exclude GLOB` | Leave a path out of `scan`. Repeatable. Matched against the file name as well as the full path, so `package-lock.json` also covers `web/package-lock.json`. |
| `--include-lockfiles` | Scan dependency lockfiles too. They are left out by default. |

## Exit codes

| | |
|---|---|
| `0` | Nothing found |
| `1` | Something found |
| `2` | Not a git repository, or a bad configuration |

## Tests

```sh
python -m unittest discover -s tests -t .
```

No dependencies required. The tests build real git repositories in a temporary
directory and verify the behaviour that matters: that a value removed by a later
commit is still found, and that every derived form is caught.

## A note from writing this

The first version of this README used real values as examples — a real resource
identifier, a real internal term — on the reasoning that examples from experience
beat invented ones. They went into the README and the tests of a tool built to find
exactly that. I caught it and rebuilt the repository before anyone had cloned it.

The part worth writing down comes after. `scan` had already flagged one of them. It
printed the identifier as a candidate. I read that output and decided it was fine,
because it was "just a test fixture."

The tool worked. The person reading its output was the one who got it wrong. That is
why `verify` exits non-zero and belongs in CI — not because you will forget, but
because you will see the warning and talk yourself out of it.

Later, running `scan` over a JavaScript repository before publishing it returned 197
candidates. 155 of them were integrity hashes out of `package-lock.json`. I did not
read the list; I audited that repository by hand instead. **A finding you cannot find
is not much better than a miss.** That is when lockfiles became a default exclusion.
The same repository now reports one candidate, and it is one worth a decision.

Whatever gets excluded is printed back with a count and the file names. Skipping
quietly would only trade one problem for another: believing you looked at everything.

## License

MIT. See [LICENSE](LICENSE).
