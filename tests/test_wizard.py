"""Every wizard string obeys the copy rules, and the wizard demos the product."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import adapters  # noqa: E402
import config  # noqa: E402
import instructions  # noqa: E402
import linter  # noqa: E402
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
        # A state redirection: the CLI linter records what it sees, and this
        # run is not the developer's own linting.
        state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, state)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "check", "-"],
            input=joined, capture_output=True, text=True,
            env=dict(os.environ, XDG_STATE_HOME=state, COPYDESK_STATE_DIR=state),
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
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
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
            XDG_STATE_HOME=str(empty / "state"),
            PATH=str(empty / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
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


class ProofRunTests(unittest.TestCase):
    """Every proof starts with no history behind it.

    The proof reuses one session id across setups, so its retry state
    outlived any single run. The third consecutive proof then tripped the
    gate's identical-content escape valve, and a healthy install reported a
    failed proof.
    """

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()
        self.state = self.home / "state"
        # Pinned in-process as well as in subprocesses: prove runs gate.sh
        # from this interpreter's environment, so an unpinned variable would
        # resolve to the developer's real state directory.
        self._saved_env = {
            name: os.environ.get(name)
            for name in ("COPYDESK_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "COPYDESK_STATE_DIR")
        }
        os.environ["COPYDESK_HOME"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "config")
        os.environ["XDG_STATE_HOME"] = str(self.state)
        os.environ.pop("COPYDESK_STATE_DIR", None)
        self.addCleanup(self._restore_env)
        env = dict(os.environ, PATH=str(self.home / "nothing"))
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _restore_env(self) -> None:
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _entries(self) -> dict:
        state_file = self.state / "copydesk" / "copydesk-setup-proof.json"
        return json.loads(state_file.read_text(encoding="utf-8"))["files"]

    def test_three_proofs_in_a_row_each_block_and_leave_at_most_one_entry(self) -> None:
        for attempt in range(1, 4):
            blocked, reason = wizard.prove(self.home)
            self.assertTrue(blocked, f"proof {attempt}: {reason}")
            self.assertLessEqual(len(self._entries()), 1)

    def test_a_proof_clears_entries_left_behind_by_earlier_runs(self) -> None:
        state_file = self.state / "copydesk" / "copydesk-setup-proof.json"
        stale = "/an/earlier/temporary/home/copydesk-sample.md"
        state_file.write_text(json.dumps({"files": {
            stale: {
                "content_hash": "stale",
                "hashes": ["stale"],
                "streak": 2,
                "updated_at": time.time(),
            },
        }}), encoding="utf-8")
        blocked, reason = wizard.prove(self.home)
        self.assertTrue(blocked, reason)
        self.assertNotIn(stale, self._entries())

    def test_an_undeletable_state_path_is_named_in_the_reason(self) -> None:
        # A directory sitting at the session file makes unlink fail for a
        # reason that is not absence. Setup must not crash on it, but the
        # survivor decides what the proof runs against, so the reason has
        # to name it rather than report only that the proof failed.
        state_file = self.state / "copydesk" / "copydesk-setup-proof.json"
        state_file.unlink()
        state_file.mkdir()
        blocked, reason = wizard.prove(self.home)
        self.assertFalse(blocked, f"proof {reason}")
        self.assertTrue(state_file.is_dir(), "the gate removed what deletion could not")
        self.assertIn(str(state_file), reason)
        self.assertIn("could not be deleted", reason)


class RealStateDirectoryTests(unittest.TestCase):
    """A suite run must leave the developer's own state directory alone.

    The tests once redirected XDG_CONFIG_HOME only, so every gate subprocess
    resolved the default state path and wrote temporary-home paths into the
    developer's real proof session file and telemetry log.
    """

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()

    @staticmethod
    def _snapshot() -> list:
        # Watch whichever directory this process would actually write to.
        # Hardcoding ~/.local/state/copydesk misses COPYDESK_STATE_DIR and
        # a non-default XDG_STATE_HOME, so the equality below would pass
        # while the suite still wrote into the developer's real state.
        base = linter._state_directory()
        if not base.is_dir():
            return []
        return sorted(
            (str(path.relative_to(base)), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in base.rglob("*")
            if path.is_file()
        )

    def test_a_setup_run_touches_only_the_redirected_state(self) -> None:
        before = self._snapshot()
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Control: this run exercised the state path at all. Without a file
        # under the redirection, the equality below could pass because the
        # run wrote no state anywhere.
        self.assertTrue(
            (self.home / "state" / "copydesk" / "copydesk-setup-proof.json").is_file()
        )
        self.assertEqual(self._snapshot(), before)


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
            XDG_STATE_HOME=str(self.home / "state"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
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

    def test_uninstall_takes_the_style_file_and_any_retired_leftovers(self) -> None:
        styles_dir = self.home / ".claude" / "output-styles"
        self._cli("setup", "--defaults", "--yes")
        self.assertTrue((styles_dir / "copydesk.md").is_file())
        # A leftover from the three-file layout an upgrade may not have
        # migrated yet goes with uninstall too.
        (styles_dir / "copydesk-high.md").write_text("left over\n", encoding="utf-8")
        self._cli("uninstall", "--yes")
        self.assertEqual(list(styles_dir.glob("copydesk*")), [])

    def test_uninstall_unsets_an_owned_output_style_and_keeps_the_rest(self) -> None:
        # The style file is unlinked; leaving `outputStyle: CopyDesk` would
        # name a file that is not on disk. Other keys are the user's.
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(
            json.dumps({"model": "opus", "outputStyle": "CopyDesk"}),
            encoding="utf-8",
        )
        styles = self.home / ".claude" / "output-styles"
        styles.mkdir(parents=True, exist_ok=True)
        (styles / "copydesk.md").write_text("installed\n", encoding="utf-8")
        self._cli("uninstall", "--yes")
        remaining = json.loads(settings.read_text(encoding="utf-8"))
        self.assertNotIn("outputStyle", remaining)
        self.assertEqual(remaining["model"], "opus")
        self.assertEqual(list(styles.glob("copydesk*")), [])

    def test_uninstall_leaves_a_foreign_output_style_alone(self) -> None:
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(
            json.dumps({"model": "opus", "outputStyle": "Plain English"}),
            encoding="utf-8",
        )
        self._cli("uninstall", "--yes")
        remaining = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(remaining["outputStyle"], "Plain English")
        self.assertEqual(remaining["model"], "opus")

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
            XDG_STATE_HOME=str(self.home / "state"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
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
            XDG_STATE_HOME=str(self.home / "state"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
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
        self.installed = self.home / ".claude" / "output-styles" / "copydesk.md"

    def _cli(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
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

    def test_the_installed_description_matches_the_chosen_style(self) -> None:
        # The picker in Claude Code reads the frontmatter, so a plain
        # description on an engineer install advertises a style the file
        # does not contain.
        import styles

        self._write_config("engineer")
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.installed.read_text(encoding="utf-8")
        self.assertIn(f"description: {styles.DESCRIPTIONS['engineer']}", body)

    def test_an_installed_style_names_setup_as_its_writer(self) -> None:
        # The wizard wrote this copy from the user's config; naming the
        # repository's generator sends a reader to regenerate the wrong file.
        self._cli("setup", "--defaults", "--yes")
        body = self.installed.read_text(encoding="utf-8")
        self.assertIn("by copydesk setup", body)
        self.assertIn("copydesk setup --repair", body)

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

    def test_the_installed_style_carries_the_configured_verbosity(self) -> None:
        # One style file renders at whatever the config says, replacing the
        # three per-level files the picker was never wired to switch between.
        path = self.home / "config" / "copydesk" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"version": 1, "channels": {"chat": {"verbosity": "high"}}}',
            encoding="utf-8",
        )
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = self.installed.read_text(encoding="utf-8")
        self.assertIn(instructions._VERBOSITY_LINES["high"], body)
        self.assertNotIn(instructions._VERBOSITY_LINES["low"], body)

    def test_setup_installs_exactly_one_style_file(self) -> None:
        self._cli("setup", "--defaults", "--yes")
        installed = sorted(
            p.name for p in (self.home / ".claude" / "output-styles").glob("*")
        )
        self.assertEqual(installed, ["copydesk.md"])


class MigrationTests(unittest.TestCase):
    """An install upgraded from the three-style layout ends with one.

    Setup must remove copydesk-low.md, copydesk-medium.md and
    copydesk-high.md, repoint an `outputStyle` naming any of them, and
    leave every other value alone.
    """

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()
        self.styles_dir = self.home / ".claude" / "output-styles"
        self.styles_dir.mkdir(parents=True)
        for level in ("low", "medium", "high"):
            (self.styles_dir / f"copydesk-{level}.md").write_text(
                f"---\nname: CopyDesk {level}\n---\nold body {level}\n",
                encoding="utf-8",
            )
        self.settings = self.home / ".claude" / "settings.json"
        config_path = self.home / "config" / "copydesk" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('{"version": 1}', encoding="utf-8")

    def _cli(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )

    def _seed_settings(self, output_style: str) -> None:
        self.settings.write_text(
            json.dumps({"model": "opus", "outputStyle": output_style}),
            encoding="utf-8",
        )

    def test_a_selected_retired_style_ends_repointed_at_CopyDesk(self) -> None:
        # A session running "CopyDesk medium" must not lose its style when
        # the file it names is deleted. The repoint is migration, not a new
        # choice, so it happens without asking.
        self._seed_settings("CopyDesk medium")
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(document["outputStyle"], "CopyDesk")

    def test_the_three_files_are_gone_and_one_remains(self) -> None:
        self._seed_settings("CopyDesk medium")
        self._cli("setup", "--repair", "--yes")
        installed = sorted(p.name for p in self.styles_dir.glob("*"))
        self.assertEqual(installed, ["copydesk.md"])

    def test_other_settings_keys_survive_the_migration(self) -> None:
        self._seed_settings("CopyDesk medium")
        self._cli("setup", "--repair", "--yes")
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(document["model"], "opus")

    def test_a_foreign_active_style_is_left_alone(self) -> None:
        # The control for the repoint test above. Only a value naming one of
        # CopyDesk's own retired files is migrated; anything else belongs to
        # the user.
        self._seed_settings("Plain English")
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(document["outputStyle"], "Plain English")

    def test_dry_run_names_the_leftovers_without_touching_them(self) -> None:
        self._seed_settings("CopyDesk medium")
        before = sorted(p.name for p in self.styles_dir.glob("*"))
        result = self._cli("setup", "--repair", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("copydesk-medium.md", result.stdout)
        self.assertEqual(sorted(p.name for p in self.styles_dir.glob("*")), before)

    def test_retired_files_leave_when_the_commit_hook_joins_the_plan(self) -> None:
        # Setup inside a repository adds the commit-msg write by rebuilding
        # the plan. The rebuild must keep `removes`, or the three retired
        # files survive every upgrade that also installs the hook.
        subprocess.run(["git", "init", "-q"], cwd=self.home, check=True, env=CLEAN_ENV)
        self._seed_settings("CopyDesk medium")
        git_dir = str(Path(shutil.which("git") or "/usr/bin/git").parent)
        env = dict(
            CLEAN_ENV,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=os.pathsep.join([str(self.home / "nothing"), git_dir]),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--repair", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("(removed)", result.stdout)
        installed = sorted(p.name for p in self.styles_dir.glob("*"))
        self.assertEqual(installed, ["copydesk.md"])
        self.assertTrue((self.home / ".git" / "hooks" / "commit-msg").is_file())


class ActiveStyleTests(unittest.TestCase):
    """Setup offers to make CopyDesk the active style; nothing writes the
    settings key without asking, except the part-1 migration repoint."""

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
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), *args],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )

    def test_the_prompt_names_whatever_is_being_replaced(self) -> None:
        text = wizard.active_style_prompt("Plain English")
        self.assertIn("Make CopyDesk your active output style?", text)
        self.assertIn("Claude Code currently uses Plain English.", text)

    def test_the_prompt_says_when_nothing_is_set(self) -> None:
        text = wizard.active_style_prompt(None)
        self.assertIn("Make CopyDesk your active output style?", text)
        self.assertNotIn("currently uses", text)

    def test_defaults_activate_the_style(self) -> None:
        # --defaults answers every question with its default; this one
        # defaults to yes.
        result = self._cli("setup", "--defaults", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(document["outputStyle"], "CopyDesk")

    def test_a_scripted_repair_never_writes_the_key(self) -> None:
        # No terminal to ask in, no --defaults to answer for the user: the
        # key stays exactly as found.
        self.settings.write_text('{"model": "opus"}', encoding="utf-8")
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertNotIn("outputStyle", document)
        self.assertEqual(document["model"], "opus")

    def test_defaults_leave_the_key_alone_when_chat_is_off(self) -> None:
        # --defaults answers the activation question with yes, but the
        # style body always carries the chat rules. Chat off means that
        # write would load them into every Claude Code session the config
        # says should not receive them. --repair is how the existing
        # config (chat off) is the one that is resolved; --defaults
        # without it would rebuild channels with chat on.
        config_path = self.home / "config" / "copydesk" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            json.dumps({
                "version": 1,
                "agents": ["claude-code"],
                "channels": {"chat": {"enabled": False}},
            }),
            encoding="utf-8",
        )
        result = self._cli("setup", "--repair", "--defaults", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertNotIn("outputStyle", document)


class _FdReader:
    """Minimal stdin over a file descriptor: what the raw-mode prompts touch."""

    def __init__(self, fd: int) -> None:
        self._fd = fd

    def isatty(self) -> bool:
        return os.isatty(self._fd)

    def fileno(self) -> int:
        return self._fd


class ActiveStyleInteractiveTests(unittest.TestCase):
    """The activation question over a real terminal: Enter accepts, Escape
    leaves the key exactly as it was found.

    Output goes to a StringIO and the answer is offered on a timer until
    setup consumes it. Entering raw mode flushes whatever is queued ahead
    of it, so one well-timed write cannot be relied on; a repeat every
    quarter second always lands inside the blocking key read. The spacing
    stays outside the 0.05 s window in which two escapes would decode as
    one sequence.
    """

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.home / ".claude").mkdir()
        self.settings = self.home / ".claude" / "settings.json"
        self.settings.write_text('{"model": "opus"}', encoding="utf-8")
        config_path = self.home / "config" / "copydesk" / "config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            '{"version": 1, "agents": ["claude-code"]}', encoding="utf-8"
        )
        # Pinned in-process: setup runs in this interpreter, so an unpinned
        # variable would point the proof run at the developer's own state.
        self._saved_env = {
            name: os.environ.get(name)
            for name in ("COPYDESK_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "COPYDESK_STATE_DIR")
        }
        os.environ["COPYDESK_HOME"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "config")
        os.environ["XDG_STATE_HOME"] = str(self.home / "state")
        os.environ.pop("COPYDESK_STATE_DIR", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _run(self, answer: bytes) -> str:
        import io
        import threading

        master, slave = os.openpty()
        outcome: dict = {}
        printed = io.StringIO()

        def run() -> None:
            try:
                outcome["code"] = wizard.run_setup(
                    ["--repair", "--yes"],
                    stdin=_FdReader(master),
                    stdout=printed,
                )
            except BaseException as error:  # surfaced below, not swallowed
                outcome["error"] = error

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            offered = time.time()
            while thread.is_alive():
                if time.time() > offered + 30:
                    self.fail("setup never reached or answered the activation question")
                thread.join(timeout=0.25)
                if thread.is_alive():
                    try:
                        os.write(slave, answer)
                    except OSError:
                        break
            thread.join(10)
        finally:
            os.close(master)
            os.close(slave)
        if "error" in outcome:
            raise outcome["error"]
        self.assertEqual(outcome.get("code"), 0)
        return printed.getvalue()

    def test_enter_accepts_and_sets_the_key(self) -> None:
        self._run(b"\r")
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(document["outputStyle"], "CopyDesk")

    def test_escape_declines_and_leaves_the_key_alone(self) -> None:
        self._run(b"\x1b")
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertNotIn("outputStyle", document)
        self.assertEqual(document["model"], "opus")


class RetiredStyleNeedsNoQuestionTests(ActiveStyleInteractiveTests):
    """A key naming a retired per-level style is renamed, never asked about.

    The file it names is deleted by this same run, so "no" is an answer
    setup cannot honour. Asking anyway produced a question whose answer was
    then overridden. The question must not appear, and the key must land on
    the single name.
    """

    def setUp(self) -> None:
        super().setUp()
        self.settings.write_text(
            json.dumps({"model": "opus", "outputStyle": "CopyDesk medium"}),
            encoding="utf-8",
        )
        styles = self.home / ".claude" / "output-styles"
        styles.mkdir(parents=True, exist_ok=True)
        for level in instructions.VERBOSITY_LEVELS:
            (styles / f"copydesk-{level}.md").write_text("retired\n", encoding="utf-8")

    # The inherited cases start from a key this class replaces.
    def test_enter_accepts_and_sets_the_key(self) -> None:
        self.skipTest("covered by the base fixture, which has no retired key")

    def test_escape_declines_and_leaves_the_key_alone(self) -> None:
        self.skipTest("covered by the base fixture, which has no retired key")

    def test_the_question_is_never_asked(self) -> None:
        printed = self._run(b"\x1b")
        self.assertNotIn(wizard.COPY["active_style"], printed)
        document = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(document["outputStyle"], "CopyDesk")
        self.assertEqual(document["model"], "opus")
        styles = self.home / ".claude" / "output-styles"
        self.assertEqual(
            sorted(path.name for path in styles.glob("*.md")), ["copydesk.md"]
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
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
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


class InstructionAudienceTests(unittest.TestCase):
    """Which channels reach which harness is decided per real file.

    Claude Code used to receive no instruction file at all, and every other
    harness received a block missing chat and the floor clauses.
    """

    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def _plan(self, tools: list[str]) -> object:
        return wizard._build_plan(
            self.home,
            self.home / "config" / "copydesk" / "config.json",
            '{"version": 1}',
            tools,
            config.resolve(ROOT / "rules"),
            {},
            write_config=False,
        )

    @staticmethod
    def _instruction_writes(plan: object) -> list:
        return [w for w in plan.writes if w.path.name in ("AGENTS.md", "CLAUDE.md")]

    def _write_all_channels_config(self, agents: list[str]) -> None:
        path = self.home / "config" / "copydesk" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 1,
            "agents": agents,
            "channels": {
                "chat": {"enabled": True},
                "documents": {"enabled": True},
                "commits": {"enabled": True},
                "reviews": {"enabled": True},
            },
        }), encoding="utf-8")

    def _resolved(self, enabled: list[str]) -> object:
        """A resolved config where exactly the named channels are on."""
        path = self.home / "config.json"
        path.write_text(json.dumps({
            "version": 1,
            "channels": {
                name: {"enabled": name in enabled}
                for name in ("chat", "documents", "commits", "reviews")
            },
        }), encoding="utf-8")
        return config.resolve(ROOT / "rules", user_path=path)

    def test_a_chat_only_claude_code_plan_writes_no_instruction_file(self) -> None:
        # Chat reaches Claude Code through the output style, so with the
        # other channels off the block renders empty and setup skips the
        # file. The installs line promises a CLAUDE.md block conditionally
        # because of exactly this plan; this pins the behaviour it words.
        plan = wizard._build_plan(
            self.home,
            self.home / "config" / "copydesk" / "config.json",
            '{"version": 1}',
            ["claude-code"],
            self._resolved(["chat"]),
            {},
            write_config=False,
        )
        self.assertEqual(self._instruction_writes(plan), [])

    def test_documents_joining_makes_the_claude_code_block_exist(self) -> None:
        # The control for the skip above: a block with something to say
        # produces the write, so the skip tracks emptiness, not the harness.
        plan = wizard._build_plan(
            self.home,
            self.home / "config" / "copydesk" / "config.json",
            '{"version": 1}',
            ["claude-code"],
            self._resolved(["chat", "documents"]),
            {},
            write_config=False,
        )
        self.assertEqual([w.path.name for w in self._instruction_writes(plan)], ["CLAUDE.md"])

    def _cli(self, command: str, *args: str) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), command, *args],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )

    def test_setup_for_claude_code_alone_writes_its_instruction_file(self) -> None:
        (self.home / ".claude").mkdir()
        self._write_all_channels_config(["claude-code"])
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("problem before the solution", body)
        self.assertIn("72 characters", body)
        self.assertIn("name the file and line", body)

    def test_claude_code_s_file_carries_no_chat_line(self) -> None:
        # Chat stays in the output style, where Claude Code already reads it.
        (self.home / ".claude").mkdir()
        self._write_all_channels_config(["claude-code"])
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.home / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertNotIn("answer first", body.lower())
        self.assertNotIn(instructions.style_line("chat", "plain").lower(), body.lower())

    def test_setup_for_grok_writes_chat_and_the_floor_into_its_block(self) -> None:
        (self.home / ".grok").mkdir()
        self._write_all_channels_config(["grok"])
        result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        body = (self.home / ".grok" / "AGENTS.md").read_text(encoding="utf-8").lower()
        self.assertIn("answer first", body)
        self.assertIn("closing block appears only when", body)
        self.assertIn("say a thing once", body)
        self.assertIn(instructions.style_line("chat", "plain").lower(), body)
        self.assertIn("problem before the solution", body)

    def test_a_file_two_harnesses_share_gets_one_write_carrying_chat(self) -> None:
        claude_dir = self.home / ".claude"
        codex_dir = self.home / ".codex"
        claude_dir.mkdir()
        codex_dir.mkdir()
        real = claude_dir / "CLAUDE.md"
        (codex_dir / "AGENTS.md").symlink_to(real)
        writes = self._instruction_writes(self._plan(["claude-code", "codex"]))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].path, real.resolve())
        body = writes[0].content.lower()
        self.assertIn("answer first", body)
        self.assertIn("problem before the solution", body)

    def test_without_the_symlink_each_harness_gets_its_own_block(self) -> None:
        # The control for the shared-file test above: separate files keep
        # chat out of the copy only Claude Code reads.
        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        writes = self._instruction_writes(self._plan(["claude-code", "codex"]))
        self.assertEqual(len(writes), 2)
        by_file = {w.path.name: w for w in writes}
        self.assertNotIn("answer first", by_file["CLAUDE.md"].content.lower())
        self.assertIn("answer first", by_file["AGENTS.md"].content.lower())

    def test_render_agents_block_has_exactly_one_caller_under_lib(self) -> None:
        # Two assemblies of one block is how the two copies came to disagree.
        counts = {}
        for py in sorted((ROOT / "lib").glob("*.py")):
            found = len(re.findall(r"\brender_agents_block\s*\(", py.read_text()))
            if found:
                counts[py.name] = found
        self.assertEqual(counts, {"instructions.py": 1, "wizard.py": 1})

    def test_the_wizard_no_longer_renders_channel_parts_inline(self) -> None:
        source = (ROOT / "lib" / "wizard.py").read_text()
        for part in ("render_documents(", "render_commits(", "render_reviews("):
            self.assertNotIn(part, source)

    def test_uninstall_takes_back_claude_code_s_instruction_file(self) -> None:
        (self.home / ".claude").mkdir()
        self._write_all_channels_config(["claude-code"])
        setup_result = self._cli("setup", "--repair", "--yes")
        self.assertEqual(setup_result.returncode, 0, setup_result.stderr)
        claude_md = self.home / ".claude" / "CLAUDE.md"
        text = claude_md.read_text(encoding="utf-8")
        self.assertIn("<!-- copydesk:start -->", text)
        claude_md.write_text(text + "\n# Added after setup\n", encoding="utf-8")
        uninstall_result = self._cli("uninstall", "--yes")
        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
        body = claude_md.read_text(encoding="utf-8")
        self.assertNotIn("copydesk:start", body)
        self.assertIn("# Added after setup", body)


class GitIsNotAnAIToolTests(unittest.TestCase):
    """Git has its own question.

    It was an entry in the AI-tools list, which mixed two things: seven
    entries write into the home directory and configure an assistant, and
    the eighth writes a hook into whichever repository you happen to be in.
    """

    def test_the_registry_still_carries_git(self) -> None:
        # The control. If git left the registry the test below would pass by
        # accident, and the commit-msg hook would have no adapter at all.
        self.assertIn("git", adapters.REGISTRY)
        self.assertEqual(adapters.REGISTRY["git"].label, "Git commit messages")

    def test_the_tools_question_asks_about_ai_tools_only(self) -> None:
        # The wizard builds its options from the registry minus git. Asserting
        # on the copy would not catch a list built the old way, so this walks
        # the source for the exclusion the flow depends on.
        source = (ROOT / "lib" / "wizard.py").read_text()
        self.assertIn('name != "git"', source)

    def test_the_git_question_says_where_it_writes(self) -> None:
        # A user reading it should know the hook goes into this repository
        # rather than into their home directory like everything else.
        self.assertIn("repository", wizard.COPY["git"].lower())
        self.assertIn("commit-msg hook", wizard.COPY["git_yes"])
        self.assertIn("home directory", wizard.COPY["git_no_because"])


if __name__ == "__main__":
    unittest.main()
