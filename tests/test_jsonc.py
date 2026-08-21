"""Comments are stripped before parsing, and positions survive."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import jsonc  # noqa: E402


class StripTests(unittest.TestCase):
    def test_a_line_comment_is_removed(self) -> None:
        text = '{\n  "version": 1  // the schema version\n}'
        self.assertEqual(json.loads(jsonc.strip_comments(text)), {"version": 1})

    def test_a_block_comment_is_removed(self) -> None:
        text = '{\n  /* two\n     lines */\n  "version": 1\n}'
        self.assertEqual(json.loads(jsonc.strip_comments(text)), {"version": 1})

    def test_a_double_slash_inside_a_string_survives(self) -> None:
        text = '{"version": 1, "home": "https://example.com/x"}'
        self.assertEqual(
            json.loads(jsonc.strip_comments(text))["home"], "https://example.com/x"
        )

    def test_an_escaped_quote_does_not_end_the_string(self) -> None:
        text = '{"version": 1, "note": "a \\" then // not a comment"}'
        self.assertEqual(
            json.loads(jsonc.strip_comments(text))["note"], 'a " then // not a comment'
        )

    def test_an_unterminated_block_comment_is_refused(self) -> None:
        with self.assertRaises(jsonc.UnterminatedComment):
            jsonc.strip_comments('{"version": 1} /*')

    def test_an_unterminated_string_is_refused(self) -> None:
        with self.assertRaises(jsonc.UnterminatedComment):
            jsonc.strip_comments('{"version": "unclosed')

    def test_a_closed_block_comment_at_the_end_is_fine(self) -> None:
        self.assertEqual(json.loads(jsonc.strip_comments('{"version": 1} /* done */')), {"version": 1})

    def test_line_and_column_positions_are_preserved(self) -> None:
        text = '{\n  // a comment\n  "version": oops\n}'
        stripped = jsonc.strip_comments(text)
        self.assertEqual(len(stripped), len(text))
        with self.assertRaises(json.JSONDecodeError) as caught:
            json.loads(stripped)
        self.assertEqual(caught.exception.lineno, 3)


if __name__ == "__main__":
    unittest.main()
