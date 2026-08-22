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
        (styles_dir / "copydesk-low.md").write_text(
            "---\nname: CopyDesk low\n---\n\n<!-- copydesk-build:000000000000 -->\nbody\n",
            encoding="utf-8",
        )
        out = self._doctor().stdout
        self.assertIn("copydesk-low.md", out)
        self.assertIn("out of date", out.lower())
        self.assertIn("copydesk setup --repair", out)

    def _fresh_body(self, level: str) -> str:
        """The body the generator would stamp, under this test's config home."""
        previous_config = os.environ.get("XDG_CONFIG_HOME")
        previous_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "config")
        os.environ["XDG_STATE_HOME"] = str(self.home / "state")
        try:
            return instructions.render_output_style_body(linter.user_layer(), level)
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
        # of a freshly rendered body, which is what the generator stamps and
        # what doctor must compare against.
        styles_dir = self.home / ".claude" / "output-styles"
        styles_dir.mkdir(parents=True)
        fresh = self._fresh_body("low")
        (styles_dir / "copydesk-low.md").write_text(
            "---\nname: CopyDesk low\n---\n\n"
            f"<!-- copydesk-build:{instructions.fingerprint(fresh)} -->\n\n{fresh}\n",
            encoding="utf-8",
        )
        self.assertNotIn("out of date", self._doctor().stdout.lower())

    def test_a_generated_output_style_is_not_reported_as_stale(self) -> None:
        """The shipped file carries the generator's own stamp, so a fresh
        install must read as current rather than as drift."""
        styles_dir = self.home / ".claude" / "output-styles"
        styles_dir.mkdir(parents=True)
        for level in ("low", "medium", "high"):
            shutil.copy(ROOT / "output-styles" / f"copydesk-{level}.md",
                        styles_dir / f"copydesk-{level}.md")
        self.assertNotIn("out of date", self._doctor().stdout.lower())
