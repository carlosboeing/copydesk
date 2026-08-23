"""Tests for copydesk doctor."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import json  # noqa: E402
import instructions
import linter


class DoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        # The installation root is separate from the project directory, and
        # doctor reads both. Without an isolated home it reports the
        # operator's real install, and the drift assertions become a lottery.
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.root / "copydesk.config.json").write_text(
            '{"version": 1, "channels": {"documents": {"style": "engineer"}},'
            ' "paths": {"warn": ["CHANGELOG.md"]}}', encoding="utf-8"
        )
        (self.root / "doc.md").write_text("text\n", encoding="utf-8")

    def _doctor(self, *args) -> subprocess.CompletedProcess:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "doctor", *args],
            cwd=self.root, capture_output=True, text=True, env=env,
        )

    def test_doctor_on_a_file_attributes_each_value_to_a_layer(self) -> None:
        out = self._doctor("doc.md").stdout
        self.assertIn("documents", out)
        self.assertIn("engineer", out)
        self.assertIn("copydesk.config.json", out)

    def test_bare_doctor_reads_the_config_back_as_sentences(self) -> None:
        out = self._doctor().stdout
        self.assertIn("CHANGELOG.md warns", out)

    def test_rules_lists_every_rule_and_guidance_id(self) -> None:
        out = self._doctor("--rules").stdout
        for rule_id in ("sentence-length", "banned-word", "unglossed-term"):
            self.assertIn(rule_id, out)
        for name in ("recommendations", "verification"):
            self.assertIn(name, out)

    def test_a_personal_key_in_a_project_file_is_named_with_its_fix(self) -> None:
        (self.root / "copydesk.config.json").write_text(
            '{"version": 1, "agents": ["codex"]}', encoding="utf-8"
        )
        out = self._doctor("doc.md").stdout
        self.assertIn("agents", out)
        self.assertIn("personal key", out.lower())

    def test_a_prevention_only_channel_says_so(self) -> None:
        out = self._doctor().stdout
        self.assertIn("prevention-only", out.lower())

    def test_a_stale_fingerprint_is_reported_with_its_fix(self) -> None:
        # Install a style carrying a marker that cannot match, so the
        # diagnostic can only come from the fingerprint check. Asserting on
        # the repair command alone would pass on any other drift warning.
        styles_dir = self.home / ".claude" / "output-styles"
        styles_dir.mkdir(parents=True)
        (styles_dir / "copydesk.md").write_text(
            "---\nname: CopyDesk\n---\n\n<!-- copydesk-build:000000000000 -->\nbody\n",
            encoding="utf-8",
        )
        out = self._doctor().stdout
        self.assertIn("copydesk.md", out)
        self.assertIn("out of date", out.lower())
        self.assertIn("copydesk setup --repair", out)

    def test_a_retired_style_file_left_behind_is_reported_stale(self) -> None:
        # An upgrade that has not run repair yet leaves copydesk-low.md and
        # its siblings behind. A check keyed to the new file's name would go
        # quiet about them; doctor must keep naming them until they are gone.
        styles_dir = self.home / ".claude" / "output-styles"
        styles_dir.mkdir(parents=True)
        (styles_dir / "copydesk-high.md").write_text(
            "---\nname: CopyDesk high\n---\n\n<!-- copydesk-build:000000000000 -->\nbody\n",
            encoding="utf-8",
        )
        out = self._doctor().stdout
        self.assertIn("copydesk-high.md", out)
        self.assertIn("out of date", out.lower())

    def _fresh_install(self) -> str:
        """The whole file setup writes today, under this test's config home."""
        previous_config = os.environ.get("XDG_CONFIG_HOME")
        previous_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "config")
        os.environ["XDG_STATE_HOME"] = str(self.home / "state")
        try:
            layer = linter.user_layer()
            return instructions.render_output_style(
                layer, writer=instructions.SETUP_WRITER
            )
        finally:
            if previous_config is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous_config
            if previous_state is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = previous_state

    def test_a_current_fingerprint_reports_no_drift(self) -> None:
        # The control. Without it the assertion above cannot tell a working
        # check from one that always warns. The marker holds the fingerprint
        # of a freshly rendered file, which is what setup stamps and what
        # doctor must compare against.
        styles_dir = self.home / ".claude" / "output-styles"
        styles_dir.mkdir(parents=True)
        (styles_dir / "copydesk.md").write_text(
            self._fresh_install(), encoding="utf-8"
        )
        self.assertNotIn("out of date", self._doctor().stdout.lower())

    def test_styles_the_setup_wizard_wrote_are_not_reported_as_stale(self) -> None:
        """The check re-renders through the writer that produces installed
        copies. The one installed file must read as current rather than as
        drift."""
        styles_dir = self.home / ".claude" / "output-styles"
        styles_dir.mkdir(parents=True)
        (styles_dir / "copydesk.md").write_text(
            self._fresh_install(), encoding="utf-8"
        )
        self.assertNotIn("out of date", self._doctor().stdout.lower())


class ActiveStyleReportTests(unittest.TestCase):
    """Doctor reports which style is active, not only which are installed."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.root / "doc.md").write_text("text\n", encoding="utf-8")

    def _doctor(self) -> str:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "doctor"],
            cwd=self.root, capture_output=True, text=True, env=env,
        ).stdout

    def _install_style(self) -> None:
        styles_dir = self.home / ".claude" / "output-styles"
        styles_dir.mkdir(parents=True)
        previous_config = os.environ.get("XDG_CONFIG_HOME")
        previous_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "config")
        os.environ["XDG_STATE_HOME"] = str(self.home / "state")
        try:
            fresh = instructions.render_output_style(
                linter.user_layer(), writer=instructions.SETUP_WRITER
            )
        finally:
            if previous_config is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous_config
            if previous_state is None:
                os.environ.pop("XDG_STATE_HOME", None)
            else:
                os.environ["XDG_STATE_HOME"] = previous_state
        (styles_dir / "copydesk.md").write_text(fresh, encoding="utf-8")

    def test_the_active_style_is_named_when_it_is_not_copydesk_s(self) -> None:
        # The complaint behind the report: a user can have CopyDesk
        # installed while their own style is the one in effect, and
        # "installed" reads as "active". Naming both ends the ambiguity.
        self._install_style()
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"outputStyle": "Plain English"}), encoding="utf-8")
        out = self._doctor()
        self.assertRegex(out, r"active\s+Plain English")
        self.assertIn("copydesk.md", out)

    def test_CopyDesk_active_is_named_too(self) -> None:
        self._install_style()
        settings = self.home / ".claude" / "settings.json"
        settings.write_text(json.dumps({"outputStyle": "CopyDesk"}), encoding="utf-8")
        out = self._doctor()
        self.assertRegex(out, r"active\s+CopyDesk")
        self.assertIn("copydesk.md", out)

    def test_no_style_set_is_reported_as_none(self) -> None:
        # The control for the tests above: without it they could pass on a
        # report that prints whatever string was last seen.
        self.assertRegex(self._doctor(), r"active\s+none")


class SharedInstructionFileTests(unittest.TestCase):
    """Doctor reports instruction targets that resolve to one real file.

    Setups that symlink every per-harness name at one canonical file make
    several adapters share one block. Without the report, a reader cannot
    tell why their CLAUDE.md carries documents when Claude Code's own file
    should not.
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        (self.root / "doc.md").write_text("text\n", encoding="utf-8")

    def _doctor(self) -> str:
        env = dict(
            os.environ,
            COPYDESK_HOME=str(self.home),
            XDG_CONFIG_HOME=str(self.home / "config"),
            XDG_STATE_HOME=str(self.home / "state"),
            PATH=str(self.home / "nothing"),
        )
        env.pop("COPYDESK_STATE_DIR", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "doctor"],
            cwd=self.root, capture_output=True, text=True, env=env,
        ).stdout

    def _real_file(self) -> Path:
        claude_dir = self.home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        return claude_dir / "CLAUDE.md"

    def test_two_harnesses_on_one_file_are_named_with_the_real_path(self) -> None:
        real = self._real_file()
        real.write_text("# mine\n", encoding="utf-8")
        codex = self.home / ".codex" / "AGENTS.md"
        codex.parent.mkdir(parents=True)
        codex.symlink_to(real)
        out = self._doctor()
        self.assertIn(str(real), out)
        self.assertIn("Claude Code", out)
        self.assertIn("Codex", out)

    def test_the_shared_block_s_chat_duplication_is_measured(self) -> None:
        # Chat joins the shared block because another harness needs it, so
        # Claude Code loads those rules from the output style AND this file.
        # The word count makes the cost visible instead of inferred.
        real = self._real_file()
        real.write_text("# mine\n", encoding="utf-8")
        agents_home = self.home / ".agents" / "AGENTS.md"
        agents_home.parent.mkdir(parents=True)
        agents_home.symlink_to(real)
        out = self._doctor()
        self.assertIn("twice", out.lower())
        self.assertRegex(out, r"\(\d+ words\)")

    def test_separate_files_are_listed_once_without_a_duplication_line(self) -> None:
        # The control: no symlinks, no sharing, no warning.
        claude_dir = self.home / ".claude"
        claude_dir.mkdir(parents=True)
        (claude_dir / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
        agents_home = self.home / ".agents" / "AGENTS.md"
        agents_home.parent.mkdir(parents=True)
        agents_home.write_text("# mine too\n", encoding="utf-8")
        out = self._doctor()
        self.assertIn(str(claude_dir / "CLAUDE.md"), out)
        self.assertIn(str(agents_home), out)
        self.assertNotIn("twice", out.lower())

    def test_an_empty_home_names_no_instruction_files(self) -> None:
        self.assertEqual(self._doctor().count("AGENTS.md"), 0)
        self.assertNotIn("CLAUDE.md", self._doctor())
