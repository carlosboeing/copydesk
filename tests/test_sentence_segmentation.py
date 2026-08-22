"""Structural sentence segmentation: list items and commit subjects are their own units.

Issue 22: the splitter only looked for terminal punctuation, so a commit
subject and every bullet after it concatenated into one sentence-length
finding on a message whose longest real sentence was eight words.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import linter  # noqa: E402


ISSUE_22_MESSAGE = """fix(hook): keep the registry, survive set -e, admit a failed strip

- Hook registry is deleted by the session-state sweeper
  lib/hook.py:126 - https://github.com/carlosboeing/copydesk/pull/21/files#r3835783255
- Chained block aborts the commit under set -e
  lib/hook.py:60 - https://github.com/carlosboeing/copydesk/pull/21/files#r3835783289
- Uninstall ignores a failed strip of a chained block
  lib/wizard.py:970 - https://github.com/carlosboeing/copydesk/pull/21/files#r3835783307

Crossrev-pr: carlosboeing/copydesk#21
Crossrev-pass: 1
"""


class SegmentationTests(unittest.TestCase):
    """The splitter must break on structure as well as punctuation."""

    def test_consecutive_list_items_are_separate_units(self) -> None:
        text = (
            "- Hook registry is deleted by the session-state sweeper\n"
            "- Chained block aborts the commit under set -e\n"
            "- Uninstall ignores a failed strip of a chained block\n"
        )
        records = linter._sentence_records(linter.exclude_markdown(text))
        self.assertEqual(len(records), 3)
        self.assertTrue(all(record.words < 25 for record in records))

    def test_consecutive_list_items_produce_no_error_finding(self) -> None:
        text = (
            "# Heading\n\n"
            "- Hook registry is deleted by the session-state sweeper\n"
            "- Chained block aborts the commit under set -e\n"
            "- Uninstall ignores a failed strip of a chained block\n"
        )
        blocking = [f for f in linter.lint(text) if f.severity == "error"]
        self.assertEqual(blocking, [])

    def test_a_list_item_does_not_continue_into_following_prose(self) -> None:
        text = (
            "- The first item ends here without terminal punctuation\n"
            "Plain prose follows the list at column zero.\n"
        )
        records = linter._sentence_records(linter.exclude_markdown(text))
        self.assertEqual(len(records), 2)

    def test_an_indented_continuation_stays_with_its_item(self) -> None:
        text = (
            "- Hook registry is deleted by the session-state sweeper\n"
            "  lib/hook.py:126 - https://example.com/pull/21/files#r3835783255\n"
        )
        records = linter._sentence_records(linter.exclude_markdown(text))
        self.assertEqual(len(records), 1)
        self.assertIn("sweeper", records[0].text)

    def test_three_prose_sentences_split_into_three(self) -> None:
        # The control. Without it the tests above could pass by never splitting.
        text = (
            "The parser assumed at least one token was present. An empty file "
            "reached the index operation and crashed. Return an empty AST instead.\n"
        )
        records = linter._sentence_records(text)
        self.assertEqual(len(records), 3)


class CommitMessageTests(unittest.TestCase):
    def run_commit_msg(self, message: str) -> tuple[int, str]:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".msg", delete=False, encoding="utf-8"
        )
        try:
            handle.write(message)
            handle.close()
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = linter.run_commit_msg(handle.name)
            return code, stderr.getvalue()
        finally:
            os.unlink(handle.name)

    def test_the_reported_commit_message_passes(self) -> None:
        code, stderr = self.run_commit_msg(ISSUE_22_MESSAGE)
        self.assertEqual(code, 0, stderr)

    def test_the_bisection_table_passes_after_the_fix(self) -> None:
        subject = "fix(hook): keep the registry, survive set -e, admit a failed strip"
        bullets = [
            "- Hook registry is deleted by the session-state sweeper\n"
            "  lib/hook.py:126 - https://github.com/carlosboeing/copydesk/pull/21/files#r3835783255",
            "- Chained block aborts the commit under set -e\n"
            "  lib/hook.py:60 - https://github.com/carlosboeing/copydesk/pull/21/files#r3835783289",
            "- Uninstall ignores a failed strip of a chained block\n"
            "  lib/wizard.py:970 - https://github.com/carlosboeing/copydesk/pull/21/files#r3835783307",
        ]
        cases = {
            "one bullet": [bullets[0]],
            "two bullets": bullets[:2],
            "three bullets": bullets,
        }
        for name, chosen in cases.items():
            with self.subTest(case=name):
                message = subject + "\n\n" + "\n".join(chosen) + "\n"
                code, stderr = self.run_commit_msg(message)
                self.assertEqual(code, 0, stderr)

    def test_a_full_stop_on_the_subject_changes_nothing(self) -> None:
        # The bisection's proof that punctuation was all the splitter saw.
        # After the fix the punctuated and unpunctuated subjects behave alike.
        message = ISSUE_22_MESSAGE.replace("failed strip\n", "failed strip.\n", 1)
        code, stderr = self.run_commit_msg(message)
        self.assertEqual(code, 0, stderr)

    def test_a_subject_line_does_not_continue_into_unpunctuated_prose(self) -> None:
        body = (
            "the chained hook block aborts every later check when errexit is set "
            "so the wrapper captures the status before any guard can end the script "
            "and a missing binary on the path still leaves the commit entirely alone"
        )
        message = f"fix(hook): capture the status under set -e\n\n{body}\n"
        code, stderr = self.run_commit_msg(message)
        self.assertEqual(code, 0, stderr)
        # The control: counting subject and body as one unit must refuse it.
        combined = f"capture the status under set -e {body}"
        self.assertGreater(len(combined.split()), 40)

    def test_trailer_lines_do_not_read_as_prose(self) -> None:
        body = (
            "the chained hook block aborts every later check when errexit is set "
            "so the wrapper captures the status before any guard can end it"
        )
        trailers = "\n".join(f"Crossrev-pr: carlosboeing/copydesk#{number}" for number in range(20, 30))
        message = f"fix(hook): capture the status under set -e\n\n{body}\n\n{trailers}\n"
        code, stderr = self.run_commit_msg(message)
        self.assertEqual(code, 0, stderr)

    def test_a_subject_with_five_short_bullets_passes(self) -> None:
        bullets = [
            "- The registry survives the session-state sweeper",
            "- The chained block captures its own status",
            "- The uninstall path strips only its region",
            "- The wizard verifies the block is reached",
            "- The doctor reports a stale output style",
        ]
        message = "fix(hook): harden the commit gate\n\n" + "\n".join(bullets) + "\n"
        code, stderr = self.run_commit_msg(message)
        self.assertEqual(code, 0, stderr)


    def test_a_commit_subject_is_its_own_unit(self) -> None:
        text = (
            "fix(hook): capture the status under set -e\n\n"
            "the chained hook block aborts every later check when errexit is set "
            "so the wrapper captures the status first"
        )
        records = linter._sentence_records(
            linter.exclude_markdown(text), subject_is_own_unit=True
        )
        # The conventional prefix carries a colon, which punctuation-splitting
        # has always broken on; the unit boundary is what keeps the body out.
        self.assertEqual(records[0].text, "capture the status under set -e")
        self.assertTrue(records[1].text.startswith("the chained hook block"))


class CapStillAppliesTests(unittest.TestCase):
    def run_commit_msg(self, message: str) -> tuple[int, str]:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".msg", delete=False, encoding="utf-8"
        )
        try:
            handle.write(message)
            handle.close()
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = linter.run_commit_msg(handle.name)
            return code, stderr.getvalue()
        finally:
            os.unlink(handle.name)

    def test_a_genuine_thirty_word_sentence_still_trips_the_cap(self) -> None:
        sentence = (
            "The gate keeps the registry beside the state directory because a "
            "second spelling of the same file would let the sweeper delete a "
            "record that every installed hook still needs to read."
        )
        findings = [
            f for f in linter.lint(sentence + "\n")
            if f.check == "sentence-length"
        ]
        self.assertTrue(findings)

    def test_a_sentence_over_the_hard_cap_still_blocks_a_commit(self) -> None:
        words = "word " * 44 + "here."
        message = f"fix(parser): handle empty input\n\n{words}\n"
        code, stderr = self.run_commit_msg(message)
        self.assertEqual(code, 1, stderr)
        self.assertIn("sentence-length", stderr)


class WordCountingTests(unittest.TestCase):
    """What counts toward a sentence's word total (decided in issue 22)."""

    def test_a_url_counts_as_one_word(self) -> None:
        text = "See the thread for details about this change right over here now please everyone https://github.com/carlosboeing/copydesk/pull/21/files#r38357832553344556677889900112233445566778899001122334455667788990011223344556677\n"
        records = linter._sentence_records(linter.exclude_markdown(text))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].text.split().count("URL"), 1)

    def test_a_long_url_does_not_inflate_a_short_sentence_past_the_cap(self) -> None:
        url = "https://example.com/" + "a" * 200
        text = f"The registry keeps one entry per installed hook today always {url}\n"
        blocking = [f for f in linter.lint(text) if f.severity == "error"]
        self.assertEqual(blocking, [])

    def test_a_file_path_counts_as_one_word(self) -> None:
        text = "See lib/hook.py:126 for the sweeper.\n"
        records = linter._sentence_records(linter.exclude_markdown(text))
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].text.split()), 5)


if __name__ == "__main__":
    unittest.main()
