import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import config  # noqa: E402
import instructions  # noqa: E402
import linter  # noqa: E402
import styles  # noqa: E402

PRESET = json.loads((ROOT / "rules" / "plain.json").read_text(encoding="utf-8"))


def run_cli(args: list, env: dict) -> int:
    merged = dict(os.environ)
    merged.update(env)
    if "COPYDESK_STATE_DIR" not in env:
        merged.pop("COPYDESK_STATE_DIR", None)
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
            },
            "documents": {
                "enabled": True,
                "style": "plain",
                "verbosity": "high",
                "guidance": {"recommendations": True},
            },
            "commits": {
                "enabled": True,
                "style": "engineer",
                "verbosity": "low",
                "guidance": {},
            },
            "reviews": {
                "enabled": False,
                "style": "plain",
                "verbosity": "medium",
                "guidance": {"pushback": True},
            },
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


class OneInstructionPerFactTests(unittest.TestCase):
    """The channel line, the style line and the verbosity line say one thing each.

    Two of them saying the same thing spends a budget measured in words on
    nothing. Two of them saying different things leaves the model to choose,
    and a commit body cannot be bullets and a paragraph at once.
    """

    CHANNEL_LINES = {
        "commits": instructions._COMMITS,
        "reviews": instructions._REVIEWS,
    }
    STYLES = ("plain", "general", "engineer", "editorial")

    @staticmethod
    def _runs(text: str, size: int = 4) -> set:
        words = re.findall(r"[a-z0-9]+", text.lower())
        return {tuple(words[i:i + size]) for i in range(len(words) - size + 1)}

    def test_no_style_line_restates_its_channel_line(self) -> None:
        for channel, line in self.CHANNEL_LINES.items():
            for style in self.STYLES:
                shared = self._runs(line) & self._runs(instructions.style_line(channel, style))
                self.assertEqual(shared, set(), f"{channel}/{style} repeats {shared}")

    def test_no_verbosity_line_restates_its_channel_line(self) -> None:
        for level, line in instructions._COMMITS_VERBOSITY.items():
            shared = self._runs(instructions._COMMITS) & self._runs(line)
            self.assertEqual(shared, set(), f"commits/{level} repeats {shared}")

    def test_a_commit_body_is_never_asked_for_bullets_and_a_paragraph(self) -> None:
        for style in self.STYLES:
            for level in instructions.VERBOSITY_LEVELS:
                config = resolved()
                config["channels"]["commits"] = {
                    "enabled": True, "style": style, "verbosity": level, "guidance": {},
                }
                rendered = instructions.render_commits(config).lower()
                self.assertFalse(
                    "bullet" in rendered and "paragraph" in rendered,
                    f"commits/{style}/{level} asks for both forms",
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


class OutputStyleFrontmatterTests(unittest.TestCase):
    """The frontmatter must describe the style the config picked, and the
    provenance comment must name whoever wrote this copy of the file.

    The description used to come from the preset's `output_style` block, so
    an install with a different chat style advertised plain in Claude Code's
    style picker while the body rendered something else. The provenance line
    used to hard-code the generator, so files the setup wizard wrote claimed
    a writer that had never run on that machine.
    """

    GENERATOR = "scripts/generate-instructions.py"
    WIZARD = "copydesk setup; regenerate with copydesk setup --repair"

    def _config(self, chat_style: str = "plain") -> dict:
        config = resolved()
        # `config.resolve()` grafts the preset onto the resolved document;
        # the shared fixture still nests it under `preset`.
        config.update(config.pop("preset"))
        config["channels"]["chat"]["style"] = chat_style
        return config

    def _render(self, chat_style: str, level: str = "low") -> str:
        return instructions.render_output_style(self._config(chat_style), level, writer=self.GENERATOR)

    def test_the_description_matches_the_chat_style(self) -> None:
        for style in ("plain", "general", "engineer", "editorial"):
            self.assertIn(
                f"description: {styles.DESCRIPTIONS[style]}", self._render(style), style
            )

    def test_the_preset_description_is_absent_when_the_style_differs(self) -> None:
        preset_description = PRESET["instructions"]["output_style"]["description"]
        for style in ("general", "engineer", "editorial"):
            self.assertNotIn(preset_description, self._render(style), style)

    def test_a_file_rendered_through_the_generator_names_the_script(self) -> None:
        self.assertIn(f"by {self.GENERATOR}.", self._render("plain"))

    def test_a_file_rendered_through_the_wizard_names_setup_and_repair(self) -> None:
        rendered = instructions.render_output_style(self._config(), "low", writer=self.WIZARD)
        self.assertIn("by copydesk setup", rendered)
        self.assertIn("copydesk setup --repair", rendered)
        self.assertNotIn(self.GENERATOR, rendered)


class SetCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_set_writes_the_user_config(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}
        code = run_cli(["set", "channels.chat.verbosity=medium"], env=env)
        self.assertEqual(code, 0)
        body = json.loads((self.home / "copydesk" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(body["channels"]["chat"]["verbosity"], "medium")

    def test_set_refuses_a_key_it_does_not_own(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}
        self.assertEqual(run_cli(["set", "gate.retries=5"], env=env), 64)

    def test_set_refuses_an_invalid_value(self) -> None:
        env = {"XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")}
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
        run_cli(["set", "channels.chat.verbosity=medium"], env={"XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")})
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
        run_cli(["set", "channels.chat.verbosity=high"], env={"XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")})
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
        run_cli(["set", "channels.chat.verbosity=high"], env={"XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")})
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
        run_cli(["set", "channels.chat.verbosity=medium"], env={"XDG_CONFIG_HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state")})
        self.assertEqual(path.read_text(encoding="utf-8"), original)


class FingerprintTests(unittest.TestCase):
    def test_a_fingerprint_is_twelve_hex_characters(self) -> None:
        value = instructions.fingerprint("rendered text")
        self.assertEqual(len(value), 12)
        int(value, 16)

    def test_a_changed_rendering_changes_the_fingerprint(self) -> None:
        self.assertNotEqual(
            instructions.fingerprint("rendered text"), instructions.fingerprint("rendered text 2")
        )

    def test_the_marker_line_itself_is_not_hashed(self) -> None:
        # Otherwise stamping the file would change what the stamp describes.
        body = "line one\nline two\n"
        stamped = f"line one\n<!-- {instructions.FINGERPRINT_MARKER}abc123abc123 -->\nline two\n"
        self.assertEqual(instructions.fingerprint(body), instructions.fingerprint(stamped))

    def test_a_changed_guidance_snippet_changes_the_fingerprint(self) -> None:
        first = instructions.render_chat(resolved())
        config = resolved()
        config["channels"]["chat"]["guidance"] = {"sources": True}
        self.assertNotEqual(
            instructions.fingerprint(first), instructions.fingerprint(instructions.render_chat(config))
        )

    def test_every_generated_style_embeds_its_fingerprint(self) -> None:
        for level in ("low", "medium", "high"):
            text = (ROOT / "output-styles" / f"copydesk-{level}.md").read_text(encoding="utf-8")
            self.assertIn(instructions.FINGERPRINT_MARKER, text)


class DeltaTests(unittest.TestCase):
    def test_no_difference_produces_no_line(self) -> None:
        self.assertIsNone(instructions.delta(resolved(), resolved()))

    def test_a_different_documents_style_is_named(self) -> None:
        effective = resolved()
        effective["channels"]["documents"] = {"style": "engineer", "verbosity": "high"}
        static = resolved()
        static["channels"]["documents"] = {"style": "plain", "verbosity": "high"}
        line = instructions.delta(static, effective)
        self.assertIsNotNone(line)
        assert line is not None
        self.assertIn("documents", line)
        self.assertIn("engineer", line)

    def test_the_delta_is_one_line(self) -> None:
        effective = resolved()
        effective["channels"]["chat"] = {"style": "editorial", "verbosity": "high", "guidance": {}}
        d = instructions.delta(resolved(), effective)
        self.assertIsNotNone(d)
        assert d is not None
        self.assertNotIn("\n", d)

    def test_a_disabled_channel_is_named(self) -> None:
        effective = resolved()
        effective["channels"]["documents"] = {"enabled": False}
        d = instructions.delta(resolved(), effective)
        self.assertIsNotNone(d)
        assert d is not None
        self.assertIn("documents is off", d)

    def test_a_guidance_change_is_named(self) -> None:
        effective = resolved()
        effective["channels"]["chat"] = dict(resolved()["channels"]["chat"])
        effective["channels"]["chat"]["guidance"] = dict(
            resolved()["channels"]["chat"]["guidance"], sources=True
        )
        d = instructions.delta(resolved(), effective)
        self.assertIsNotNone(d)
        assert d is not None
        self.assertIn("chat sources is on", d)


class StalenessNoticeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def _write_user_config(self, text: str) -> None:
        path = self.home / ".config" / "copydesk" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _install_style_matching_current_config(self) -> None:
        env_prev = os.environ.get("XDG_CONFIG_HOME")
        state_prev = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home / ".config")
        os.environ["XDG_STATE_HOME"] = str(self.home / "state")
        try:
            cfg = linter.user_layer()
        finally:
            if env_prev is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = env_prev
            if state_prev is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = state_prev
        fresh = instructions.render_output_style_body(cfg, "low")
        marker = instructions.fingerprint(fresh)
        style_path = self.home / ".claude" / "output-styles" / "copydesk-low.md"
        style_path.parent.mkdir(parents=True, exist_ok=True)
        style_path.write_text(
            f"---\nname: CopyDesk low\n---\n<!-- copydesk-build:{marker} -->\n{fresh}\n",
            encoding="utf-8",
        )

    def test_a_changed_user_config_makes_the_installed_style_stale(self) -> None:
        self._install_style_matching_current_config()
        self._write_user_config('{"version": 1, "channels": {"chat": {"style": "editorial"}}}')
        env_prev = os.environ.get("XDG_CONFIG_HOME")
        state_prev = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home / ".config")
        os.environ["XDG_STATE_HOME"] = str(self.home / "state")
        try:
            self.assertIn("out of date", linter._fingerprint_notice(self.home) or "")
        finally:
            if env_prev is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = env_prev
            if state_prev is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = state_prev

    def test_an_unchanged_config_reports_nothing(self) -> None:
        self._install_style_matching_current_config()
        env_prev = os.environ.get("XDG_CONFIG_HOME")
        state_prev = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home / ".config")
        os.environ["XDG_STATE_HOME"] = str(self.home / "state")
        try:
            self.assertIsNone(linter._fingerprint_notice(self.home))
        finally:
            if env_prev is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = env_prev
            if state_prev is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = state_prev


class ChannelBlockTests(unittest.TestCase):
    def test_documents_carries_the_craft_block(self) -> None:
        rendered = instructions.render_documents(resolved()).lower()
        self.assertIn("problem before the solution", rendered)
        self.assertIn("heading carries a claim", rendered)

    def test_the_craft_block_fits_forty_words(self) -> None:
        self.assertLessEqual(instructions.word_count(instructions.CRAFT), 40)

    def test_commits_and_reviews_together_fit_thirty_five_words(self) -> None:
        total = instructions.word_count(
            instructions.render_commits(resolved()) + " " + instructions.render_reviews(resolved())
        )
        self.assertLessEqual(total, 35)

    def test_the_commits_floor_is_stated(self) -> None:
        rendered = instructions.render_commits(resolved()).lower()
        self.assertIn("72", rendered)
        self.assertIn("why", rendered)

    def test_the_block_orders_chat_documents_commits(self) -> None:
        # The markers around this body are applied where the block is
        # spliced, and are asserted against the installed file by the
        # setup and uninstall tests.
        body = instructions.render_agents_block(resolved(), include_chat=True)
        self.assertLess(
            body.index(instructions.style_line("chat", "plain")),
            body.index("problem before the solution"),
        )
        self.assertLess(
            body.index("problem before the solution"), body.index("imperative subject")
        )

    def test_a_disabled_channel_contributes_nothing(self) -> None:
        config = resolved()
        config["channels"]["commits"] = {"enabled": False}
        self.assertNotIn("commit", instructions.render_agents_block(config).lower())

    def test_disabled_chat_contributes_nothing_even_when_the_block_asks(self) -> None:
        # render_chat itself has no enabled check: the output style calls it
        # for its rules region. The join is where the flag has to bite.
        config = resolved()
        config["channels"]["chat"]["enabled"] = False
        self.assertNotIn(
            "answer first",
            instructions.render_agents_block(config, include_chat=True).lower(),
        )
        for name in ("documents", "commits", "reviews"):
            config["channels"][name] = {"enabled": False}
        self.assertEqual(
            instructions.render_agents_block(config, include_chat=True),
            "",
        )

    def test_the_chat_channel_joins_when_the_flag_asks(self) -> None:
        rendered = instructions.render_agents_block(resolved(), include_chat=True).lower()
        self.assertIn("answer first", rendered)
        self.assertIn("closing block appears only when", rendered)
        self.assertIn("say a thing once", rendered)
        self.assertIn(instructions.style_line("chat", "plain").lower(), rendered)

    def test_without_the_flag_the_block_still_leaves_chat_out(self) -> None:
        # The control for the test above. Claude Code reads chat through the
        # output style, so a file only Claude Code reads must not carry it
        # a second time.
        rendered = instructions.render_agents_block(resolved()).lower()
        self.assertNotIn("answer first", rendered)


class RepeatCloserTests(unittest.TestCase):
    def test_the_same_closer_twice_is_detected(self) -> None:
        first = linter._closer_hash("Answer.\n\n1. Ship it?\n2. Wait?\n")
        second = linter._closer_hash("Different body.\n\n1. Ship it?\n2. Wait?\n")
        self.assertEqual(first, second)

    def test_a_changed_closer_is_not_detected(self) -> None:
        self.assertNotEqual(
            linter._closer_hash("Answer.\n\n1. Ship it?\n"),
            linter._closer_hash("Answer.\n\n1. Roll back?\n"),
        )

    def test_a_reply_with_no_closing_block_hashes_to_none(self) -> None:
        self.assertIsNone(linter._closer_hash("Just an answer with no list.\n"))

    def test_an_earlier_body_list_is_not_part_of_the_hash(self) -> None:
        with_body_list = "Answer.\n\n- a body bullet\n\nProse.\n\n1. Ship it?\n"
        without = "Answer.\n\nProse.\n\n1. Ship it?\n"
        self.assertEqual(linter._closer_hash(with_body_list), linter._closer_hash(without))

    def test_trailing_prose_after_a_list_means_no_closing_block(self) -> None:
        self.assertIsNone(linter._closer_hash("Answer.\n\n1. Ship it?\n\nOne more thought.\n"))

    def test_only_the_final_contiguous_list_is_hashed(self) -> None:
        two_lists = "- first list\n\nProse between.\n\n1. Ship it?\n2. Wait?\n"
        only_last = "1. Ship it?\n2. Wait?\n"
        self.assertEqual(linter._closer_hash(two_lists), linter._closer_hash(only_last))

    def test_a_wrapped_list_item_stays_with_its_item(self) -> None:
        wrapped = "Answer.\n\n1. Ship it,\n   once the tests pass?\n"
        self.assertIsNotNone(linter._closer_hash(wrapped))

    def test_trailing_blank_lines_do_not_change_the_hash(self) -> None:
        self.assertEqual(
            linter._closer_hash("Answer.\n\n1. Ship it?\n"),
            linter._closer_hash("Answer.\n\n1. Ship it?\n\n\n"),
        )


class ProductionShapeTests(unittest.TestCase):
    """The fixture above hand-builds a `preset` key. `config.resolve()` never
    produces one, so a renderer reading it got an empty dict in production and
    silently dropped whatever it held. These tests use the real resolved shape.
    """

    def resolve_production(self) -> dict:
        home = tempfile.mkdtemp()
        try:
            return config.resolve(
                ROOT / "rules",
                user_path=Path(home) / "absent.json",
            )
        except config.ConfigError:
            return config.resolve(ROOT / "rules")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_resolve_puts_the_instructions_at_the_top_level(self) -> None:
        resolved_config = self.resolve_production()
        self.assertIn("instructions", resolved_config)
        self.assertNotIn("preset", resolved_config)

    def test_the_categories_reach_the_rendered_chat_block(self) -> None:
        resolved_config = self.resolve_production()
        categories = resolved_config["instructions"]["categories"]
        self.assertTrue(categories, "the preset carries no categories text to check")
        self.assertIn(categories, instructions.render_chat(resolved_config))

    def test_every_shipped_output_style_carries_the_categories(self) -> None:
        categories = PRESET["instructions"]["categories"]
        for level in instructions.VERBOSITY_LEVELS:
            path = ROOT / "output-styles" / f"copydesk-{level}.md"
            self.assertIn(
                categories, path.read_text(encoding="utf-8"),
                f"{path.name} was generated without the categories paragraph",
            )

    def test_a_style_the_categories_are_absent_from_is_caught(self) -> None:
        # Control: the same assertion against text known not to contain them.
        with self.assertRaises(AssertionError):
            self.assertIn(PRESET["instructions"]["categories"], "no categories here")


if __name__ == "__main__":
    unittest.main()
