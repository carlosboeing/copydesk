"""The setup plan and uninstall cover the Grok and OpenCode gates."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import wizard  # noqa: E402


class HarnessGatePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def _plan(self, tools: list[str]) -> wizard.apply.Plan:
        return wizard._build_plan(
            home=self.home,
            config_path=self.home / "config" / "copydesk" / "config.json",
            config_body="{}",
            selected_tools=tools,
            resolved_config={},
            settings_doc={},
        )

    def _writes(self, plan: wizard.apply.Plan) -> dict[Path, str]:
        return {write.path: write.content for write in plan.writes}

    def test_the_grok_plan_bundles_the_gate_and_registers_it(self) -> None:
        writes = self._writes(self._plan(["grok"]))
        gate = self.home / ".grok" / "hooks" / "copydesk" / "grok-gate.py"
        linter = self.home / ".grok" / "hooks" / "copydesk" / "linter.py"
        preset = self.home / ".grok" / "hooks" / "copydesk" / "rules" / "plain.json"
        registration = self.home / ".grok" / "hooks" / "copydesk.json"
        for path in (gate, linter, preset, registration):
            self.assertIn(path, writes, path)
        registered = json.loads(writes[registration])
        group = registered["hooks"]["PreToolUse"][0]
        self.assertEqual(group["matcher"], "Write|Edit")
        handler = group["hooks"][0]
        self.assertEqual(handler["command"], str(gate))
        self.assertGreaterEqual(handler["timeout"], 10)

    def test_the_grok_gate_source_carries_the_executable_bit(self) -> None:
        # The installed copy takes its mode from setup's chmod alone:
        # apply.execute writes every planned file with Path.write_text
        # (lib/apply.py), which creates it at the process umask and never
        # copies the source mode. This bit matters when the gate runs
        # straight from a checkout.
        source = ROOT / "hooks" / "grok-gate.py"
        self.assertTrue(source.exists())
        self.assertTrue(os.access(source, os.X_OK))

    def test_setup_installs_a_working_grok_gate_end_to_end(self) -> None:
        (self.home / ".grok").mkdir()
        env = dict(os.environ)
        env.update({
            "COPYDESK_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_STATE_HOME": str(self.home / "state"),
            "PATH": str(self.home / "nothing"),
        })
        env.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        gate = self.home / ".grok" / "hooks" / "copydesk" / "grok-gate.py"
        registration = self.home / ".grok" / "hooks" / "copydesk.json"
        self.assertTrue(gate.is_file(), result.stdout)
        self.assertTrue(os.access(gate, os.X_OK))
        self.assertTrue(registration.is_file())
        registered = json.loads(registration.read_text(encoding="utf-8"))
        self.assertEqual(registered["hooks"]["PreToolUse"][0]["matcher"], "Write|Edit")
        self.assertTrue((self.home / ".grok" / "hooks" / "copydesk" / "rules" / "plain.json").is_file())

    def test_setup_installs_the_opencode_plugin_end_to_end(self) -> None:
        """OpenCode was covered at plan level only. The last confirmed defect
        on this branch was a root mismatch between the plan and the file the
        harness reads, which no plan-level test can see."""
        xdg = self.home / "config"
        (xdg / "opencode").mkdir(parents=True)
        env = dict(os.environ)
        env.update({
            "COPYDESK_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(xdg),
            "XDG_STATE_HOME": str(self.home / "state"),
            "PATH": str(self.home / "nothing"),
        })
        env.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugin = xdg / "opencode" / "plugins" / "copydesk-gate.js"
        linter = xdg / "opencode" / "copydesk" / "linter.py"
        preset = xdg / "opencode" / "copydesk" / "rules" / "plain.json"
        block = xdg / "opencode" / "AGENTS.md"
        for path in (plugin, linter, preset, block):
            self.assertTrue(path.is_file(), f"{path} missing\n{result.stdout}")
        # The root the plan chose is the root on disk, and nothing landed in
        # the literal ~/.config the block used to expand to.
        self.assertFalse((self.home / ".config" / "opencode").exists(), result.stdout)

    def test_setup_makes_the_grok_gate_executable_with_claude_selected(self) -> None:
        (self.home / ".grok").mkdir()
        (self.home / ".claude").mkdir()
        env = dict(os.environ)
        env.update({
            "COPYDESK_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_STATE_HOME": str(self.home / "state"),
            "PATH": str(self.home / "nothing"),
        })
        env.pop("COPYDESK_STATE_DIR", None)
        result = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "copydesk"), "setup", "--defaults", "--yes"],
            cwd=self.home, capture_output=True, text=True, env=env, input="",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        gate = self.home / ".grok" / "hooks" / "copydesk" / "grok-gate.py"
        self.assertTrue(os.access(gate, os.X_OK), result.stdout)

    def test_the_opencode_plan_installs_one_plugin_and_a_bundle(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home / "config")}):
            writes = self._writes(self._plan(["opencode"]))
        plugin = self.home / "config" / "opencode" / "plugins" / "copydesk-gate.js"
        linter = self.home / "config" / "opencode" / "copydesk" / "linter.py"
        for path in (plugin, linter):
            self.assertIn(path, writes, path)
        plugin_names = [p.name for p in writes if p.parent.name == "plugins"]
        self.assertEqual(plugin_names, ["copydesk-gate.js"])

    def test_the_opencode_block_lands_beside_its_plugin_under_xdg(self) -> None:
        """The gate resolved the OpenCode root through XDG_CONFIG_HOME while
        the instruction file expanded the literal ~/.config, so on a machine
        that sets the variable the block landed in a file OpenCode never
        reads and uninstall looked for it in the same unread place."""
        xdg = self.home / "elsewhere"
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg)}):
            writes = self._writes(self._plan(["opencode"]))
        plugin = xdg / "opencode" / "plugins" / "copydesk-gate.js"
        # Instruction targets are coalesced through Path.resolve, so the
        # comparison resolves too — on macOS /var is a link to /private/var.
        block = (xdg / "opencode" / "AGENTS.md").resolve()
        self.assertIn(plugin, writes)
        self.assertIn(block, writes)
        stale = (self.home / ".config" / "opencode" / "AGENTS.md").resolve()
        self.assertNotIn(stale, writes)

    def test_an_empty_xdg_value_falls_back_to_the_default_root(self) -> None:
        """A shell that exports XDG_CONFIG_HOME empty is common. The
        two-argument environ.get returns "" for it, and Path("") is Path("."),
        which would resolve every OpenCode path against the working
        directory."""
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": ""}):
            resolved = wizard._adapter_home(wizard.adapters.REGISTRY["opencode"], self.home)
        self.assertEqual(resolved, self.home / ".config" / "opencode")
        self.assertTrue(resolved.is_absolute())

    def test_a_literal_harness_home_ignores_xdg(self) -> None:
        """~/.claude and ~/.grok are not XDG base directories, and the tools
        that own them do not relocate on the variable."""
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home / "elsewhere")}):
            self.assertEqual(
                wizard._adapter_home(wizard.adapters.REGISTRY["grok"], self.home),
                self.home / ".grok",
            )
            self.assertEqual(
                wizard._adapter_home(wizard.adapters.REGISTRY["claude-code"], self.home),
                self.home / ".claude",
            )

    def test_an_unrelated_tool_plans_neither_gate(self) -> None:
        writes = self._writes(self._plan(["cursor"]))
        for path in writes:
            self.assertNotIn(".grok", str(path))
            self.assertNotIn("copydesk-gate.js", path.name)


class HarnessGateUninstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)

    def test_uninstall_lists_the_grok_and_opencode_files(self) -> None:
        grok_dir = self.home / ".grok" / "hooks" / "copydesk"
        grok_dir.mkdir(parents=True)
        (grok_dir / "grok-gate.py").write_text("", encoding="utf-8")
        (self.home / ".grok" / "hooks" / "copydesk.json").write_text("{}", encoding="utf-8")
        oc_root = self.home / "config" / "opencode"
        (oc_root / "plugins").mkdir(parents=True)
        (oc_root / "plugins" / "copydesk-gate.js").write_text("", encoding="utf-8")
        (oc_root / "copydesk").mkdir()
        (oc_root / "copydesk" / "linter.py").write_text("", encoding="utf-8")

        out = io.StringIO()
        with mock.patch.dict(os.environ, {
            "COPYDESK_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
        }):
            code = wizard.run_uninstall(["--dry-run"], stdin=io.StringIO(""), stdout=out)
        self.assertEqual(code, 0)
        listing = out.getvalue()
        for named in (
            grok_dir,
            self.home / ".grok" / "hooks" / "copydesk.json",
            oc_root / "plugins" / "copydesk-gate.js",
            oc_root / "copydesk",
        ):
            self.assertIn(str(named), listing)


if __name__ == "__main__":
    unittest.main()
