import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import instructions  # noqa: E402

PRESET = json.loads((ROOT / "rules" / "plain.json").read_text(encoding="utf-8"))


def run_cli(args: list, env: dict) -> int:
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.run(
        [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
        capture_output=True, env=merged, text=True,
    ).returncode


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


class VerbosityTests(unittest.TestCase):
    def test_the_environment_variable_wins(self) -> None:
        self.assertEqual(
            instructions.resolve_verbosity(resolved(), {"COPYDESK_VERBOSITY": "high"}), "high"
        )

    def test_the_config_is_the_resting_default(self) -> None:
        self.assertEqual(instructions.resolve_verbosity(resolved(), {}), "low")

    def test_an_invalid_environment_value_falls_back_to_the_config(self) -> None:
        self.assertEqual(
            instructions.resolve_verbosity(resolved(), {"COPYDESK_VERBOSITY": "loud"}), "low"
        )

    def test_three_output_styles_are_named(self) -> None:
        self.assertEqual(
            instructions.OUTPUT_STYLE_NAMES, ("CopyDesk low", "CopyDesk medium", "CopyDesk high")
        )

    def test_each_output_style_renders_its_own_verbosity_line(self) -> None:
        for level in ("low", "medium", "high"):
            config = resolved()
            config["channels"]["chat"]["verbosity"] = level
            self.assertIn(
                instructions._VERBOSITY_LINES[level], instructions.render_chat(config)
            )


class SetCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_set_writes_the_user_config(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.home)}
        code = run_cli(["set", "channels.chat.verbosity=medium"], env=env)
        self.assertEqual(code, 0)
        body = json.loads((self.home / "copydesk" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(body["channels"]["chat"]["verbosity"], "medium")

    def test_set_refuses_a_key_it_does_not_own(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.home)}
        self.assertEqual(run_cli(["set", "gate.retries=5"], env=env), 64)

    def test_set_refuses_an_invalid_value(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.home)}
        self.assertEqual(run_cli(["set", "channels.chat.verbosity=loud"], env=env), 64)

    def test_set_keeps_the_comments_the_wizard_wrote(self) -> None:
        path = self.home / "copydesk" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{\n  "version": 1,\n  "channels": {\n'
            '    "chat": { "verbosity": "low" },   // how much chat says\n'
            '    "documents": { "verbosity": "high" }\n  }\n}\n',
            encoding="utf-8",
        )
        run_cli(["set", "channels.chat.verbosity=medium"], env={"XDG_CONFIG_HOME": str(self.home)})
        body = path.read_text(encoding="utf-8")
        self.assertIn("// how much chat says", body)
        self.assertIn('"verbosity": "medium"', body)
        self.assertIn('"documents": { "verbosity": "high" }', body)

    def test_a_reordered_config_changes_the_right_channel(self) -> None:
        path = self.home / "copydesk" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{\n  "version": 1,\n  "channels": {\n'
            '    "documents": { "verbosity": "high" },\n'
            '    "commits": { "verbosity": "low" },\n'
            '    "reviews": { "verbosity": "medium" },\n'
            '    "chat": { "verbosity": "low" }\n  }\n}\n',
            encoding="utf-8",
        )
        run_cli(["set", "channels.chat.verbosity=high"], env={"XDG_CONFIG_HOME": str(self.home)})
        body = json.loads(path.read_text(encoding="utf-8"))["channels"]
        self.assertEqual(body["chat"]["verbosity"], "high")
        self.assertEqual(body["documents"]["verbosity"], "high")
        self.assertEqual(body["commits"]["verbosity"], "low")
        self.assertEqual(body["reviews"]["verbosity"], "medium")

    def test_a_nested_key_of_the_same_name_is_not_the_target(self) -> None:
        path = self.home / "copydesk" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"version": 1, "channels": {"chat": '
            '{"guidance": {"verbosity": true}, "verbosity": "low"}}}',
            encoding="utf-8",
        )
        run_cli(["set", "channels.chat.verbosity=high"], env={"XDG_CONFIG_HOME": str(self.home)})
        chat = json.loads(path.read_text(encoding="utf-8"))["channels"]["chat"]
        self.assertEqual(chat["verbosity"], "high")
        self.assertIs(chat["guidance"]["verbosity"], True)

    def test_a_failed_write_leaves_the_old_file_intact(self) -> None:
        path = self.home / "copydesk" / "config.json"
        path.parent.mkdir(parents=True)
        original = '{"version": 1, "channels": {"chat": {"verbosity": "low"}}}'
        path.write_text(original, encoding="utf-8")
        os.chmod(path.parent, 0o500)
        self.addCleanup(os.chmod, path.parent, 0o700)
        run_cli(["set", "channels.chat.verbosity=medium"], env={"XDG_CONFIG_HOME": str(self.home)})
        self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
