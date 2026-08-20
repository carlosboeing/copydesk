"""Instructions are generated, budgeted, and free of token lists."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import instructions  # noqa: E402

PRESET = json.loads((ROOT / "rules" / "plain.json").read_text(encoding="utf-8"))


def resolved(**overrides) -> dict:
    base = {
        "channels": {
            "chat": {
                "enabled": True,
                "style": "plain",
                "verbosity": "low",
                "guidance": {"recommendations": True, "direction": True, "progress": True},
            }
        },
        "preset": PRESET,
    }
    base.update(overrides)
    return base


class ChatBudgetTests(unittest.TestCase):
    def test_the_chat_block_fits_its_budget(self) -> None:
        words = instructions.word_count(instructions.render_chat(resolved()))
        self.assertLessEqual(words, instructions.BUDGETS["chat"])

    def test_the_budget_is_the_designed_one(self) -> None:
        self.assertEqual(instructions.BUDGETS["chat"], 220)

    def test_no_banned_word_token_list_reaches_the_chat_block(self) -> None:
        rendered = instructions.render_chat(resolved())
        tokens = [t for b in PRESET["patterns"] if b["id"] == "banned-word" for t in b["tokens"]]
        leaked = [t for t in tokens if isinstance(t, str) and t.lower() in rendered.lower()]
        self.assertEqual(leaked, [], f"token list leaked into the chat block: {leaked}")

    def test_the_categories_replace_the_lists_inside_sixty_words(self) -> None:
        categories = PRESET["instructions"]["categories"]
        self.assertLessEqual(instructions.word_count(categories), 60)

    def test_the_floor_is_present_under_every_style(self) -> None:
        for style in ("plain", "general", "engineer", "editorial"):
            config = resolved()
            config["channels"]["chat"]["style"] = style
            rendered = instructions.render_chat(config).lower()
            self.assertIn("answer first", rendered)
            self.assertIn("once", rendered)

    def test_each_style_changes_the_rendered_block(self) -> None:
        seen = set()
        for style in ("plain", "general", "engineer", "editorial"):
            config = resolved()
            config["channels"]["chat"]["style"] = style
            seen.add(instructions.render_chat(config))
        self.assertEqual(len(seen), 4, "a style that renders identical text is not a style")

    def test_every_channel_and_style_pair_has_a_line(self) -> None:
        for channel in ("chat", "documents", "commits", "reviews"):
            for style in ("plain", "general", "engineer", "editorial"):
                self.assertTrue(instructions.style_line(channel, style), f"{channel}/{style}")

    def test_the_alias_reaches_the_same_line(self) -> None:
        self.assertEqual(
            instructions.style_line("chat", "plain-english"), instructions.style_line("chat", "plain")
        )

    def test_guidance_reaches_the_block_merged(self) -> None:
        rendered = instructions.render_chat(resolved())
        self.assertIn("step 3 of 5", rendered)
        self.assertNotIn("Never list the work already performed", rendered)

    def test_turning_guidance_off_shortens_the_block(self) -> None:
        off = resolved()
        off["channels"]["chat"]["guidance"] = {}
        self.assertLess(
            instructions.word_count(instructions.render_chat(off)),
            instructions.word_count(instructions.render_chat(resolved())),
        )
