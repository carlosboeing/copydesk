"""Test harness adapters and detection."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import adapters  # noqa: E402


class AdapterTests(unittest.TestCase):
    def test_a_shared_home_alone_detects_neither(self) -> None:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home)
        (home / ".agents").mkdir()
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(adapters.detect("kimi", home))
            self.assertFalse(adapters.detect("antigravity", home))

    def test_the_executable_distinguishes_a_shared_home(self) -> None:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home)
        (home / ".agents").mkdir()
        with mock.patch("shutil.which", side_effect=lambda n: "/usr/bin/kimi" if n == "kimi" else None):
            self.assertTrue(adapters.detect("kimi", home))
            self.assertFalse(adapters.detect("antigravity", home))

    def test_an_unshared_home_still_detects(self) -> None:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home)
        (home / ".claude").mkdir()
        with mock.patch("shutil.which", return_value=None):
            self.assertTrue(adapters.detect("claude-code", home))

    def test_every_adapter_has_an_executable_name(self) -> None:
        for name in adapters.REGISTRY:
            if name != "git":
                self.assertIn(name, adapters.EXECUTABLES, name)

    def test_every_adapter_has_a_label_and_installs(self) -> None:
        for name, adapter in adapters.REGISTRY.items():
            self.assertEqual(adapter.name, name)
            self.assertTrue(adapter.label)
            self.assertTrue(adapter.installs)
            self.assertTrue(adapter.home)

    def test_every_harness_names_its_instruction_file_and_git_names_none(self) -> None:
        expected = {
            "claude-code": "CLAUDE.md",
            "codex": "AGENTS.md",
            "cursor": "AGENTS.md",
            "kimi": "AGENTS.md",
            "opencode": "AGENTS.md",
            "antigravity": "AGENTS.md",
            "grok": "AGENTS.md",
            "git": "",
        }
        self.assertEqual(set(adapters.REGISTRY), set(expected))
        for name, adapter in adapters.REGISTRY.items():
            self.assertEqual(adapter.instruction_file, expected[name], name)


if __name__ == "__main__":
    unittest.main()
