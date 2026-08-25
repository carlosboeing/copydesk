"""The chat gate must not refuse a turn over one long-ish sentence.

`lib/linter.py` promotes any sentence past `hardMax` to `error` whatever
severity the preset declares. A chat reply is a whole turn, so that promotion
refuses the turn and the operator reads the refused answer and its replacement
side by side. A style a terse chat channel selects must therefore keep both
triggers at or above the plain preset's 40-word hard cap.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import linter  # noqa: E402

# Styles a chat channel selects when it wants short replies. `general` is
# absent on purpose: it declares `sentence-length` as `error` at 20 words, so
# it blocks by its own declaration rather than by the hard-cap promotion.
TERSE_CHAT_STYLES = ("plain", "engineer", "editorial")

_STEM = (
    "The gate reads the reply, counts the words in each sentence, compares "
    "that count against the style in force, and refuses the turn when a "
    "single sentence runs past the hard cap set by the preset"
)
SENTENCE_39_WORDS = _STEM + " for one turn."
SENTENCE_41_WORDS = _STEM + " for one turn each time."


class ChatSentenceLengthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        saved = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self._home.name

        def restore() -> None:
            if saved is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = saved

        self.addCleanup(restore)

    def _chat_style(self, style: str) -> None:
        """Point the chat channel at one style through the user config."""
        directory = Path(self._home.name) / "copydesk"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.json").write_text(
            json.dumps({"version": 1, "channels": {"chat": {"style": style}}}),
            encoding="utf-8",
        )

    def _sentence_length_severities(self, text: str) -> list[str]:
        return [f.severity for f in linter.lint(text, channel="chat") if f.check == "sentence-length"]

    def test_the_fixtures_are_the_lengths_they_claim(self) -> None:
        """The control: every other assertion here rests on these two counts."""
        self.assertEqual(linter._sentence_records(SENTENCE_39_WORDS)[0].words, 39)
        self.assertEqual(linter._sentence_records(SENTENCE_41_WORDS)[0].words, 41)

    def test_no_terse_chat_style_blocks_a_sentence_under_forty_words(self) -> None:
        for style in TERSE_CHAT_STYLES:
            with self.subTest(style=style):
                self._chat_style(style)
                self.assertNotIn("error", self._sentence_length_severities(SENTENCE_39_WORDS))

    def test_a_terse_chat_style_still_reports_a_sentence_under_forty_words(self) -> None:
        """Not blocking is not the same as not asking. The warning stays."""
        self._chat_style("engineer")
        self.assertIn("warning", self._sentence_length_severities(SENTENCE_39_WORDS))

    def test_the_forty_word_hard_cap_still_blocks(self) -> None:
        """The control for the scan above: the promotion is intact past 40."""
        for style in TERSE_CHAT_STYLES:
            with self.subTest(style=style):
                self._chat_style(style)
                self.assertIn("error", self._sentence_length_severities(SENTENCE_41_WORDS))

    def test_every_terse_chat_style_declares_the_same_hard_cap(self) -> None:
        """A cheap guard on the preset files, ahead of the behaviour above."""
        for style in TERSE_CHAT_STYLES:
            with self.subTest(style=style):
                self._chat_style(style)
                preset, _ = linter.effective_preset(channel="chat")
                self.assertNotEqual(linter._rule_severity(preset, "sentence-length", "warning"), "error")
                self.assertGreaterEqual(linter._rule_number(preset, "sentence-length", "hardMax", 0.0), 40)


if __name__ == "__main__":
    unittest.main()
