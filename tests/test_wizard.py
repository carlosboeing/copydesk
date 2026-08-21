"""Every wizard string obeys the copy rules, and the wizard demos the product."""

from __future__ import annotations

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

import adapters  # noqa: E402
import instructions  # noqa: E402
import wizard  # noqa: E402

# A suite run from inside a worktree inherits GIT_DIR and its siblings, which
# would point a temporary repository back at the real one.
CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def every_string() -> list:
    strings = list(wizard.COPY.values())
    strings.extend(wizard.EXAMPLES.values())
    for presets in wizard.PRESETS.values():
        for preset in presets:
            strings.append(f"{preset.label} - {preset.consequence}")
    return strings


class CopyRuleTests(unittest.TestCase):
    def test_a_consequence_is_at_most_seven_words(self) -> None:
        for presets in wizard.PRESETS.values():
            for preset in presets:
                self.assertLessEqual(len(preset.consequence.split()), 7, preset.label)

    def test_no_internal_vocabulary_reaches_the_screen(self) -> None:
        banned = ("channel", "preset", "carr" + "ier", "adapter", "cascade")
        for text in every_string():
            if "(" in text:
                continue  # a Customize prompt shows its config key in brackets
            for word in banned:
                self.assertNotIn(word, text.lower(), text)

    def test_every_channel_question_has_the_same_shape(self) -> None:
        # Two or three real options, then Customize. The design's rule counts
        # the options a user chooses between, so Customize is excluded here
        # and asserted separately.
        for name, presets in wizard.PRESETS.items():
            self.assertIn(len(presets) - 1, (2, 3), f"{name}: {len(presets) - 1} options")
            self.assertEqual(presets[-1].label, "Customize…", name)

    def test_the_option_counts_are_the_designed_ones(self) -> None:
        self.assertEqual(
            {name: len(presets) - 1 for name, presets in wizard.PRESETS.items()},
            {"chat": 3, "documents": 3, "commits": 2, "reviews": 2},
        )

    def test_the_wizard_strings_pass_copydesk_check(self) -> None:
        joined = "\n\n".join(every_string())
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "check", "-"],
            input=joined, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_no_recommended_label_appears(self) -> None:
        for text in every_string():
            self.assertNotIn("recommended", text.lower())


class PresetMappingTests(unittest.TestCase):
    def test_chat_presets_match_the_design_table(self) -> None:
        self.assertEqual(
            [(p.label, p.style, p.verbosity) for p in wizard.PRESETS["chat"][:-1]],
            [
                ("Short and direct", "plain", "low"),
                ("More explanatory", "general", "medium"),
                ("Thorough", "plain", "high"),
            ],
        )

    def test_commits_presets_are_the_two_grid_points(self) -> None:
        self.assertEqual(
            [(p.label, p.style, p.verbosity) for p in wizard.PRESETS["commits"][:-1]],
            [("Subject only", "engineer", "low"), ("Subject and body", "engineer", "medium")],
        )

    def test_every_preset_is_expressible_as_knobs(self) -> None:
        # A preset no knob combination can express does not ship.
        for presets in wizard.PRESETS.values():
            for preset in presets[:-1]:
                self.assertIn(preset.style, ("plain", "general", "engineer", "editorial"))
                self.assertIn(preset.verbosity, ("low", "medium", "high"))

    def test_reviews_ships_unticked(self) -> None:
        self.assertFalse(wizard.CHANNEL_PRESELECTED["reviews"])

    def test_every_preset_has_an_example(self) -> None:
        missing = [
            (channel, preset.label)
            for channel, presets in wizard.PRESETS.items()
            for preset in presets[:-1]
            if (channel, preset.style, preset.verbosity) not in wizard.EXAMPLES
        ]
        self.assertEqual(missing, [], f"presets with no example: {missing}")

    def test_no_example_is_orphaned(self) -> None:
        wanted = {
            (channel, preset.style, preset.verbosity)
            for channel, presets in wizard.PRESETS.items()
            for preset in presets[:-1]
        }
        self.assertEqual(set(wizard.EXAMPLES) - wanted, set())


class StateTests(unittest.TestCase):
    def test_every_named_state_has_copy(self) -> None:
        for state in (
            "intro", "tools", "where", "review", "confirm", "progress",
            "outro_success", "outro_cancelled", "outro_no_tools", "rerun", "non_tty",
        ):
            self.assertIn(state, wizard.COPY, state)

    def test_the_tool_lines_come_from_the_registry(self) -> None:
        for name, adapter in adapters.REGISTRY.items():
            if name == "git":
                continue
            self.assertIn(adapter.installs, wizard.tool_line(name, available=True))

    def test_a_missing_tool_says_so(self) -> None:
        self.assertIn("not found on this machine", wizard.tool_line("grok", available=False))


class FlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()

    def _run(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            PATH=str(self.home / "nothing"),
        )
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", *args],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )

    def test_a_seeded_harness_is_detected(self) -> None:
        self.assertIn("Claude Code", self._run("--dry-run").stdout)

    def test_dry_run_writes_nothing(self) -> None:
        before = sorted(p.name for p in self.home.iterdir())
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), before)
        self.assertIn("these files will change", result.stdout.lower())

    def test_defaults_still_shows_the_review_panel(self) -> None:
        result = self._run("--defaults", "--yes")
        self.assertIn("these files will change", result.stdout.lower())

    def test_defaults_with_yes_writes_the_config(self) -> None:
        self._run("--defaults", "--yes")
        self.assertTrue((self.home / "config" / "copydesk" / "config.json").is_file())

    def test_the_written_config_carries_a_schema_line(self) -> None:
        self._run("--defaults", "--yes")
        body = (self.home / "config" / "copydesk" / "config.json").read_text(encoding="utf-8")
        self.assertIn('"$schema"', body)

    def test_the_written_config_carries_comments(self) -> None:
        self._run("--defaults", "--yes")
        body = (self.home / "config" / "copydesk" / "config.json").read_text(encoding="utf-8")
        self.assertIn("//", body)

    def test_the_outro_names_the_undo_command(self) -> None:
        result = self._run("--defaults", "--yes")
        self.assertIn("copydesk uninstall", result.stdout)

    def test_the_proof_run_reports_a_block(self) -> None:
        result = self._run("--defaults", "--yes")
        self.assertIn("blocked a sample", result.stdout.lower())

    def test_no_tools_found_takes_the_no_tools_outro(self) -> None:
        empty = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, empty)
        env = dict(
            os.environ,
            COPYDESK_HOME=str(empty),
            XDG_CONFIG_HOME=str(empty / "config"),
            PATH=str(empty / "nothing"),
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=empty, capture_output=True, text=True, env=env, input="",
        )
        self.assertIn("install one", result.stdout.lower())
        self.assertFalse((empty / "config").exists())

    def test_a_second_run_offers_the_three_way_fork(self) -> None:
        self._run("--defaults", "--yes")
        result = self._run("--dry-run")
        self.assertIn("Change settings", result.stdout)
        self.assertIn("Repair the install", result.stdout)
        self.assertIn("Start over", result.stdout)

    def test_repair_keeps_the_settings_it_finds(self) -> None:
        """Repair rebuilds hooks and styles. The config is the user's own."""
        config_path = self.home / "config" / "copydesk" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            '{\n  "version": 1,\n  // mine\n'
            '  "channels": {"chat": {"style": "editorial", "verbosity": "high"}},\n'
            '  "agents": ["claude-code"]\n}\n',
            encoding="utf-8",
        )
        before = config_path.read_text(encoding="utf-8")
        result = self._run("--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config_path.read_text(encoding="utf-8"), before)
        # No other planned write ends in config.json, so its absence from the
        # panel says the repair plan left the config out.
        self.assertNotIn("config.json", result.stdout)

    def test_repair_still_rewrites_the_generated_files(self) -> None:
        config_path = self.home / "config" / "copydesk" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('{"version": 1, "channels": {"chat": {"style": "editorial"}}}',
                               encoding="utf-8")
        self._run("--repair", "--yes")
        self.assertTrue((self.home / ".claude" / "hooks" / "copydesk" / "gate.sh").is_file())

    def test_repair_keeps_a_config_that_has_no_channels_block(self) -> None:
        # A config of rules alone is valid and is the user's own writing. The
        # preserve test used to read `channels` specifically, so this file was
        # treated as nothing to preserve and overwritten with defaults.
        config_path = self.home / "config" / "copydesk" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            '{\n  "version": 1,\n'
            '  "rules": {"banned-word": {"add": ["synergy"]}}\n}\n',
            encoding="utf-8",
        )
        before = config_path.read_text(encoding="utf-8")
        result = self._run("--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(config_path.read_text(encoding="utf-8"), before)

    def test_repair_refuses_a_config_it_cannot_read(self) -> None:
        # Unreadable is not the same as absent. Reading them as one wrote
        # defaults over a file whose contents were never understood.
        config_path = self.home / "config" / "copydesk" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("{ not json", encoding="utf-8")
        result = self._run("--repair", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(config_path.read_text(encoding="utf-8"), "{ not json")
        self.assertFalse(
            (self.home / ".claude" / "hooks" / "copydesk").exists(),
            "setup wrote before refusing",
        )

    def test_repair_with_no_config_writes_one(self) -> None:
        # The control. Repair has nothing to preserve on a fresh machine, so
        # there it behaves as a first install rather than skipping the config.
        result = self._run("--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / "config" / "copydesk" / "config.json").is_file())


class UninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()
        self.agents = self.home / ".codex" / "AGENTS.md"
        self.agents.parent.mkdir(parents=True)
        self.original = "# My own instructions\n\nKeep these.\n"
        self.agents.write_text(self.original, encoding="utf-8")

    def test_both_harnesses_were_detected(self) -> None:
        output = self._cli("setup", "--dry-run").stdout
        self.assertIn("Claude Code", output)
        self.assertIn("Codex", output)

    def _cli(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
        )
        # A temporary directory rather than the caller's, so a run of the
        # suite never installs a commit-msg hook into the repository it is
        # testing.
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )

    def test_uninstall_removes_the_marked_block_and_leaves_the_rest(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        self.assertIn("<!-- copydesk:start -->", self.agents.read_text(encoding="utf-8"))
        self._cli("uninstall", "--yes")
        self.assertEqual(self.agents.read_text(encoding="utf-8"), self.original)

    def test_uninstall_removes_the_hook_directory(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        hooks = self.home / ".claude" / "hooks" / "copydesk"
        self.assertTrue(hooks.is_dir())
        self._cli("uninstall", "--yes")
        self.assertFalse(hooks.exists())

    def test_uninstall_names_every_path_before_touching_one(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        result = self._cli("uninstall", "--dry-run")
        self.assertIn(str(self.agents), result.stdout)
        self.assertIn("<!-- copydesk:start -->", self.agents.read_text(encoding="utf-8"))

    def test_uninstall_keeps_edits_made_after_setup(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        text = self.agents.read_text(encoding="utf-8")
        self.agents.write_text(text + "\n# Added after setup\n", encoding="utf-8")
        self._cli("uninstall", "--yes")
        body = self.agents.read_text(encoding="utf-8")
        self.assertIn("# Added after setup", body)
        self.assertIn("# My own instructions", body)
        self.assertNotIn("copydesk:start", body)

    def test_uninstall_leaves_other_hooks_registered(self) -> None:
        settings = self.home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            '{"hooks": {"PreToolUse": [{"matcher": "Bash",'
            ' "hooks": [{"type": "command", "command": "~/my-own-hook.sh"}]}]}}',
            encoding="utf-8",
        )
        self._cli("setup", "--defaults", "--yes")
        self._cli("uninstall", "--yes")
        body = settings.read_text(encoding="utf-8")
        self.assertIn("my-own-hook.sh", body)
        self.assertNotIn("copydesk", body)

    def test_uninstall_keeps_the_user_config(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        self._cli("uninstall", "--yes")
        self.assertTrue((self.home / "config" / "copydesk" / "config.json").is_file())

    def test_purge_removes_the_user_config(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        self._cli("uninstall", "--yes", "--purge")
        self.assertFalse((self.home / "config" / "copydesk" / "config.json").is_file())


class CommitHookSetupTests(unittest.TestCase):
    """Setup installs the commits gate where git will run it, and uninstall
    takes back only what setup put there."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True, env=CLEAN_ENV)
        self.hook = self.repo / ".git" / "hooks" / "commit-msg"

    def _cli(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            CLEAN_ENV,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
        )
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            cwd=self.repo, capture_output=True, text=True, env=env, input="",
        )

    def test_setup_installs_the_commit_msg_hook(self) -> None:
        result = self._cli("setup", "--defaults", "--yes")
        self.assertTrue(self.hook.is_file(), result.stdout + result.stderr)
        self.assertIn("# CopyDesk commits gate", self.hook.read_text(encoding="utf-8"))

    def test_the_review_panel_names_the_hook(self) -> None:
        self.assertIn("commit-msg", self._cli("setup", "--dry-run").stdout)

    def test_dry_run_installs_no_hook(self) -> None:
        self._cli("setup", "--dry-run")
        self.assertFalse(self.hook.exists())

    def test_outside_a_repository_no_hook_is_installed(self) -> None:
        # The control. Without it the tests above could pass on a wizard that
        # writes a commit-msg hook into any directory it is run from.
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside)
        env = dict(
            CLEAN_ENV,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
        )
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=outside, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(outside.rglob("commit-msg")), [])

    def test_a_hook_someone_else_wrote_is_reported_and_kept(self) -> None:
        theirs = "#!/bin/sh\necho theirs\n"
        self.hook.parent.mkdir(parents=True, exist_ok=True)
        self.hook.write_text(theirs, encoding="utf-8")
        result = self._cli("setup", "--defaults", "--yes")
        self.assertIn("already exists", result.stdout)
        self.assertEqual(self.hook.read_text(encoding="utf-8"), theirs)

    def test_a_hook_that_cannot_be_written_fails_the_whole_setup(self) -> None:
        # Setup advertises all-or-nothing. Installing the hook after the plan
        # applied meant a failure here left every home write in place and
        # still reported success.
        self.hook.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.hook.parent, 0o500)
        self.addCleanup(os.chmod, self.hook.parent, 0o700)
        result = self._cli("setup", "--defaults", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.hook.exists())
        self.assertFalse(
            (self.home / "config" / "copydesk" / "config.json").exists(),
            "a write from earlier in the plan survived the failed hook install",
        )

    def test_uninstall_removes_the_hook_setup_installed(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        self.assertTrue(self.hook.is_file())
        self._cli("uninstall", "--yes")
        self.assertFalse(self.hook.exists())
        self.assertTrue(self.hook.parent.is_dir(), "git owns its hooks directory")

    def test_uninstall_keeps_a_hook_someone_else_wrote(self) -> None:
        theirs = "#!/bin/sh\necho theirs\n"
        self.hook.parent.mkdir(parents=True, exist_ok=True)
        self.hook.write_text(theirs, encoding="utf-8")
        self._cli("setup", "--defaults", "--yes")
        self._cli("uninstall", "--yes")
        self.assertEqual(self.hook.read_text(encoding="utf-8"), theirs)


class InstalledStyleTests(unittest.TestCase):
    """The installed output styles carry the user's settings, not the
    repository's defaults, and doctor agrees the moment setup finishes."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()
        self.installed = self.home / ".claude" / "output-styles" / "copydesk-low.md"

    def _cli(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            PATH=str(self.home / "nothing"),
        )
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )

    def _write_config(self, style: str) -> None:
        path = self.home / "config" / "copydesk" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"version": 1, "channels": {"chat": '
            f'{{"enabled": true, "style": "{style}", "verbosity": "low"}}}}}}',
            encoding="utf-8",
        )

    def test_a_chosen_chat_style_reaches_the_installed_output_style(self) -> None:
        self._write_config("editorial")
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.installed.read_text(encoding="utf-8")
        self.assertIn(instructions.style_line("chat", "editorial"), body)
        self.assertNotIn(instructions.style_line("chat", "plain"), body)

    def test_the_default_install_carries_the_default_style(self) -> None:
        # The control. Without it the test above could pass on a wizard that
        # writes editorial into every install whatever the config says.
        result = self._cli("setup", "--defaults", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.installed.read_text(encoding="utf-8")
        self.assertIn(instructions.style_line("chat", "plain"), body)
        self.assertNotIn(instructions.style_line("chat", "editorial"), body)

    def test_a_chosen_style_install_reports_no_drift(self) -> None:
        self._write_config("editorial")
        self._cli("setup", "--repair", "--yes")
        self.assertNotIn("out of date", self._cli("doctor").stdout.lower())

    def test_a_default_install_reports_no_drift(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        self.assertNotIn("out of date", self._cli("doctor").stdout.lower())

    def test_every_verbosity_level_is_installed(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        for level in instructions.VERBOSITY_LEVELS:
            style = self.home / ".claude" / "output-styles" / f"copydesk-{level}.md"
            self.assertTrue(style.is_file(), level)
            self.assertIn(
                instructions._VERBOSITY_LINES[level], style.read_text(encoding="utf-8")
            )


class ExistingSettingsTests(unittest.TestCase):
    """A harness settings file setup cannot parse is refused, never replaced."""

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()
        self.settings = self.home / ".claude" / "settings.json"

    def _cli(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            PATH=str(self.home / "nothing"),
        )
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )

    def test_a_settings_file_that_will_not_parse_stops_setup(self) -> None:
        original = '{"model": "opus", "permissions": {"allow": ["Bash"]}\n'
        self.settings.write_text(original, encoding="utf-8")
        result = self._cli("setup", "--defaults", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), original)

    def test_the_refusal_names_the_path_and_the_reason(self) -> None:
        self.settings.write_text('{"model": "opus"\n', encoding="utf-8")
        output = self._cli("setup", "--defaults", "--yes").stdout
        self.assertIn(str(self.settings), output)
        self.assertIn("line", output.lower())

    def test_nothing_is_written_before_the_refusal(self) -> None:
        self.settings.write_text("{ not json", encoding="utf-8")
        self._cli("setup", "--defaults", "--yes")
        self.assertFalse((self.home / ".claude" / "hooks" / "copydesk").exists())
        self.assertFalse((self.home / "config" / "copydesk" / "config.json").exists())

    def test_a_settings_file_holding_a_list_is_refused(self) -> None:
        original = "[]\n"
        self.settings.write_text(original, encoding="utf-8")
        self.assertEqual(self._cli("setup", "--defaults", "--yes").returncode, 1)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), original)

    def test_a_settings_file_that_parses_keeps_its_other_keys(self) -> None:
        # The control. Without it the four tests above could pass on a setup
        # that refuses every existing settings.json.
        self.settings.write_text(
            '{"model": "opus", "permissions": {"allow": ["Bash"]}}', encoding="utf-8"
        )
        result = self._cli("setup", "--defaults", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(document["model"], "opus")
        self.assertEqual(document["permissions"], {"allow": ["Bash"]})
        self.assertIn("PreToolUse", document["hooks"])

    def test_comments_in_a_settings_file_are_not_a_refusal(self) -> None:
        self.settings.write_text(
            '{\n  // mine\n  "model": "opus"\n}\n', encoding="utf-8"
        )
        result = self._cli("setup", "--defaults", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["model"], "opus")


if __name__ == "__main__":
    unittest.main()
