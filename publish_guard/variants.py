"""Enumerate the other ways the same value gets written.

Searching for the plain string alone misses things. These are the ones that
were actually hit.

  A non-ASCII sheet name arrives percent-encoded inside a Sheets API URL, and
  a test's expected value had been written in that form. Replacing the plain
  string left the expectation untouched, and the first anyone knew of it was a
  failing test.

  A filename had its dots escaped inside a regex literal. Replacing
  "automation.once.json" left "automation\\.once\\.json" behind.

  An organisation name turned up in upper case and in camel case too —
  ACMECORP, AcmeCorp and acmecorp — and only the lower-case one had been
  removed.

  Non-ASCII text written out by json.dumps with ensure_ascii becomes \\uXXXX.
  Opening the file and reading it will not find it.

This module derives all of those from one term, mechanically. It is here so
that nothing depends on a person remembering them.

Some of it cannot be derived. AcmeCorp does not follow from acmecorp, because
there is no telling where the word boundaries are — if camel case is a
possibility, both belong in the config. That is a limit, stated as one.
"""

from __future__ import annotations

import json
import urllib.parse


def _percent(value: str, *, upper: bool) -> str:
    encoded = urllib.parse.quote(value, safe="")
    return encoded if upper else encoded.lower()


def _json_escaped(value: str) -> str:
    # json.dumps with ensure_ascii=True turns non-ASCII into \uXXXX. The
    # surrounding quotes come off; only the contents are wanted.
    return json.dumps(value, ensure_ascii=True)[1:-1]


def _regex_escaped(value: str) -> str:
    # Inside a regex literal, . is written \. re.escape would flatten the
    # other punctuation too and drift from how things are actually written,
    # so this covers the dot and nothing else.
    return value.replace(".", "\\.")


def variants(term: str) -> list[str]:
    """Every way term might appear, longest first.

    Longest first so that using these for replacement cannot break on a
    partial match. Without handling "average monthly revenue" before
    "revenue", replacing the shorter one leaves a mangled remainder.
    """
    found: set[str] = {term}

    has_non_ascii = any(ord(c) > 127 for c in term)

    if has_non_ascii:
        found.add(_percent(term, upper=True))
        found.add(_percent(term, upper=False))
        found.add(_json_escaped(term))
    else:
        # An ASCII term shows up with the case shifted around.
        found.add(term.upper())
        found.add(term.lower())
        if term:
            found.add(term[0].upper() + term[1:])

    if "." in term:
        found.add(_regex_escaped(term))
        # An ASCII term with a dot can also appear escaped and recased.
        if not has_non_ascii:
            found.add(_regex_escaped(term.lower()))

    # An empty string is not something to search for.
    found.discard("")
    return sorted(found, key=len, reverse=True)


def expand(terms: list[str]) -> list[str]:
    """Expand several terms at once, deduplicated and longest first."""
    seen: set[str] = set()
    for term in terms:
        seen.update(variants(term))
    seen.discard("")
    return sorted(seen, key=len, reverse=True)
