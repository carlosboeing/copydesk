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

    def test_the_claude_code_installs_string_names_the_instruction_file(self) -> None:
        # The wizard prints this line beside each harness while the user
        # picks what to configure, so it must name everything setup writes.
        adapter = adapters.REGISTRY["claude-code"]
        self.assertIn(adapter.instruction_file, adapter.installs)

    def test_a_gate_verified_adapter_says_so_in_its_installs_line(self) -> None:
        """`gate_verified` has no reader outside this test. The docstring
        presents it as what governs a gate claim, and the user-visible half
        is the `installs` string, so the two are tied here rather than left
        free to drift."""
        for name, adapter in adapters.REGISTRY.items():
            if not adapter.gate_verified:
                continue
            self.assertTrue(
                "gate" in adapter.installs or "hook" in adapter.installs,
                f"{name} claims a verified gate and names nothing that enforces one",
            )

    def test_an_unverified_adapter_promises_no_gate(self) -> None:
        """The `git` adapter installs a commit-msg hook and is not gate
        verified, so the word tested here is `gate`, not `hook`."""
        for name, adapter in adapters.REGISTRY.items():
            if adapter.gate_verified:
                continue
            self.assertNotIn(
                "gate", adapter.installs,
                f"{name} promises a gate its transcript has not proved",
            )


if __name__ == "__main__":
    unittest.main()
