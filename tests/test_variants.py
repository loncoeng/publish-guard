"""Tests for expanding a term into its other forms.

Break this and you are back to removing the plain string and feeling safe.
The cases that actually got missed are here as they happened.

The non-ASCII literals below are inputs, not prose. Percent-encoding and
\\uXXXX escaping only happen to non-ASCII text, so a non-ASCII term is the
only thing that exercises them, and the encoded forms are written out by hand
— computing them here would be asking the implementation to agree with itself.
"""

from __future__ import annotations

import unittest

from publish_guard.variants import expand, variants

TERM = "取引先"
TERM_PERCENT_UPPER = "%E5%8F%96%E5%BC%95%E5%85%88"
TERM_PERCENT_LOWER = "%e5%8f%96%e5%bc%95%e5%85%88"
TERM_JSON_ESCAPED = "\\u53d6\\u5f15\\u5148"


class NonAsciiTest(unittest.TestCase):
    def test_the_percent_encoded_form_is_included(self):
        # This is the form a non-ASCII sheet name takes inside a Sheets API
        # URL. A test's expected value was written that way, and replacing
        # the plain string left it behind.
        self.assertIn(TERM_PERCENT_UPPER, variants(TERM))

    def test_the_lower_case_percent_encoded_form_too(self):
        # Which of %E5 and %e5 you get depends on who did the encoding.
        self.assertIn(TERM_PERCENT_LOWER, variants(TERM))

    def test_the_json_unicode_escape_is_included(self):
        # In JSON written with ensure_ascii, reading the file will not find it.
        self.assertIn(TERM_JSON_ESCAPED, variants(TERM))

    def test_the_term_itself_is_included(self):
        self.assertIn(TERM, variants(TERM))

    def test_no_case_variants_are_made_for_non_ascii(self):
        # Pointless candidates only make the scan slower.
        self.assertEqual(len([v for v in variants(TERM) if v == TERM]), 1)


class AsciiTest(unittest.TestCase):
    def test_case_variants_are_included(self):
        # A company name turns up as ACMECORP in an environment variable and
        # as Acmecorp in a comment.
        got = variants("acmecorp")
        for expected in ("acmecorp", "ACMECORP", "Acmecorp"):
            self.assertIn(expected, got)

    def test_internal_capitals_cannot_be_recovered(self):
        # AcmeCorp does not follow from acmecorp, because there is no telling
        # where the word boundary is. If camel case is a possibility, both
        # belong in the config.
        self.assertNotIn("AcmeCorp", variants("acmecorp"))

    def test_no_percent_encoding_is_made_for_ascii(self):
        # ASCII survives encoding unchanged, so there is no candidate to add.
        self.assertNotIn("%61%63%6d%65%63%6f%72%70", variants("acmecorp"))


class RegexEscapeTest(unittest.TestCase):
    def test_the_dot_escaped_form_is_included(self):
        # This is how it is written inside a regex literal.
        self.assertIn("automation\\.once\\.json", variants("automation.once.json"))

    def test_nothing_is_escaped_when_there_is_no_dot(self):
        self.assertNotIn("acme\\.", variants("acme"))

    def test_hostnames_get_an_escaped_form_too(self):
        self.assertIn("admin\\.acme\\.example", variants("admin.acme.example"))


class OrderingTest(unittest.TestCase):
    def test_longest_first(self):
        got = variants(TERM)
        self.assertEqual(got, sorted(got, key=len, reverse=True))

    def test_expand_is_longest_first_too(self):
        # Used for replacement, taking the short term first mangles the long
        # one: "主要取引先" has to be handled before "取引先".
        got = expand([TERM, "主要" + TERM])
        self.assertEqual(got, sorted(got, key=len, reverse=True))

    def test_expand_deduplicates(self):
        got = expand(["acme", "acme"])
        self.assertEqual(len(got), len(set(got)))


class EdgeCaseTest(unittest.TestCase):
    def test_the_empty_string_is_not_a_candidate(self):
        self.assertNotIn("", variants(""))
        self.assertNotIn("", expand(["", "acme"]))

    def test_a_term_mixing_ascii_and_non_ascii_works(self):
        got = variants("Acme" + TERM)
        self.assertIn("Acme" + TERM, got)
        self.assertTrue(any(v.startswith("%") or "\\u" in v for v in got))


if __name__ == "__main__":
    unittest.main()
